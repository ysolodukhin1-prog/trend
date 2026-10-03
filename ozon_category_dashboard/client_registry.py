"""Persistent client registry and encrypted marketplace credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


CLIENT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
MARKETPLACES = {"wb", "ozon", "avito", "lamoda", "yandex_market"}
DEFAULT_CLIENTS_ROOT = Path(
    os.environ.get(
        "BI_CLIENTS_ROOT",
        r"G:\Общие диски\Kokoc Marketplaces\Clients",
    )
)


def normalize_client_payload(payload: dict, report_ids: set[str]) -> dict:
    key = str(payload.get("key") or "").strip().lower()
    label = str(payload.get("label") or "").strip()
    db_name = str(payload.get("db_name") or key).strip().lower()
    root_path = str(payload.get("root_path") or (DEFAULT_CLIENTS_ROOT / (label or key))).strip()
    status = str(payload.get("status") or "active").strip().lower()
    marketplaces = sorted({str(value).strip().lower() for value in payload.get("marketplaces", []) if str(value).strip()})
    reports = [str(value).strip() for value in payload.get("reports", []) if str(value).strip() in report_ids]

    if not CLIENT_KEY_RE.fullmatch(key):
        raise ValueError("Ключ клиента: 2–48 латинских символов, цифр или _, первый символ — буква")
    if not label or len(label) > 120:
        raise ValueError("Укажите название клиента длиной до 120 символов")
    if label in {".", ".."} or any(char in label for char in '<>:"/\\|?*'):
        raise ValueError("Название клиента содержит символ, недопустимый для папки Windows")
    if not CLIENT_KEY_RE.fullmatch(db_name):
        raise ValueError("Имя БД: 2–48 латинских символов, цифр или _, первый символ — буква")
    if status not in {"active", "paused"}:
        raise ValueError("Некорректный статус клиента")
    if not set(marketplaces).issubset(MARKETPLACES) or not marketplaces:
        raise ValueError("Выберите хотя бы один маркетплейс")
    if not reports:
        raise ValueError("Выберите хотя бы один доступный отчёт")
    # TREND stores marketplace access in PostgreSQL and no longer provisions
    # a per-client Windows folder during connection saves. Keep root_path only
    # as backward-compatible metadata; never reject or create it here.

    return {
        "key": key,
        "label": label,
        "db_name": db_name,
        "root_path": root_path,
        "status": status,
        "marketplaces": marketplaces,
        "reports": list(dict.fromkeys(reports)),
    }


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_client_registry (
                client_key text PRIMARY KEY,
                label text NOT NULL,
                db_name text NOT NULL,
                status text NOT NULL DEFAULT 'active',
                root_path text NOT NULL,
                marketplaces jsonb NOT NULL DEFAULT '[]'::jsonb,
                reports jsonb NOT NULL DEFAULT '[]'::jsonb,
                operation text,
                operation_status text,
                operation_stage text,
                operation_progress integer NOT NULL DEFAULT 0,
                operation_message text,
                operation_error text,
                operation_started_at timestamptz,
                operation_finished_at timestamptz,
                history_date_from date,
                history_date_to date,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        for statement in (
            "ADD COLUMN IF NOT EXISTS operation text",
            "ADD COLUMN IF NOT EXISTS operation_status text",
            "ADD COLUMN IF NOT EXISTS operation_stage text",
            "ADD COLUMN IF NOT EXISTS operation_progress integer NOT NULL DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS operation_message text",
            "ADD COLUMN IF NOT EXISTS operation_error text",
            "ADD COLUMN IF NOT EXISTS operation_started_at timestamptz",
            "ADD COLUMN IF NOT EXISTS operation_finished_at timestamptz",
            "ADD COLUMN IF NOT EXISTS history_date_from date",
            "ADD COLUMN IF NOT EXISTS history_date_to date",
        ):
            cur.execute(f"ALTER TABLE public.bi_client_registry {statement}")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_client_credentials (
                client_key text NOT NULL REFERENCES public.bi_client_registry(client_key) ON DELETE CASCADE,
                credential_key text NOT NULL,
                encrypted_value bytea NOT NULL,
                fingerprint text NOT NULL,
                updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (client_key, credential_key)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_client_marketplace_accounts (
                client_key text NOT NULL REFERENCES public.bi_client_registry(client_key) ON DELETE CASCADE,
                marketplace text NOT NULL,
                external_account_id text NOT NULL,
                label text NOT NULL DEFAULT '',
                is_accessible boolean NOT NULL DEFAULT true,
                last_verified_at timestamptz NOT NULL DEFAULT now(),
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (client_key, marketplace, external_account_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.bi_client_marketplace_stores (
                client_key text NOT NULL,
                marketplace text NOT NULL,
                external_account_id text NOT NULL,
                store_id text NOT NULL,
                label text NOT NULL DEFAULT '',
                domain text NOT NULL DEFAULT '',
                placement_type text NOT NULL DEFAULT '',
                source_status text NOT NULL DEFAULT '',
                import_enabled boolean NOT NULL DEFAULT true,
                is_accessible boolean NOT NULL DEFAULT true,
                last_seen_at timestamptz NOT NULL DEFAULT now(),
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (client_key, marketplace, external_account_id, store_id),
                FOREIGN KEY (client_key, marketplace, external_account_id)
                    REFERENCES public.bi_client_marketplace_accounts(client_key, marketplace, external_account_id)
                    ON DELETE CASCADE
            )
            """
        )
    conn.commit()


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


def _serialize_row(row) -> dict:
    item = dict(row)
    item["key"] = item.pop("client_key")
    item["marketplaces"] = _json_list(item.get("marketplaces"))
    item["reports"] = _json_list(item.get("reports"))
    credentials = item.get("credentials")
    item["credentials"] = credentials if isinstance(credentials, dict) else {}
    for field in (
        "created_at", "updated_at", "operation_started_at", "operation_finished_at",
        "history_date_from", "history_date_to",
    ):
        value = item.get(field)
        item[field] = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    return item


def list_clients(conn) -> list[dict]:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.client_key, r.label, r.db_name, r.status, r.root_path,
                   r.marketplaces, r.reports, r.operation, r.operation_status,
                   r.operation_stage, r.operation_progress, r.operation_message,
                   r.operation_error, r.operation_started_at, r.operation_finished_at,
                   r.history_date_from, r.history_date_to, r.created_at, r.updated_at,
                   COALESCE(jsonb_object_agg(c.credential_key,
                       jsonb_build_object('saved', true, 'fingerprint', c.fingerprint,
                                          'updated_at', c.updated_at)
                   ) FILTER (WHERE c.credential_key IS NOT NULL), '{}'::jsonb) AS credentials
            FROM public.bi_client_registry r
            LEFT JOIN public.bi_client_credentials c ON c.client_key = r.client_key
            GROUP BY r.client_key
            ORDER BY lower(r.label), r.client_key
            """
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT a.client_key, a.marketplace, a.external_account_id, a.label,
                   a.is_accessible, a.last_verified_at,
                   COALESCE(jsonb_agg(jsonb_build_object(
                       'campaign_id', s.store_id, 'name', s.label, 'domain', s.domain,
                       'placement_type', s.placement_type, 'status', s.source_status,
                       'import_enabled', s.import_enabled, 'is_accessible', s.is_accessible,
                       'last_seen_at', s.last_seen_at
                   ) ORDER BY lower(s.label), s.store_id)
                   FILTER (WHERE s.store_id IS NOT NULL), '[]'::jsonb) AS stores
            FROM public.bi_client_marketplace_accounts a
            LEFT JOIN public.bi_client_marketplace_stores s
              ON s.client_key = a.client_key AND s.marketplace = a.marketplace
             AND s.external_account_id = a.external_account_id
            GROUP BY a.client_key, a.marketplace, a.external_account_id, a.label,
                     a.is_accessible, a.last_verified_at
            ORDER BY a.client_key, a.marketplace, lower(a.label), a.external_account_id
            """
        )
        account_rows = cur.fetchall()
    clients = [_serialize_row(row) for row in rows]
    by_key = {item["key"]: item for item in clients}
    for row in account_rows:
        item = dict(row)
        client = by_key.get(str(item.get("client_key") or ""))
        if not client:
            continue
        stores = []
        for raw_store in item.get("stores") or []:
            store = dict(raw_store)
            last_seen = store.get("last_seen_at")
            store["last_seen_at"] = last_seen.isoformat() if hasattr(last_seen, "isoformat") else str(last_seen or "")
            stores.append(store)
        accounts = client.setdefault("marketplace_accounts", {})
        accounts.setdefault(str(item.get("marketplace") or ""), []).append({
            "business_id": str(item.get("external_account_id") or ""),
            "name": str(item.get("label") or ""),
            "is_accessible": bool(item.get("is_accessible")),
            "last_verified_at": item.get("last_verified_at").isoformat() if hasattr(item.get("last_verified_at"), "isoformat") else str(item.get("last_verified_at") or ""),
            "stores": stores,
        })
    return clients


def save_marketplace_accounts(
    conn,
    client_key: str,
    marketplace: str,
    accounts: list[dict],
    enabled_store_ids: set[str] | None = None,
) -> None:
    ensure_schema(conn)
    enabled = {str(value) for value in enabled_store_ids} if enabled_store_ids is not None else None
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.bi_client_marketplace_accounts SET is_accessible = false "
            "WHERE client_key = %s AND marketplace = %s",
            (client_key, marketplace),
        )
        cur.execute(
            "UPDATE public.bi_client_marketplace_stores SET is_accessible = false "
            "WHERE client_key = %s AND marketplace = %s",
            (client_key, marketplace),
        )
        for account in accounts:
            account_id = str(account.get("business_id") or "").strip()
            if not account_id:
                continue
            cur.execute(
                """
                INSERT INTO public.bi_client_marketplace_accounts
                    (client_key, marketplace, external_account_id, label, is_accessible, last_verified_at)
                VALUES (%s, %s, %s, %s, true, now())
                ON CONFLICT (client_key, marketplace, external_account_id) DO UPDATE SET
                    label = EXCLUDED.label, is_accessible = true, last_verified_at = now()
                """,
                (client_key, marketplace, account_id, str(account.get("name") or "")),
            )
            for store in account.get("stores") or []:
                store_id = str(store.get("campaign_id") or "").strip()
                if not store_id:
                    continue
                is_accessible = store.get("is_accessible", True) is not False
                import_enabled = is_accessible and (store_id in enabled if enabled is not None else bool(store.get("import_enabled", True)))
                cur.execute(
                    """
                    INSERT INTO public.bi_client_marketplace_stores
                        (client_key, marketplace, external_account_id, store_id, label, domain,
                         placement_type, source_status, import_enabled, is_accessible, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (client_key, marketplace, external_account_id, store_id) DO UPDATE SET
                        label = EXCLUDED.label, domain = EXCLUDED.domain,
                        placement_type = EXCLUDED.placement_type, source_status = EXCLUDED.source_status,
                        import_enabled = CASE WHEN NOT EXCLUDED.is_accessible THEN false
                            WHEN %s THEN EXCLUDED.import_enabled
                            ELSE bi_client_marketplace_stores.import_enabled END,
                        is_accessible = EXCLUDED.is_accessible, last_seen_at = now()
                    """,
                    (
                        client_key, marketplace, account_id, store_id,
                        str(store.get("name") or ""), str(store.get("domain") or ""),
                        str(store.get("placement_type") or ""), str(store.get("status") or ""),
                        import_enabled, is_accessible, enabled is not None,
                    ),
                )
    conn.commit()


def get_client(conn, client_key: str) -> dict | None:
    return next((item for item in list_clients(conn) if item["key"] == client_key), None)


def credential_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def save_client(conn, client: dict, credentials: dict[str, str], master_key: str) -> dict:
    if any(credentials.get(k) for k in ("lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id")):
        raise ValueError("Lamoda: используйте отдельный аккаунт FBO/FBS")
    ensure_schema(conn)
    allowed = {
        "wb_api_token", "wb_service_api_token", "ozon_client_id", "ozon_api_key",
        "ozon_performance_client_id", "ozon_performance_client_secret",
        "avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret",
        "lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id",
        "yandex_market_api_key", "yandex_market_business_id", "yandex_market_campaign_id",
    }
    clean_credentials = {
        str(key): str(value).strip()
        for key, value in credentials.items()
        if key in allowed and str(value).strip()
    }
    if clean_credentials and not master_key:
        raise RuntimeError("Не настроен CLIENT_CREDENTIALS_MASTER_KEY")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.bi_client_registry
                (client_key, label, db_name, status, root_path, marketplaces, reports)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (client_key) DO UPDATE SET
                label = EXCLUDED.label, db_name = EXCLUDED.db_name,
                status = EXCLUDED.status, root_path = EXCLUDED.root_path,
                marketplaces = EXCLUDED.marketplaces, reports = EXCLUDED.reports,
                updated_at = now()
            """,
            (
                client["key"], client["label"], client["db_name"], client["status"],
                client["root_path"], json.dumps(client["marketplaces"]), json.dumps(client["reports"]),
            ),
        )
        for credential_key, value in clean_credentials.items():
            cur.execute(
                """
                INSERT INTO public.bi_client_credentials
                    (client_key, credential_key, encrypted_value, fingerprint)
                VALUES (%s, %s, pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256'), %s)
                ON CONFLICT (client_key, credential_key) DO UPDATE SET
                    encrypted_value = EXCLUDED.encrypted_value,
                    fingerprint = EXCLUDED.fingerprint,
                    updated_at = now()
                """,
                (client["key"], credential_key, value, master_key, credential_fingerprint(value)),
            )
    conn.commit()
    return client


def update_client_status(conn, client_key: str, status: str) -> None:
    if status not in {"active", "paused"}:
        raise ValueError("Некорректный статус клиента")
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.bi_client_registry SET status = %s, updated_at = now() WHERE client_key = %s",
            (status, client_key),
        )
    conn.commit()


def update_client_operation(
    conn,
    client_key: str,
    *,
    operation: str,
    status: str,
    stage: str,
    progress: int,
    message: str = "",
    error: str = "",
    history_date_from: str | None = None,
    history_date_to: str | None = None,
) -> None:
    ensure_schema(conn)
    finished = status in {"completed", "partial", "failed", "stopped"}
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.bi_client_registry
            SET operation = %s, operation_status = %s, operation_stage = %s,
                operation_progress = %s, operation_message = %s, operation_error = %s,
                operation_started_at = CASE WHEN %s = 'running' AND operation_status IS DISTINCT FROM 'running'
                    THEN now() ELSE operation_started_at END,
                operation_finished_at = CASE WHEN %s THEN now() ELSE NULL END,
                history_date_from = COALESCE(%s::date, history_date_from),
                history_date_to = COALESCE(%s::date, history_date_to),
                updated_at = now()
            WHERE client_key = %s
            """,
            (
                operation, status, stage, max(0, min(100, int(progress))), message, error,
                status, finished, history_date_from, history_date_to, client_key,
            ),
        )
    conn.commit()


def read_credential(conn, client_key: str, credential_key: str, master_key: str) -> str:
    if not master_key:
        return ""
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pgp_sym_decrypt(encrypted_value, %s)
            FROM public.bi_client_credentials
            WHERE client_key = %s AND credential_key = %s
            """,
            (master_key, client_key, credential_key),
        )
        row = cur.fetchone()
    if not row:
        return ""
    if isinstance(row, dict):
        return str(next(iter(row.values())) or "")
    return str(row[0] or "")
