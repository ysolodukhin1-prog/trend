"""FF Platform admin connection, bounded full snapshots and background import.

Only the documented read API is used. Tokens stay in the existing encrypted
credential store; stock snapshots are isolated by TREND client and import run.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg2
import requests
from psycopg2.extras import Json, execute_values

API = "https://ff-platform.ru/lk/4/client_api/v1"
ENDPOINT = "/api/admin/ff-platform"
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 200000
MAX_SCOPES = 100
# Verified provider request filter -> returned product type. Packaging is a
# separate stock domain; prodType enum values are NOT request filter values.
STOCK_FILTERS = {'Goods': 'g'}
_LOCK = threading.Lock()
_READY = False


class ImportErrorSafe(ValueError):
    """Only fixed, public messages may leave the importer."""


DDL = """
CREATE TABLE IF NOT EXISTS ff_platform.connections (
 client_key text PRIMARY KEY, settings jsonb NOT NULL DEFAULT '{}',
 checked_at timestamptz, check_status text NOT NULL DEFAULT 'untested',
 check_message text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ff_platform.jobs (
 id uuid PRIMARY KEY,client_key text NOT NULL,status text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),started_at timestamptz,
 finished_at timestamptz,heartbeat_at timestamptz NOT NULL DEFAULT now(),
 request jsonb NOT NULL,result jsonb NOT NULL DEFAULT '{}',
 message text NOT NULL DEFAULT '',pid integer,user_id bigint);
CREATE UNIQUE INDEX IF NOT EXISTS ff_platform_one_active
 ON ff_platform.jobs(client_key) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS ff_platform.scopes (
 batch_id uuid NOT NULL REFERENCES ff_platform.jobs(id),client_key text NOT NULL,
 warehouse_id text NOT NULL,nomenclature_type text NOT NULL,
 observed_at timestamptz NOT NULL,row_count integer NOT NULL,digest text NOT NULL,
 PRIMARY KEY(batch_id,warehouse_id,nomenclature_type));
CREATE TABLE IF NOT EXISTS ff_platform.stocks (
 batch_id uuid NOT NULL,client_key text NOT NULL,warehouse_id text NOT NULL,
 nomenclature_type text NOT NULL,nomenclature_id text NOT NULL,
 client_article text NOT NULL,name text NOT NULL,
 available_qty integer NOT NULL,defect_qty integer NOT NULL,at_work_qty integer NOT NULL,
 PRIMARY KEY(batch_id,warehouse_id,nomenclature_type,nomenclature_id),
 FOREIGN KEY(batch_id,warehouse_id,nomenclature_type)
 REFERENCES ff_platform.scopes(batch_id,warehouse_id,nomenclature_type));
CREATE TABLE IF NOT EXISTS ff_platform.active (
 client_key text NOT NULL,warehouse_id text NOT NULL,nomenclature_type text NOT NULL,
 batch_id uuid NOT NULL,PRIMARY KEY(client_key,warehouse_id,nomenclature_type),
 FOREIGN KEY(batch_id,warehouse_id,nomenclature_type)
 REFERENCES ff_platform.scopes(batch_id,warehouse_id,nomenclature_type));
CREATE OR REPLACE VIEW ff_platform.current_stocks AS
 SELECT s.*,sc.observed_at FROM ff_platform.stocks s
 JOIN ff_platform.active a USING(client_key,warehouse_id,nomenclature_type,batch_id)
 JOIN ff_platform.scopes sc USING(client_key,warehouse_id,nomenclature_type,batch_id);
GRANT USAGE ON SCHEMA ff_platform TO pulse_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ff_platform TO pulse_reader;
"""


def ensure(app):
    global _READY
    with _LOCK:
        if _READY:
            return
        with app.client_registry_connection() as conn:
            app.ensure_service_credentials_schema(conn)
            with conn.cursor() as cur:
                cur.execute(DDL)
        _READY = True


def client_key(app, value):
    key = str(value or "toptop").strip().lower()
    if key not in {"toptop", "lera_nena"} or key not in app.ADMIN_CLIENTS:
        raise ImportErrorSafe("Выберите доступный аккаунт TREND")
    return key


def identifier(value):
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,10}", str(value or "")):
        raise ImportErrorSafe("API вернул неподдерживаемый идентификатор")
    value = int(value)
    if value < 1 or value > 2147483647:
        raise ImportErrorSafe("API вернул неподдерживаемый идентификатор")
    return str(value)


def settings(value):
    warehouses = value.get("warehouse_ids", [])
    types = value.get("nomenclature_types", [])
    if not isinstance(warehouses, list) or not isinstance(types, list):
        raise ImportErrorSafe("Выберите склады и укажите типы номенклатуры")
    warehouses = list(dict.fromkeys(identifier(v) for v in warehouses))
    types = list(dict.fromkeys(str(v).strip() for v in types))
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v) for v in types):
        raise ImportErrorSafe("Типы номенклатуры: буквы, цифры, дефис и подчёркивание; до 64 символов")
    if len(warehouses) > 50 or len(types) > 20 or len(warehouses) * len(types) > MAX_SCOPES:
        raise ImportErrorSafe("Слишком много складов или типов для одного импорта")
    return {"warehouse_ids": warehouses, "nomenclature_types": types}


def collection(value, keys):
    """Accept explicit collection envelopes; reject errors and partial responses."""
    if isinstance(value, list):
        if len(value) > MAX_ROWS:
            raise ImportErrorSafe("Ответ превышает предел загрузки; прежние остатки сохранены")
        return value
    if not isinstance(value, dict):
        raise ImportErrorSafe("Неизвестный формат ответа API; данные не заменены")
    if value.get("error") or value.get("success") is False or value.get("ok") is False:
        raise ImportErrorSafe("API сообщил об ошибке; данные не заменены")
    if any(value.get(k) for k in ("has_more", "hasMore", "next", "nextCursor", "next_cursor", "nextPage", "next_page", "truncated")):
        raise ImportErrorSafe("API вернул неполную выборку; требуется уточнить пагинацию")
    found = [value[k] for k in keys if k in value]
    if len(found) != 1:
        raise ImportErrorSafe("Неизвестный формат списка API; требуется проверить контракт")
    rows = collection(found[0], keys)
    for key in ("total", "totalCount", "total_count"):
        if key in value and value[key] is not None:
            try:
                if isinstance(value[key], bool) or int(value[key]) != len(rows):
                    raise ImportErrorSafe("Число строк API не совпадает с total; данные не заменены")
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ImportErrorSafe):
                    raise
                raise ImportErrorSafe("Некорректный счётчик строк API") from None
    return rows


def request_json(token, path, params=None):
    """Never follow redirects with the token or return upstream error text."""
    for attempt in range(3):
        try:
            with requests.get(API + path, params=params, headers={"token": token, "Accept": "application/json"},
                              timeout=(8, 45), allow_redirects=False, stream=True) as response:
                code = response.status_code
                if code in (401, 403):
                    raise ImportErrorSafe("API отклонил токен: проверьте ключ и права доступа")
                if code == 429 or 500 <= code < 600:
                    if attempt < 2:
                        retry = response.headers.get("Retry-After", "")
                        delay = min(15, int(retry)) if retry.isdigit() else 2 ** attempt
                        time.sleep(max(1, delay))
                        continue
                    raise ImportErrorSafe("API временно недоступен или достигнут лимит запросов")
                if code != 200:
                    raise ImportErrorSafe("API вернул HTTP " + str(code) + "; проверьте параметры подключения")
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ImportErrorSafe("Ответ API слишком большой; данные не заменены")
                try:
                    return json.loads(content)
                except (ValueError, UnicodeError):
                    raise ImportErrorSafe("API вернул ответ, который не является JSON") from None
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ImportErrorSafe("Не удалось связаться с Фулфилмент Платформой") from None


def parse_warehouses(payload):
    rows = collection(payload, ("data", "items", "sklads", "warehouses", "result"))
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ImportErrorSafe("Неизвестный формат склада в API")
        wid = identifier(row.get("id", row.get("skladId", row.get("sklad_id"))))
        if wid in seen:
            raise ImportErrorSafe("API вернул повторяющиеся склады")
        seen.add(wid)
        name = row.get("name") or row.get("title") or row.get("skladName") or row.get('description') or ("Склад " + wid)
        if not isinstance(name, str):
            raise ImportErrorSafe("Неизвестный формат названия склада")
        result.append({"id": wid, "name": name[:200]})
    if not result:
        raise ImportErrorSafe("API не вернул доступных складов")
    if len(result) > 500:
        raise ImportErrorSafe("Слишком много складов в ответе API")
    return result


def parse_stocks(payload, warehouse, requested_type, allow_empty=False):
    rows = collection(payload, ("data", "items", "stocks", "result"))
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ImportErrorSafe("Неизвестный формат строки остатков")
        nid = identifier(row.get("nomenclatureId"))
        if identifier(row.get("skladId")) != warehouse:
            raise ImportErrorSafe("API вернул остатки другого склада; данные не заменены")
        if nid in seen:
            raise ImportErrorSafe("API вернул дубли номенклатуры; данные не заменены")
        seen.add(nid)
        source_type = row.get("typeNomenclature")
        if not isinstance(source_type, str) or not source_type:
            raise ImportErrorSafe("В ответе API отсутствует тип номенклатуры")
        quantities = []
        for key in ("numAvailable", "numDefect", "numAtWork"):
            qty = row.get(key)
            if isinstance(qty, bool) or not isinstance(qty, int) or not -2147483648 <= qty <= 2147483647:
                raise ImportErrorSafe("API вернул некорректное количество; данные не заменены")
            quantities.append(qty)
        if not isinstance(row.get("clientArticul"), str) or not isinstance(row.get("name"), str):
            raise ImportErrorSafe("В ответе API отсутствует артикул или название")
        # Filters such as 'all' may return multiple product types. Store actual
        # types separately and reject ambiguous overlapping scopes later.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_type):
            raise ImportErrorSafe("API вернул неподдерживаемый тип номенклатуры")
        result.append({"nomenclature_id": nid, "client_article": row["clientArticul"][:500],
                       "name": row["name"][:2000], "nomenclature_type": source_type,
                       "available_qty": quantities[0], "defect_qty": quantities[1], "at_work_qty": quantities[2]})
    if not result and not allow_empty:
        raise ImportErrorSafe("API вернул пустой остаток. Прежние данные сохранены; проверьте склад и тип номенклатуры")
    # Exact type matching prevents mixing filter meanings before supplier confirms
    # an 'all' filter. Unknown broad filters need a separate validated adapter.
    if any(row["nomenclature_type"] != requested_type for row in result):
        raise ImportErrorSafe("Типы в ответе не совпадают с фильтром. Укажите отдельные типы номенклатуры")
    return result


def automatic_batches(token, warehouses, progress=lambda message: None):
    """Full product inventory using verified filter aliases and API warehouses."""
    batches, discovered, count = [], set(), 0
    if not warehouses or len(warehouses) * len(STOCK_FILTERS) > MAX_SCOPES:
        raise ImportErrorSafe('Число складов превышает предел автоматической загрузки')
    for warehouse in warehouses:
        for filter_name, kind in STOCK_FILTERS.items():
            payload = request_json(token, '/stocks', {'skladId': warehouse, 'isGetZeroStock': 'true', 'typeNomenclature': filter_name})
            rows = parse_stocks(payload, warehouse, kind, allow_empty=True)
            if rows:
                discovered.add(kind)
            count += len(rows)
            if count > MAX_ROWS:
                raise ImportErrorSafe('Превышен общий предел строк импорта; данные не заменены')
            batches.append((warehouse, kind, datetime.now(timezone.utc), rows))
            progress('Загружено выборок: ' + str(len(batches)))
    if not count:
        raise ImportErrorSafe('Фулфилмент не вернул товары. Прежние остатки сохранены')
    return batches, sorted(discovered)


def token_value(app, client):
    value = app.service_credential("ff_platform:" + client, "token")
    if not value:
        raise ImportErrorSafe("Сначала сохраните API-токен")
    return value


def serialized(row):
    return {k: v.isoformat() if isinstance(v, datetime) else str(v) if isinstance(v, uuid.UUID) else v
            for k, v in dict(row).items()}


def advisory_lock(cur, client):
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("ff_platform:" + client,))


def guard_idle(cur, client):
    advisory_lock(cur, client)
    cur.execute("SELECT 1 FROM ff_platform.jobs WHERE client_key=%s AND status IN ('queued','running')", (client,))
    if cur.fetchone():
        raise ImportErrorSafe("Импорт уже выполняется. Дождитесь завершения")


def save(app, client, payload):
    # The cabinet controls its warehouses/types; no technical form settings.
    config = {'warehouse_ids': [], 'nomenclature_types': [], 'automatic': True}
    token = payload.get("token", "")
    if not isinstance(token, str) or len(token) > 8192 or any(ch in token for ch in ("\n", "\r", "\x00")):
        raise ImportErrorSafe("Некорректный API-токен")
    token = token.strip()
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            guard_idle(cur, client)
            cur.execute("SELECT 1 FROM public.bi_service_credentials WHERE service_key=%s AND credential_key='token'",
                        ("ff_platform:" + client,))
            if not token and not cur.fetchone():
                raise ImportErrorSafe("Введите API-токен")
            if token:
                master = app.client_credentials_master_key(create=True)
                if not master:
                    raise ImportErrorSafe("Шифрованное хранилище недоступно")
                cur.execute("""INSERT INTO public.bi_service_credentials
                 (service_key,credential_key,encrypted_value,fingerprint)
                 VALUES(%s,'token',pgp_sym_encrypt(%s,%s,'cipher-algo=aes256'),%s)
                 ON CONFLICT(service_key,credential_key) DO UPDATE SET
                 encrypted_value=EXCLUDED.encrypted_value,fingerprint=EXCLUDED.fingerprint,updated_at=now()""",
                            ("ff_platform:" + client, token, master, hashlib.sha256(token.encode()).hexdigest()[:10]))
            cur.execute("SELECT settings FROM ff_platform.connections WHERE client_key=%s", (client,))
            previous = cur.fetchone()
            if previous and not token:
                config = dict(previous['settings'])
                config['automatic'] = True
            cur.execute("""INSERT INTO ff_platform.connections(client_key,settings) VALUES(%s,%s)
             ON CONFLICT(client_key) DO UPDATE SET settings=EXCLUDED.settings,
             check_status=CASE WHEN %s THEN 'untested' ELSE ff_platform.connections.check_status END,
             checked_at=CASE WHEN %s THEN NULL ELSE ff_platform.connections.checked_at END,
             check_message=CASE WHEN %s THEN '' ELSE ff_platform.connections.check_message END,updated_at=now()""",
                        (client, Json(config), bool(token), bool(token), bool(token)))


def check(app, client):
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            guard_idle(cur, client)
            # Keep a row lock across the bounded request so save/import cannot
            # race a credential replacement and publish stale discovered scopes.
            cur.execute("SELECT settings FROM ff_platform.connections WHERE client_key=%s FOR UPDATE", (client,))
            row = cur.fetchone()
            if not row:
                raise ImportErrorSafe("Сначала сохраните подключение")
            config = dict(row["settings"])
            try:
                warehouses = parse_warehouses(request_json(token_value(app, client), "/sklads"))
                config["warehouses"] = warehouses
                config['warehouse_ids'] = [w['id'] for w in warehouses]
                config['automatic'] = True
                message = "Доступ подтверждён; складов: " + str(len(warehouses))
                status = "connected"
            except ImportErrorSafe as exc:
                message, status = str(exc), "failed"
            cur.execute("""UPDATE ff_platform.connections SET settings=%s,checked_at=now(),
             check_status=%s,check_message=%s WHERE client_key=%s""", (Json(config), status, message, client))
    return status, message


def disconnect(app, client):
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            guard_idle(cur, client)
            cur.execute("DELETE FROM public.bi_service_credentials WHERE service_key=%s AND credential_key='token'",
                        ("ff_platform:" + client,))
            cur.execute("DELETE FROM ff_platform.connections WHERE client_key=%s", (client,))
    # Imported history is retained; disconnect is not an inventory wipe.


def recover_jobs(app, client):
    """Only recover stale dead local workers, never a live import."""
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id,pid FROM ff_platform.jobs WHERE client_key=%s
             AND status IN ('queued','running') AND heartbeat_at < now()-interval '10 minutes'""", (client,))
            rows = cur.fetchall()
            for row in rows:
                if row["pid"] and Path("/proc", str(row["pid"])).exists():
                    continue
                cur.execute("""UPDATE ff_platform.jobs SET status='failed',finished_at=now(),
                 message='Процесс импорта прерван; прежние данные сохранены'
                 WHERE id=%s AND status IN ('queued','running')""", (row["id"],))


def status_payload(app, client):
    recover_jobs(app, client)
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.bi_service_credentials WHERE service_key=%s AND credential_key='token'", ("ff_platform:" + client,))
            saved = bool(cur.fetchone())
            cur.execute("SELECT settings,checked_at,check_status,check_message,updated_at FROM ff_platform.connections WHERE client_key=%s", (client,))
            row = cur.fetchone()
            connection = serialized(row) if row else {"settings": {"warehouse_ids": [], "nomenclature_types": []}, "check_status": "not_configured"}
            cur.execute("""SELECT id,status,created_at,started_at,finished_at,result,message
             FROM ff_platform.jobs WHERE client_key=%s ORDER BY created_at DESC LIMIT 10""", (client,))
            jobs = [serialized(r) for r in cur.fetchall()]
            cur.execute("""SELECT count(*) rows,coalesce(sum(available_qty),0) available,
             coalesce(sum(defect_qty),0) defect,coalesce(sum(at_work_qty),0) at_work,
             min(observed_at) oldest_at,max(observed_at) latest_at
             FROM ff_platform.current_stocks WHERE client_key=%s""", (client,))
            summary = serialized(cur.fetchone())
    return {"ok": True, "client": client, "saved": saved, "connection": connection,
            "jobs": jobs, "summary": summary, "api_url": API,
            "notice": "Товарные остатки по всем доступным складам, включая нулевые. Упаковка и расходные материалы в этот импорт не входят."}


def preview(app, client, query):
    try:
        page = max(1, min(10000, int(query.get("page", ["1"])[0])))
    except ValueError:
        raise ImportErrorSafe("Некорректная страница") from None
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) total FROM ff_platform.current_stocks WHERE client_key=%s", (client,))
            total = cur.fetchone()["total"]
            cur.execute("""SELECT warehouse_id,nomenclature_type,nomenclature_id,client_article,name,
             available_qty,defect_qty,at_work_qty,observed_at FROM ff_platform.current_stocks
             WHERE client_key=%s ORDER BY warehouse_id,nomenclature_type,nomenclature_id LIMIT 50 OFFSET %s""",
                        (client, (page - 1) * 50))
            rows = [serialized(r) for r in cur.fetchall()]
    return {"ok": True, "client": client, "page": page, "total": total, "rows": rows}


def start(app, client, identity):
    recover_jobs(app, client)
    job = str(uuid.uuid4())
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            guard_idle(cur, client)
            cur.execute("SELECT settings,check_status FROM ff_platform.connections WHERE client_key=%s FOR UPDATE", (client,))
            row = cur.fetchone()
            if not row or row["check_status"] != "connected":
                raise ImportErrorSafe("Сначала сохраните и проверьте подключение")
            config = dict(row['settings'])
            config['automatic'] = True
            if not config.get('warehouse_ids'):
                raise ImportErrorSafe('Сначала проверьте доступ к кабинету')
            discovered = {v["id"] for v in row["settings"].get("warehouses", [])}
            if not set(config["warehouse_ids"]).issubset(discovered):
                raise ImportErrorSafe("Выбранные склады не подтверждены проверкой подключения")
            token_value(app, client)
            cur.execute("INSERT INTO ff_platform.jobs(id,client_key,status,request,user_id) VALUES(%s,%s,'queued',%s,%s)",
                        (job, client, Json(config), identity.get("user_id")))
    try:
        child = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--job", job],
                                 cwd=str(Path(__file__).parent), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        with app.client_registry_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ff_platform.jobs SET pid=%s WHERE id=%s AND status='queued'", (child.pid, job))
    except Exception:
        with app.client_registry_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ff_platform.jobs SET status='failed',finished_at=now(),message='Не удалось запустить импорт' WHERE id=%s AND status='queued'", (job,))
        raise ImportErrorSafe("Не удалось запустить импорт") from None
    return {"ok": True, "job_id": job, "status": "queued"}


def publish(app, job, client, batches):
    # One transaction publishes every requested scope, or keeps all old scopes.
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            total = 0
            for warehouse, kind, observed, rows in batches:
                digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                cur.execute("INSERT INTO ff_platform.scopes VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            (job, client, warehouse, kind, observed, len(rows), digest))
                if rows:
                    execute_values(cur, """INSERT INTO ff_platform.stocks
                 (batch_id,client_key,warehouse_id,nomenclature_type,nomenclature_id,
                 client_article,name,available_qty,defect_qty,at_work_qty) VALUES %s""",
                               [(job, client, warehouse, kind, r["nomenclature_id"], r["client_article"], r["name"],
                                 r["available_qty"], r["defect_qty"], r["at_work_qty"]) for r in rows], page_size=1000)
                cur.execute("""INSERT INTO ff_platform.active VALUES(%s,%s,%s,%s)
                 ON CONFLICT(client_key,warehouse_id,nomenclature_type) DO UPDATE SET batch_id=EXCLUDED.batch_id""",
                            (client, warehouse, kind, job))
                total += len(rows)
            detected = sorted({kind for warehouse, kind, observed, rows in batches if rows})
            cur.execute("UPDATE ff_platform.connections SET settings=settings || %s::jsonb WHERE client_key=%s",
                        (Json({'nomenclature_types': detected, 'automatic': True}), client))
            cur.execute("""UPDATE ff_platform.jobs SET status='completed',finished_at=now(),heartbeat_at=now(),
             result=%s,message='Остатки импортированы' WHERE id=%s AND status='running'""",
                        (Json({"rows": total, "scopes": len(batches), "mode": "full", "zero_stock_requested": True,
                               "detected_types": detected, "automatic": True, "domain": "goods", "request_filters": list(STOCK_FILTERS),
                               "completeness": "shape_checked_supplier_limits_unconfirmed"}), job))
            if cur.rowcount != 1:
                raise ImportErrorSafe("Состояние задания изменилось; данные не опубликованы")


def worker(app, job):
    ensure(app)
    with app.client_registry_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE ff_platform.jobs SET status='running',started_at=now(),heartbeat_at=now(),pid=%s
             WHERE id=%s AND status='queued' RETURNING client_key,request""", (os.getpid(), job))
            row = cur.fetchone()
    if not row:
        return
    try:
        client, config = row["client_key"], row["request"]
        token = token_value(app, client)
        def progress(message):
            with app.client_registry_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute('UPDATE ff_platform.jobs SET heartbeat_at=now(),message=%s WHERE id=%s', (message, job))
        warehouses = parse_warehouses(request_json(token, '/sklads'))
        batches, detected = automatic_batches(token, [w['id'] for w in warehouses], progress)
        publish(app, job, client, batches)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ImportErrorSafe) else "Импорт не завершён. Прежние остатки сохранены"
        with app.client_registry_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE ff_platform.jobs SET status='failed',finished_at=now(),heartbeat_at=now(),
                 message=%s WHERE id=%s AND status='running'""", (message, job))


def handle(rt, handler, method):
    app = rt.app
    parsed = urlparse(handler.path)
    if parsed.path != ENDPOINT:
        handler.send_error(404)
        return
    if not handler.dashboard_access_granted():
        handler.send_dashboard_access_required(parsed)
        return
    identity = handler.dashboard_access_identity() or {}
    access_context = app.CURRENT_ACCESS_USER.set(identity)
    try:
        permitted = bool(identity.get("is_admin")) or bool(
            {"integrations", "clientOnboarding"}.intersection(app.current_admin_section_ids()))
        allowed_clients = {item["key"] for item in app.dashboard_clients_payload()}
    finally:
        app.CURRENT_ACCESS_USER.reset(access_context)
    if not permitted:
        handler.send_json({"ok": False, "error": "Нет права на настройку подключений"}, status=403)
        return
    if method == "POST":
        origin = handler.headers.get("Origin")
        if handler.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlparse(origin).netloc != handler.headers.get("Host")):
            handler.send_json({"ok": False, "error": "Недопустимый источник запроса"}, status=403)
            return
    try:
        query = parse_qs(parsed.query)
        payload = handler.read_json_body() if method == "POST" else {}
        if not isinstance(payload, dict):
            raise ImportErrorSafe("Некорректный запрос")
        client = client_key(app, payload.get("client") or query.get("client", ["toptop"])[0])
        if client not in allowed_clients:
            handler.send_json({"ok": False, "error": "Нет доступа к выбранному аккаунту"}, status=403)
            return
        ensure(app)
        if method == "GET":
            result = preview(app, client, query) if query.get("view") == ["preview"] else status_payload(app, client)
        else:
            action = payload.get("action")
            if action == "save":
                save(app, client, payload)
            elif action == "check":
                check(app, client)
            elif action == "disconnect":
                disconnect(app, client)
            elif action == "import":
                handler.send_json(start(app, client, identity), status=202, headers={"Cache-Control": "no-store"})
                return
            else:
                raise ImportErrorSafe("Неизвестное действие")
            result = status_payload(app, client)
        handler.send_json(result, headers={"Cache-Control": "no-store"})
    except ImportErrorSafe as exc:
        handler.send_json({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        # Do not print tracebacks: driver/HTTP errors can contain sensitive data.
        handler.send_json({"ok": False, "error": "Сервис импорта временно недоступен"}, status=503)


def server(rt):
    class Handler(rt.VPSAdminHandler):
        def do_GET(self):
            handle(rt, self, "GET")

        def do_POST(self):
            handle(rt, self, "POST")

        def do_HEAD(self):
            self.send_error(405)

    return ThreadingHTTPServer(("0.0.0.0", 8063), Handler)


def start_server(rt):
    service = server(rt)
    threading.Thread(target=service.serve_forever, daemon=True, name="ff-platform-api").start()
    return service


if __name__ == "__main__":
    import pulse_vps_admin as runtime
    runtime.configure_scope()
    runtime._USE_WRITER_CONFIG.set(True)
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        worker(runtime.app, str(uuid.UUID(sys.argv[2])))
    else:
        server(runtime).serve_forever()
