"""Lamoda connections: independent encrypted accounts, explicitly assigned to a brand/method."""
from __future__ import annotations

import hashlib
import json
import uuid
from urllib.parse import parse_qs, urlparse

from psycopg2.extras import RealDictCursor

KEYS = ('lamoda_client_id', 'lamoda_client_secret', 'lamoda_seller_id')
CLIENTS = {'toptop', 'lera_nena'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS public.bi_lamoda_accounts (
 account_id uuid PRIMARY KEY,
 client_key text REFERENCES public.bi_client_registry(client_key),
 origin_brand text REFERENCES public.bi_client_registry(client_key),
 fulfillment text CHECK (fulfillment IN ('FBO','FBS')),
 label text NOT NULL,
 credential_identity text,
 revision integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (client_key IS NULL OR fulfillment IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_lamoda_active_credentials
 ON public.bi_lamoda_accounts(credential_identity) WHERE client_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.bi_lamoda_account_credentials (
 account_id uuid NOT NULL REFERENCES public.bi_lamoda_accounts(account_id),
 credential_key text NOT NULL,
 encrypted_value bytea NOT NULL,
 fingerprint text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(account_id, credential_key)
);
REVOKE ALL ON public.bi_lamoda_account_credentials,public.bi_lamoda_accounts FROM PUBLIC,pulse_reader;
"""


def migrate(conn):
    """Move ciphertext in one transaction; never decrypt legacy credentials here."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('lamoda-account-migration-v1'))")
        cur.execute(SCHEMA)
        for brand in sorted(CLIENTS):
            account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'trend:lamoda:legacy:' + brand))
            cur.execute("""INSERT INTO public.bi_lamoda_accounts(account_id,label)
                SELECT %s,%s WHERE EXISTS(SELECT 1 FROM public.bi_client_credentials
                  WHERE client_key=%s AND credential_key=ANY(%s))
                ON CONFLICT(account_id) DO NOTHING""",
                (account_id, 'Прежнее подключение ' + brand + ' — назначьте бренд и FBO/FBS', brand, list(KEYS)))
            cur.execute("""INSERT INTO public.bi_lamoda_account_credentials
                (account_id,credential_key,encrypted_value,fingerprint,updated_at)
                SELECT %s,credential_key,encrypted_value,fingerprint,updated_at
                FROM public.bi_client_credentials WHERE client_key=%s AND credential_key=ANY(%s)
                ON CONFLICT(account_id,credential_key) DO NOTHING""", (account_id, brand, list(KEYS)))
            cur.execute("DELETE FROM public.bi_client_credentials WHERE client_key=%s AND credential_key=ANY(%s)", (brand, list(KEYS)))
            cur.execute("DELETE FROM public.bi_client_marketplace_accounts WHERE client_key=%s AND marketplace='lamoda'", (brand,))
    conn.commit()


def validate(payload):
    brand = str(payload.get('client') or '')
    method = str(payload.get('fulfillment') or '').upper()
    label = str(payload.get('label') or '').strip()
    if brand not in CLIENTS:
        raise ValueError('Выберите бренд')
    if method not in {'FBO', 'FBS'}:
        raise ValueError('Выберите FBO или FBS')
    if not label or len(label) > 120:
        raise ValueError('Название аккаунта: от 1 до 120 символов')
    account_id = str(uuid.UUID(str(payload['account_id']))) if payload.get('account_id') else str(uuid.uuid4())
    raw = payload.get('credentials') or {}
    if not isinstance(raw, dict):
        raise ValueError('Некорректные реквизиты')
    credentials = {key: str(raw.get(key) or '').strip() for key in KEYS}
    if any(len(value) > 8192 for value in credentials.values()):
        raise ValueError('Слишком длинные реквизиты')
    return brand, method, label, account_id, credentials


def list_accounts(conn, brand, include_unassigned=False):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT a.account_id::text,a.client_key,a.fulfillment,a.label,a.revision,
            count(c.credential_key)=3 AS credentials_saved
            FROM public.bi_lamoda_accounts a
            LEFT JOIN public.bi_lamoda_account_credentials c USING(account_id)
            WHERE a.client_key=%s OR (%s AND a.client_key IS NULL)
            GROUP BY a.account_id ORDER BY a.client_key NULLS LAST,a.fulfillment,a.label""",
            (brand, include_unassigned))
        return [dict(row) for row in cur.fetchall()]


def read_credentials(conn, account_id, master_key):
    with conn.cursor() as cur:
        cur.execute("""SELECT credential_key,pgp_sym_decrypt(encrypted_value,%s)
            FROM public.bi_lamoda_account_credentials WHERE account_id=%s""", (master_key, account_id))
        rows = cur.fetchall()
    return {row['credential_key']: row['pgp_sym_decrypt'] for row in rows} if rows and isinstance(rows[0], dict) else dict(rows)


def save_account(app, payload, *, allow_unassigned):
    brand, method, label, account_id, credentials = validate(payload)
    with app.client_registry_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT * FROM public.bi_lamoda_accounts WHERE account_id=%s FOR UPDATE', (account_id,))
            old = cur.fetchone()
            if payload.get('account_id') and not old:
                raise ValueError('Аккаунт не найден')
            if old:
                if old['client_key'] not in (None, brand) or (old['client_key'] is None and not allow_unassigned):
                    raise ValueError('Нет доступа к аккаунту')
                if int(payload.get('revision') or 0) != old['revision']:
                    raise ValueError('Аккаунт изменён. Обновите страницу')
                if old.get('origin_brand') and old['origin_brand'] != brand:
                    raise ValueError('Этот аккаунт содержит историю другого бренда. Добавьте новый аккаунт')
                if old['fulfillment'] and old['fulfillment'] != method:
                    raise ValueError('Для другого метода доставки добавьте отдельный аккаунт')
                saved = read_credentials(conn, account_id, app.client_credentials_master_key())
                credentials = {key: credentials[key] or saved.get(key, '') for key in KEYS}
            if not all(credentials.values()):
                raise ValueError('Заполните Client ID, Client Secret и Seller ID')
            identity = hashlib.sha256(json.dumps([credentials[k] for k in KEYS]).encode()).hexdigest()
            cur.execute('SELECT account_id FROM public.bi_lamoda_accounts WHERE credential_identity=%s AND client_key IS NOT NULL AND account_id<>%s', (identity, account_id))
            if cur.fetchone():
                raise ValueError('Этот доступ уже подключён. Для другого аккаунта нужен отдельный токен')
            if old and old['client_key'] and old['credential_identity'] != identity:
                # Rotating a secret is safe; changing the seller would mix historical data.
                if any(credentials[k] != saved.get(k) for k in ('lamoda_client_id','lamoda_seller_id')):
                    raise ValueError('Для другого продавца добавьте новый аккаунт')
            from scripts.sync_lamoda import verify_credentials
            try:
                verify_credentials(*(credentials[k] for k in KEYS))
            except Exception:
                raise ValueError('Lamoda не подтвердила доступ. Проверьте реквизиты и повторите') from None
            cur.execute("""INSERT INTO public.bi_lamoda_accounts(account_id,client_key,fulfillment,label,credential_identity,origin_brand)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(account_id) DO UPDATE SET
                client_key=excluded.client_key,fulfillment=excluded.fulfillment,label=excluded.label,origin_brand=excluded.origin_brand,
                credential_identity=excluded.credential_identity,revision=bi_lamoda_accounts.revision+1,updated_at=now()""",
                (account_id, brand, method, label, identity, brand))
            master_key = app.client_credentials_master_key()
            if not master_key:
                raise ValueError('Хранилище доступов не настроено')
            for key, value in credentials.items():
                cur.execute("""INSERT INTO public.bi_lamoda_account_credentials(account_id,credential_key,encrypted_value,fingerprint)
                    VALUES(%s,%s,pgp_sym_encrypt(%s,%s,'cipher-algo=aes256'),%s)
                    ON CONFLICT(account_id,credential_key) DO UPDATE SET encrypted_value=excluded.encrypted_value,
                    fingerprint=excluded.fingerprint,updated_at=now()""",
                    (account_id,key,value,master_key,hashlib.sha256(value.encode()).hexdigest()[:10]))
            cur.execute("""UPDATE public.bi_client_registry SET marketplaces=CASE
                WHEN marketplaces ? 'lamoda' THEN marketplaces ELSE marketplaces || '["lamoda"]'::jsonb END,
                updated_at=now() WHERE client_key=%s""", (brand,))
        conn.commit()
    app.hydrate_registered_clients()
    return {'ok': True, 'account_id': account_id}


def detach_account(app, payload):
    brand = str(payload.get('client') or '')
    account_id = str(uuid.UUID(str(payload.get('account_id') or '')))
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE public.bi_lamoda_accounts SET client_key=NULL,
                revision=revision+1,updated_at=now() WHERE account_id=%s AND client_key=%s AND revision=%s""",
                (account_id,brand,int(payload.get('revision') or 0)))
            if cur.rowcount != 1:
                raise ValueError('Аккаунт изменён или недоступен. Обновите страницу')
        conn.commit()
    return {'ok': True}


def handle(app, handler, method):
    parsed = urlparse(handler.path)
    if method == 'POST':
        origin = handler.headers.get('Origin')
        if origin and urlparse(origin).netloc != handler.headers.get('Host'):
            handler.send_json({'ok':False,'error':'Недопустимый источник запроса'},status=403)
            return
    if not handler.dashboard_access_granted():
        handler.send_dashboard_access_required(parsed)
        return
    identity = handler.dashboard_access_identity() or {}
    admin = bool(identity.get('is_admin'))
    if not admin and not set(identity.get('admin_sections') or []).intersection({'client','clientOnboarding'}):
        handler.send_json({'ok':False,'error':'Нет доступа к подключениям'},status=403)
        return
    try:
        payload = handler.read_json_body() if method == 'POST' else {k:v[0] for k,v in parse_qs(parsed.query).items()}
        brand = payload.get('client')
        if brand not in CLIENTS or (not admin and brand not in identity.get('clients', [])):
            handler.send_json({'ok':False,'error':'Нет доступа к бренду'},status=403)
            return
        if method == 'GET':
            with app.client_registry_connection() as conn:
                result = {'ok':True,'accounts':list_accounts(conn,brand,admin)}
        elif payload.get('action') == 'detach':
            result = detach_account(app,payload)
        else:
            result = save_account(app,payload,allow_unassigned=admin)
        handler.send_json(result,headers={'Cache-Control':'no-store'})
    except (ValueError, TypeError) as exc:
        handler.send_json({'ok':False,'error':str(exc)},status=400)
    except Exception:
        # Database errors may embed parameter values. Never send them to the UI/log.
        handler.send_json({'ok':False,'error':'Не удалось сохранить подключение. Обновите страницу и повторите'},status=409)
