#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extended read-only WB API collection for an isolated client database.

The script stores source responses in JSONB with explicit capture timestamps.
It never reuses another client's token and never calls mutation endpoints.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))
os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))

import app  # noqa: E402
import inventory_history  # noqa: E402
from wb_advertising_view import rebuild_wb_advertising_view  # noqa: E402
from wb_rate_limit import TokenBucketLimiter  # noqa: E402


TARGET_DB = os.environ.get("DASHBOARD_DB_NAME", "km_trade_products")
CLIENT_KEY = os.environ.get("DASHBOARD_CLIENT", "km_trade")
CLIENT_SUFFIX = CLIENT_KEY.upper()
TOKEN_ENV = os.environ.get("WB_API_TOKEN_ENV") or (
    "WB_API_TOKEN_KM_TRADE" if CLIENT_KEY == "km_trade" else f"WB_API_TOKEN_{CLIENT_SUFFIX}"
)
TIMEOUT = 180
MAX_RETRIES = 4
OUTPUT_ROOT = Path(os.environ.get("WB_API_OUTPUT_ROOT") or (PROJECT_ROOT / "work"))
OUTPUT_DIR = OUTPUT_ROOT / f"{CLIENT_KEY}_wb_api"
COMMUNICATION_MIGRATION = PROJECT_ROOT / "migrations" / "20260816_wb_feedbacks_questions.sql"
RATE_LIMITER = TokenBucketLimiter()
SEARCH_DETAILS_PAGE_SIZE = 1000
NORMQUERY_ITEM_BATCH_SIZE = 100
NORMQUERY_WINDOW_DAYS = 30
PROMOTION_ATTRIBUTION_LOOKBACK_DAYS = max(
    1,
    int(os.environ.get("WB_PROMOTION_ATTRIBUTION_LOOKBACK_DAYS", "30")),
)
PROMOTION_CHECKPOINT_RETENTION_DAYS = max(
    1,
    int(os.environ.get("WB_PROMOTION_CHECKPOINT_RETENTION_DAYS", "14")),
)


def api_rate_rule(host: str, path: str) -> tuple[int, int] | None:
    if host == "https://content-api.wildberries.ru":
        return 100, 60
    if host == "https://seller-analytics-api.wildberries.ru":
        return 3, 63
    if host == "https://advert-api.wildberries.ru":
        clean_path = path.split("?", 1)[0]
        if clean_path == "/adv/v1/normquery/stats":
            return 10, 60
        if clean_path in {"/adv/v1/promotion/count", "/api/advert/v2/adverts"}:
            return 5, 1
        if clean_path == "/adv/v3/fullstats":
            # WB documents a 20-second interval and burst=1. Keep a one-second
            # safety margin because server and local token-bucket clocks can drift.
            return 1, 21
        return 3, 63
    if host in {"https://finance-api.wildberries.ru", "https://statistics-api.wildberries.ru"}:
        return 1, 61
    if host == "https://feedbacks-api.wildberries.ru":
        return 6, 2
    return None


def api_rate_key(host: str, path: str) -> tuple[str, str]:
    clean_path = path.split("?", 1)[0]
    if host == "https://advert-api.wildberries.ru":
        if clean_path == "/adv/v1/normquery/stats":
            return host, "promotion-normquery-account"
        if clean_path in {"/adv/v1/promotion/count", "/api/advert/v2/adverts"}:
            return host, "promotion-campaign-metadata-account"
        if clean_path == "/adv/v3/fullstats":
            return host, "promotion-fullstats-account"
        return host, "promotion-account"
    if host == "https://seller-analytics-api.wildberries.ru" and clean_path.startswith("/api/v2/search-report/"):
        return host, "search-report-account"
    if host == "https://feedbacks-api.wildberries.ru":
        return host, "feedbacks-questions-account"
    return host, clean_path


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def token_value() -> str:
    values = app.read_app_env_file()
    token = str(os.environ.get(TOKEN_ENV) or values.get(TOKEN_ENV) or "").strip()
    if not token:
        token = str(app.registered_client_credential(CLIENT_KEY, "wb_api_token") or "").strip()
    if not token and CLIENT_KEY == getattr(app, "DEFAULT_CLIENT", "gloria_jeans"):
        fallback_env = app.wb_api_token_env(CLIENT_KEY)
        token = str(os.environ.get(fallback_env) or values.get(fallback_env) or "").strip()
    if not token:
        raise RuntimeError(f"Не найден WB API токен клиента {CLIENT_KEY}: {TOKEN_ENV}")
    return token


def db_connection():
    config = app.read_db_config(CLIENT_KEY)
    config["database"] = TARGET_DB
    config["application_name"] = f"{CLIENT_KEY}_wb_extended_sync"
    conn = psycopg2.connect(**config)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    if actual != TARGET_DB:
        conn.close()
        raise RuntimeError(f"Ожидалась БД {TARGET_DB}, получена {actual}")
    return conn


def sleep_progress(seconds: int, label: str) -> None:
    remaining = max(0, int(seconds))
    while remaining:
        print(f"ЛИМИТ API: {label} | осталось {remaining}s", flush=True)
        chunk = min(1, remaining)
        time.sleep(chunk)
        remaining -= chunk


def api_request(
    host: str,
    path: str,
    token: str,
    *,
    method: str = "GET",
    body: Any = None,
    binary: bool = False,
    request_label: str = "",
) -> tuple[Any, int, dict[str, str]]:
    if method not in {"GET", "POST"}:
        raise ValueError("Разрешены только GET/POST read-only отчёты")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": token, "Accept": "application/zip" if binary else "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(1, MAX_RETRIES + 1):
        rule = api_rate_rule(host, path)
        if rule:
            capacity, period_seconds = rule
            RATE_LIMITER.acquire(
                api_rate_key(host, path),
                capacity=capacity,
                period_seconds=period_seconds,
                wait=lambda seconds: sleep_progress(
                    max(1, int(seconds + 0.999)),
                    f"{request_label or path} | превентивный лимит",
                ),
            )
        request = Request(host + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
                response_headers = {key: value for key, value in response.headers.items()}
                if response.status == 204 or not raw:
                    return [], response.status, response_headers
                if binary:
                    return raw, response.status, response_headers
                return json.loads(raw.decode("utf-8")), response.status, response_headers
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            if exc.code == 204:
                return [], 204, {}
            if exc.code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                raw_wait = exc.headers.get("X-Ratelimit-Retry") or exc.headers.get("Retry-After")
                try:
                    wait = max(1, min(180, int(float(raw_wait))))
                except (TypeError, ValueError):
                    wait = min(120, 20 * attempt)
                sleep_progress(wait, f"{request_label or path} | HTTP {exc.code} | попытка {attempt}/{MAX_RETRIES}")
                continue
            raise RuntimeError(f"WB HTTP {exc.code} {path}: {detail}") from exc
        except (TimeoutError, URLError) as exc:
            if attempt < MAX_RETRIES:
                sleep_progress(min(120, 20 * attempt), f"{request_label or path} | сеть | попытка {attempt}/{MAX_RETRIES}")
                continue
            raise RuntimeError(f"WB network error {path}: {exc}") from exc
    raise RuntimeError(f"WB retry limit reached: {path}")


def ensure_schema(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.wb_api_source_snapshots (
            snapshot_id bigserial PRIMARY KEY,
            source_key text NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT now(),
            period_from date,
            period_to date,
            http_status integer,
            row_count integer NOT NULL DEFAULT 0,
            request_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
            payload jsonb NOT NULL,
            payload_sha256 text NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wb_api_snapshots_source_time
            ON public.wb_api_source_snapshots(source_key, captured_at DESC);

        CREATE TABLE IF NOT EXISTS public.wb_api_entities (
            source_key text NOT NULL,
            entity_key text NOT NULL,
            record_date date,
            captured_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL,
            PRIMARY KEY (source_key, entity_key)
        );
        CREATE INDEX IF NOT EXISTS idx_wb_api_entities_source_date
            ON public.wb_api_entities(source_key, record_date);

        CREATE TABLE IF NOT EXISTS public.wb_promotion_sync_checkpoints (
            date_from date NOT NULL,
            date_to date NOT NULL,
            checkpoint_key text NOT NULL,
            payload jsonb NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (date_from, date_to, checkpoint_key)
        );
        CREATE INDEX IF NOT EXISTS idx_wb_promotion_checkpoint_fetched_at
            ON public.wb_promotion_sync_checkpoints(fetched_at);

        CREATE TABLE IF NOT EXISTS public.wb_finance_lines (
            rrd_id bigint PRIMARY KEY,
            report_id bigint,
            operation_date date NOT NULL,
            report_from date,
            report_to date,
            create_date timestamptz,
            nm_id bigint,
            vendor_code text,
            sku text,
            title text,
            subject_name text,
            brand_name text,
            doc_type_name text,
            seller_oper_name text,
            quantity numeric NOT NULL DEFAULT 0,
            retail_amount numeric NOT NULL DEFAULT 0,
            for_pay numeric NOT NULL DEFAULT 0,
            ppvz_sales_commission numeric NOT NULL DEFAULT 0,
            acquiring_fee numeric NOT NULL DEFAULT 0,
            delivery_service numeric NOT NULL DEFAULT 0,
            paid_storage numeric NOT NULL DEFAULT 0,
            paid_acceptance numeric NOT NULL DEFAULT 0,
            deduction numeric NOT NULL DEFAULT 0,
            penalty numeric NOT NULL DEFAULT 0,
            additional_payment numeric NOT NULL DEFAULT 0,
            cashback_amount numeric NOT NULL DEFAULT 0,
            rebill_logistic_cost numeric NOT NULL DEFAULT 0,
            raw_payload jsonb NOT NULL,
            captured_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_wb_finance_lines_date
            ON public.wb_finance_lines(operation_date);
        CREATE INDEX IF NOT EXISTS idx_wb_finance_lines_product_date
            ON public.wb_finance_lines(nm_id, operation_date);

        CREATE TABLE IF NOT EXISTS public.wb_inventory_history_daily_csv (
            snapshot_date date NOT NULL,
            entity_key text NOT NULL,
            nm_id bigint,
            vendor_code text,
            warehouse_name text,
            quantity numeric,
            source_report_id uuid NOT NULL,
            raw_row jsonb NOT NULL,
            imported_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (snapshot_date, entity_key)
        );
        CREATE INDEX IF NOT EXISTS idx_wb_inventory_history_nm_date
            ON public.wb_inventory_history_daily_csv(nm_id, snapshot_date);

        CREATE TABLE IF NOT EXISTS public.km_wb_extended_api_runs (
            run_id bigserial PRIMARY KEY,
            step text NOT NULL,
            date_from date,
            date_to date,
            requests_count integer NOT NULL DEFAULT 0,
            rows_count integer NOT NULL DEFAULT 0,
            status text NOT NULL,
            error text,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        );
        """
    )
    cur.execute(COMMUNICATION_MIGRATION.read_text(encoding="utf-8"))


def rebuild_ad_search_view(cur: Any) -> None:
    """Expose normalized WB search-cluster advertising rows for BI reports."""
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_wb_ad_search_query_daily")
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_ad_search_query_daily AS
        WITH campaign_rows AS (
            SELECT DISTINCT ON ((payload->>'id')::bigint)
                (payload->>'id')::bigint AS advert_id,
                coalesce(nullif(payload#>>'{settings,name}', ''), 'Кампания ' || (payload->>'id')) AS campaign_name,
                coalesce(nullif(payload->>'bid_type', ''), 'unknown') AS bid_type,
                coalesce(nullif(payload#>>'{settings,payment_type}', ''), 'unknown') AS payment_type,
                coalesce((payload->>'status')::integer IN (9, 11), false) AS is_active,
                payload AS campaign_payload
            FROM public.wb_api_entities
            WHERE source_key = 'promotion.campaigns'
              AND nullif(payload->>'id', '') IS NOT NULL
            ORDER BY (payload->>'id')::bigint, captured_at DESC, entity_key DESC
        ),
        campaign_products AS (
            SELECT
                c.advert_id,
                (item->>'nm_id')::bigint AS nm_id,
                nullif(item#>>'{bids_kopecks,search}', '')::numeric AS bid_kopecks
            FROM campaign_rows c
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(c.campaign_payload->'nm_settings') = 'array'
                    THEN c.campaign_payload->'nm_settings'
                    ELSE '[]'::jsonb
                END
            ) item
            WHERE nullif(item->>'nm_id', '') IS NOT NULL
        ),
        product_rows AS (
            SELECT DISTINCT ON ((payload->>'nmID')::bigint)
                (payload->>'nmID')::bigint AS nm_id,
                coalesce(nullif(payload->>'title', ''), 'WB ' || (payload->>'nmID')) AS product_name,
                coalesce(nullif(payload->>'vendorCode', ''), '') AS seller_article
            FROM public.wb_api_entities
            WHERE source_key = 'content.cards'
              AND nullif(payload->>'nmID', '') IS NOT NULL
            ORDER BY (payload->>'nmID')::bigint, captured_at DESC, entity_key DESC
        )
        SELECT
            coalesce(e.record_date, (e.payload->>'date')::date) AS report_date,
            (e.payload->>'advertId')::bigint AS advert_id,
            coalesce(c.campaign_name, 'Кампания ' || (e.payload->>'advertId')) AS campaign_name,
            (e.payload->>'nmId')::bigint AS nm_id,
            coalesce(p.product_name, 'WB ' || (e.payload->>'nmId')) AS product_name,
            coalesce(p.seller_article, '') AS seller_article,
            coalesce(nullif(e.payload->>'normQuery', ''), 'Без поисковой фразы') AS norm_query,
            coalesce(c.payment_type, 'unknown') AS payment_type,
            coalesce(c.bid_type, 'unknown') AS bid_type,
            0::bigint AS views_qty,
            coalesce(nullif(e.payload->>'clicks', '')::numeric, 0)::bigint AS clicks_qty,
            coalesce(nullif(e.payload->>'atbs', '')::numeric, 0)::bigint AS atb_qty,
            coalesce(nullif(e.payload->>'orders', '')::numeric, 0)::bigint AS orders_qty,
            coalesce(nullif(e.payload->>'shks', '')::numeric, 0)::bigint AS ordered_units_qty,
            coalesce(nullif(e.payload->>'spend', '')::numeric, 0)::numeric AS spend_rub,
            nullif(e.payload->>'avgPos', '')::numeric AS avg_position,
            cp.bid_kopecks,
            false AS is_excluded,
            c.is_active
        FROM public.wb_api_entities e
        LEFT JOIN campaign_rows c ON c.advert_id = (e.payload->>'advertId')::bigint
        LEFT JOIN campaign_products cp
          ON cp.advert_id = (e.payload->>'advertId')::bigint
         AND cp.nm_id = (e.payload->>'nmId')::bigint
        LEFT JOIN product_rows p ON p.nm_id = (e.payload->>'nmId')::bigint
        WHERE e.source_key = 'promotion.normquery_stats'
          AND nullif(e.payload->>'advertId', '') IS NOT NULL
          AND nullif(e.payload->>'nmId', '') IS NOT NULL
        WITH DATA
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_mv_wb_ad_search_period "
        "ON public.mv_wb_ad_search_query_daily (report_date, advert_id, nm_id)"
    )


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "cards", "items", "orders", "stocks", "adverts"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            for nested_key in ("items", "cards", "products", "groups"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    return []


def count_payload_rows(payload: Any) -> int:
    rows = rows_from_payload(payload)
    if rows:
        return len(rows)
    if isinstance(payload, dict):
        return 1
    return len(payload) if isinstance(payload, list) else 0


def save_snapshot(
    cur: Any,
    source_key: str,
    payload: Any,
    period_from: date | None,
    period_to: date | None,
    status: int,
    request_meta: dict[str, Any],
) -> int:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    row_count = count_payload_rows(payload)
    cur.execute(
        """
        INSERT INTO public.wb_api_source_snapshots (
            source_key, period_from, period_to, http_status, row_count,
            request_meta, payload, payload_sha256
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            source_key, period_from, period_to, status, row_count,
            Json(request_meta), Json(payload), hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        ),
    )
    return row_count


def first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def parse_record_date(row: dict[str, Any]) -> date | None:
    value = first_value(
        row, "date", "dt", "rrDate", "lastChangeDate", "saleDate",
        "orderDate", "createdAt", "createdDate",
    )
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def entity_key(row: dict[str, Any], index: int) -> str:
    norm_query = first_value(row, "normQuery", "norm_query")
    advert_id = first_value(row, "advertId", "advert_id")
    nm_id = first_value(row, "nmId", "nm_id", "nmID")
    record_date = first_value(row, "date", "dt")
    if norm_query not in (None, "") and advert_id not in (None, "") and nm_id not in (None, ""):
        phrase_hash = hashlib.sha256(str(norm_query).encode("utf-8")).hexdigest()[:16]
        return f"{advert_id}:{nm_id}:{record_date or 'all'}:{phrase_hash}"
    value = first_value(
        row, "rrdId", "srid", "saleID", "reportId", "id", "advertId", "nmID", "nmId",
        "warehouseId", "supplierArticle", "vendorCode", "barcode",
    )
    if value not in (None, ""):
        suffix = first_value(row, "date", "dt", "lastChangeDate", "warehouseName", "techSize")
        return f"{value}:{suffix}" if suffix not in (None, "") else str(value)
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return f"row-{index}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def upsert_entities(cur: Any, source_key: str, rows: list[dict[str, Any]], *, replace: bool) -> int:
    if replace:
        cur.execute("DELETE FROM public.wb_api_entities WHERE source_key = %s", (source_key,))
    value_by_key = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        key = entity_key(row, index)
        value_by_key[key] = (source_key, key, parse_record_date(row), Json(row))
    values = list(value_by_key.values())
    if values:
        execute_values(
            cur,
            """
            INSERT INTO public.wb_api_entities (source_key, entity_key, record_date, payload)
            VALUES %s
            ON CONFLICT (source_key, entity_key) DO UPDATE SET
                record_date = EXCLUDED.record_date,
                captured_at = now(),
                payload = EXCLUDED.payload
            """,
            values,
            page_size=1000,
        )
    return len(values)


def promotion_daily_entities(rows: list[dict[str, Any]]) -> dict[str, tuple[date, dict[str, Any]]]:
    """Нормализует fullstats в одну сущность на кампанию и календарный день.

    WB возвращает один campaign-блок с массивом ``days``. Ключ только по advertId
    затирал предыдущие 31-дневные окна, поэтому здесь дата является частью ключа.
    """
    values: dict[str, tuple[date, dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        advert_id = first_value(row, "advertId", "advert_id")
        days = row.get("days") if isinstance(row.get("days"), list) else []
        for day_payload in days:
            if not isinstance(day_payload, dict):
                continue
            record_date = parse_record_date(day_payload)
            if advert_id in (None, "") or record_date is None:
                continue
            payload = {**row, "days": [day_payload]}
            values[f"{advert_id}:{record_date.isoformat()}"] = (record_date, payload)
    return values


def replace_promotion_fullstats(
    cur: Any,
    rows: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
    overwrite: bool,
) -> tuple[int, int]:
    """Мигрирует legacy-блоки и атомарно обновляет дневные fullstats."""
    cur.execute(
        "SELECT payload FROM public.wb_api_entities WHERE source_key = %s ORDER BY captured_at",
        ("promotion.fullstats",),
    )
    existing_rows = []
    for (payload,) in cur.fetchall():
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            existing_rows.append(payload)
    values = promotion_daily_entities(existing_rows)
    overwritten = 0
    if overwrite:
        period_keys = [
            key for key, (record_date, _payload) in values.items()
            if date_from <= record_date <= date_to
        ]
        overwritten = len(period_keys)
        for key in period_keys:
            values.pop(key, None)
    values.update(promotion_daily_entities(rows))
    cur.execute("DELETE FROM public.wb_api_entities WHERE source_key = %s", ("promotion.fullstats",))
    insert_values = [
        ("promotion.fullstats", key, record_date, Json(payload))
        for key, (record_date, payload) in values.items()
    ]
    if insert_values:
        execute_values(
            cur,
            """
            INSERT INTO public.wb_api_entities (source_key, entity_key, record_date, payload)
            VALUES %s
            ON CONFLICT (source_key, entity_key) DO UPDATE SET
                record_date = EXCLUDED.record_date,
                captured_at = now(),
                payload = EXCLUDED.payload
            """,
            insert_values,
            page_size=1000,
        )
    return len(promotion_daily_entities(rows)), overwritten


def delete_entity_period(cur: Any, source_key: str, date_from: date, date_to: date) -> int:
    cur.execute(
        """
        DELETE FROM public.wb_api_entities
        WHERE source_key = %s AND record_date BETWEEN %s AND %s
        """,
        (source_key, date_from, date_to),
    )
    return cur.rowcount


def wb_numeric(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value in (None, ""):
        return "0"
    try:
        return str(float(str(value).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return "0"


def wb_optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wb_optional_date(row: dict[str, Any], key: str) -> date | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def wb_optional_datetime(row: dict[str, Any], key: str) -> datetime | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def import_wb_finance_lines(cur: Any, detail_rows: list[dict[str, Any]]) -> int:
    values: list[tuple[Any, ...]] = []
    for row in detail_rows:
        rrd_id = wb_optional_int(row, "rrdId")
        operation_date = wb_optional_date(row, "rrDate")
        if rrd_id is None or operation_date is None:
            continue
        values.append((
            rrd_id,
            wb_optional_int(row, "reportId"),
            operation_date,
            wb_optional_date(row, "dateFrom"),
            wb_optional_date(row, "dateTo"),
            wb_optional_datetime(row, "createDate"),
            wb_optional_int(row, "nmId"),
            str(row.get("vendorCode") or ""),
            str(row.get("sku") or ""),
            str(row.get("title") or ""),
            str(row.get("subjectName") or ""),
            str(row.get("brandName") or ""),
            str(row.get("docTypeName") or ""),
            str(row.get("sellerOperName") or ""),
            wb_numeric(row, "quantity"),
            wb_numeric(row, "retailAmount"),
            wb_numeric(row, "forPay"),
            wb_numeric(row, "ppvzSalesCommission"),
            wb_numeric(row, "acquiringFee"),
            wb_numeric(row, "deliveryService"),
            wb_numeric(row, "paidStorage"),
            wb_numeric(row, "paidAcceptance"),
            wb_numeric(row, "deduction"),
            wb_numeric(row, "penalty"),
            wb_numeric(row, "additionalPayment"),
            wb_numeric(row, "cashbackAmount"),
            wb_numeric(row, "rebillLogisticCost"),
            Json(row),
        ))
    if values:
        execute_values(
            cur,
            """
            INSERT INTO public.wb_finance_lines (
                rrd_id, report_id, operation_date, report_from, report_to, create_date,
                nm_id, vendor_code, sku, title, subject_name, brand_name,
                doc_type_name, seller_oper_name, quantity, retail_amount, for_pay,
                ppvz_sales_commission, acquiring_fee, delivery_service, paid_storage,
                paid_acceptance, deduction, penalty, additional_payment,
                cashback_amount, rebill_logistic_cost, raw_payload
            ) VALUES %s
            ON CONFLICT (rrd_id) DO UPDATE SET
                report_id = EXCLUDED.report_id,
                operation_date = EXCLUDED.operation_date,
                report_from = EXCLUDED.report_from,
                report_to = EXCLUDED.report_to,
                create_date = EXCLUDED.create_date,
                nm_id = EXCLUDED.nm_id,
                vendor_code = EXCLUDED.vendor_code,
                sku = EXCLUDED.sku,
                title = EXCLUDED.title,
                subject_name = EXCLUDED.subject_name,
                brand_name = EXCLUDED.brand_name,
                doc_type_name = EXCLUDED.doc_type_name,
                seller_oper_name = EXCLUDED.seller_oper_name,
                quantity = EXCLUDED.quantity,
                retail_amount = EXCLUDED.retail_amount,
                for_pay = EXCLUDED.for_pay,
                ppvz_sales_commission = EXCLUDED.ppvz_sales_commission,
                acquiring_fee = EXCLUDED.acquiring_fee,
                delivery_service = EXCLUDED.delivery_service,
                paid_storage = EXCLUDED.paid_storage,
                paid_acceptance = EXCLUDED.paid_acceptance,
                deduction = EXCLUDED.deduction,
                penalty = EXCLUDED.penalty,
                additional_payment = EXCLUDED.additional_payment,
                cashback_amount = EXCLUDED.cashback_amount,
                rebill_logistic_cost = EXCLUDED.rebill_logistic_cost,
                raw_payload = EXCLUDED.raw_payload,
                captured_at = now()
            """,
            values,
            page_size=1000,
        )
    return len(values)


def fetch_wb_finance_details(
    token: str,
    date_from: date,
    date_to: date,
    *,
    period: str = "daily",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if period not in {"daily", "weekly"}:
        raise ValueError(f"Некорректный период финансового отчёта WB: {period}")
    all_rows: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    cursor = 0
    requests = 0
    limit = 100000
    started = time.monotonic()
    print(
        f"ПЛАН: детали реализации WB | режим={period} | период {date_from}..{date_to} | "
        f"страница до {limit:,} строк | лимит 1 запрос/мин | пагинация по rrdId",
        flush=True,
    )
    while True:
        body = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "limit": limit,
            "rrdId": cursor,
            "period": period,
        }
        payload, status, _ = api_request(
            "https://finance-api.wildberries.ru",
            "/api/finance/v1/sales-reports/detailed",
            token,
            method="POST",
            body=body,
            request_label="детали отчётов реализации",
        )
        requests += 1
        page = rows_from_payload(payload)
        pages.append({"payload": payload, "status": status, "request": body})
        all_rows.extend(page)
        elapsed = time.monotonic() - started
        speed = len(all_rows) / elapsed if elapsed > 0 else 0
        print(
            f"ПРОГРЕСС: запрос {requests} | период {date_from}..{date_to} | "
            f"rrdId={cursor} | пакет={len(page):,} | накоплено={len(all_rows):,} | "
            f"скорость={speed:,.1f} строк/с | elapsed={duration(elapsed)} | errors=0",
            flush=True,
        )
        if status == 204 or not page or len(page) < limit:
            break
        next_cursor = wb_optional_int(page[-1], "rrdId")
        if next_cursor is None or next_cursor <= cursor:
            raise RuntimeError("WB Finance вернул полную страницу без корректного следующего rrdId")
        cursor = next_cursor
    return all_rows, pages, requests


def fetch_content_cards(token: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    cursor: dict[str, Any] = {"limit": 100}
    requests = 0
    while True:
        requests += 1
        body = {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}}
        payload, _, _ = api_request(
            "https://content-api.wildberries.ru", "/content/v2/get/cards/list",
            token, method="POST", body=body, request_label="карточки товаров",
        )
        page = rows_from_payload(payload)
        rows.extend(page)
        print(f"ПРОГРЕСС: Content request={requests} | batch={len(page)} | accumulated={len(rows)}", flush=True)
        response_cursor = payload.get("cursor", {}) if isinstance(payload, dict) else {}
        if len(page) < 100 or not response_cursor.get("updatedAt") or not response_cursor.get("nmID"):
            break
        cursor = {"limit": 100, "updatedAt": response_cursor["updatedAt"], "nmID": response_cursor["nmID"]}
    return rows, requests


def communication_windows(date_from: date, date_to: date, days: int = 30) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = date_to
    while cursor >= date_from:
        start = max(date_from, cursor - timedelta(days=max(1, days) - 1))
        windows.append((start, cursor))
        cursor = start - timedelta(days=1)
    return windows


def communication_query(window_from: date, window_to: date, **values: Any) -> str:
    start = datetime(window_from.year, window_from.month, window_from.day, tzinfo=timezone.utc)
    end = datetime(window_to.year, window_to.month, window_to.day, 23, 59, 59, tzinfo=timezone.utc)
    return urlencode({
        **values,
        "dateFrom": int(start.timestamp()),
        "dateTo": int(end.timestamp()),
    })


def communication_rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    rows = data.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def fetch_question_window(
    token: str,
    window_from: date,
    window_to: date,
    answered: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    query = communication_query(
        window_from,
        window_to,
        isAnswered=str(answered).lower(),
        take=10000,
        skip=0,
        order="dateDesc",
    )
    payload, status, _ = api_request(
        "https://feedbacks-api.wildberries.ru",
        f"/api/v1/questions?{query}",
        token,
        request_label=f"вопросы {'отвеченные' if answered else 'неотвеченные'} {window_from}..{window_to}",
    )
    rows = communication_rows(payload, "questions")
    page = {
        "source_key": "communication.questions",
        "payload": payload,
        "status": status,
        "date_from": window_from,
        "date_to": window_to,
        "meta": {"method": "GET", "isAnswered": answered, "take": 10000, "skip": 0},
    }
    print(
        f"ПРОГРЕСС: вопросы | период={window_from}..{window_to} | "
        f"answered={str(answered).lower()} | batch={len(rows):,}",
        flush=True,
    )
    if len(rows) < 10000:
        return rows, [page], 1
    if window_from >= window_to:
        raise RuntimeError(
            f"WB вернул лимит 10 000 вопросов за {window_from}; безопасная полная выгрузка за день невозможна"
        )
    midpoint = window_from + timedelta(days=(window_to - window_from).days // 2)
    newer_rows, newer_pages, newer_requests = fetch_question_window(
        token, midpoint + timedelta(days=1), window_to, answered,
    )
    older_rows, older_pages, older_requests = fetch_question_window(token, window_from, midpoint, answered)
    return newer_rows + older_rows, [page, *newer_pages, *older_pages], 1 + newer_requests + older_requests


def fetch_wb_communication(token: str, date_from: date, date_to: date) -> dict[str, Any]:
    host = "https://feedbacks-api.wildberries.ru"
    pages: list[dict[str, Any]] = []
    feedback_by_id: dict[str, dict[str, Any]] = {}
    question_by_id: dict[str, dict[str, Any]] = {}
    requests = 0

    new_events, status, _ = api_request(
        host,
        "/api/v1/new-feedbacks-questions",
        token,
        request_label="проверка новых отзывов и вопросов",
    )
    requests += 1
    pages.append({
        "source_key": "communication.new_events",
        "payload": new_events,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "meta": {"method": "GET"},
    })

    archive_skip = 0
    while True:
        archive_query = urlencode({"take": 5000, "skip": archive_skip})
        archive_payload, archive_status, _ = api_request(
            host,
            f"/api/v1/feedbacks/archive?{archive_query}",
            token,
            request_label="архив отзывов",
        )
        requests += 1
        archive_rows = communication_rows(archive_payload, "feedbacks")
        pages.append({
            "source_key": "communication.feedbacks_archive",
            "payload": archive_payload,
            "status": archive_status,
            "date_from": None,
            "date_to": None,
            "meta": {"method": "GET", "take": 5000, "skip": archive_skip},
        })
        accepted = 0
        for row in archive_rows:
            record_date = parse_record_date(row)
            if record_date is None or not (date_from <= record_date <= date_to):
                continue
            source_id = str(row.get("id") or "").strip()
            if source_id:
                feedback_by_id[source_id] = row
                accepted += 1
        print(
            f"ПРОГРЕСС: архив отзывов | skip={archive_skip:,} | batch={len(archive_rows):,} | "
            f"в периоде={accepted:,} | accumulated={len(feedback_by_id):,}",
            flush=True,
        )
        if len(archive_rows) < 5000:
            break
        archive_skip += len(archive_rows)
        if archive_skip > 195000:
            raise RuntimeError("Архив WB превысил безопасный предел пагинации 200 000 отзывов")

    for window_from, window_to in communication_windows(date_from, date_to):
        for answered in (False, True):
            skip = 0
            while True:
                query = communication_query(
                    window_from,
                    window_to,
                    isAnswered=str(answered).lower(),
                    take=5000,
                    skip=skip,
                    order="dateDesc",
                )
                payload, page_status, _ = api_request(
                    host,
                    f"/api/v1/feedbacks?{query}",
                    token,
                    request_label=f"отзывы {'отвеченные' if answered else 'неотвеченные'} {window_from}..{window_to}",
                )
                requests += 1
                rows = communication_rows(payload, "feedbacks")
                pages.append({
                    "source_key": "communication.feedbacks",
                    "payload": payload,
                    "status": page_status,
                    "date_from": window_from,
                    "date_to": window_to,
                    "meta": {"method": "GET", "isAnswered": answered, "take": 5000, "skip": skip},
                })
                for row in rows:
                    source_id = str(row.get("id") or "").strip()
                    if source_id:
                        feedback_by_id[source_id] = row
                print(
                    f"ПРОГРЕСС: отзывы | период={window_from}..{window_to} | "
                    f"answered={str(answered).lower()} | skip={skip:,} | batch={len(rows):,} | "
                    f"accumulated={len(feedback_by_id):,}",
                    flush=True,
                )
                if len(rows) < 5000:
                    break
                skip += len(rows)
                if skip > 195000:
                    raise RuntimeError("WB отзывы превысили безопасный предел пагинации 200 000 строк в одном окне")

        for answered in (False, True):
            rows, question_pages, question_requests = fetch_question_window(
                token, window_from, window_to, answered,
            )
            requests += question_requests
            pages.extend(question_pages)
            for row in rows:
                source_id = str(row.get("id") or "").strip()
                if source_id:
                    question_by_id[source_id] = row

    return {
        "feedbacks": list(feedback_by_id.values()),
        "questions": list(question_by_id.values()),
        "pages": pages,
        "requests": requests,
    }


def communication_product(row: dict[str, Any]) -> tuple[str, dict[str, str]]:
    details = row.get("productDetails") if isinstance(row.get("productDetails"), dict) else {}
    product_id = str(first_value(details, "nmId", "nmID") or first_value(row, "nmId", "nmID") or "").strip()
    return product_id, {
        "seller_article": str(first_value(details, "supplierArticle", "vendorCode") or "").strip(),
        "product_name": str(first_value(details, "productName", "imtName") or "").strip(),
        "category_name": str(first_value(details, "subjectName", "subject") or "").strip(),
        "brand": str(first_value(details, "brandName", "brand") or "").strip(),
    }


def communication_answer(row: dict[str, Any]) -> str:
    answer = row.get("answer")
    return str(answer.get("text") if isinstance(answer, dict) else answer or "").strip()


def upsert_marketplace_reviews(cur: Any, rows: list[dict[str, Any]]) -> int:
    synced_at = datetime.now(timezone.utc)
    values = []
    for row in rows:
        source_id = str(row.get("id") or "").strip()
        if not source_id:
            continue
        product_id, product = communication_product(row)
        answer_text = communication_answer(row)
        photos = row.get("photoLinks")
        values.append((
            f"wb:{source_id}", source_id, "wb", product_id,
            product["seller_article"], product["product_name"], product["category_name"], product["brand"],
            parse_record_date(row), first_value(row, "productValuation", "valuation"),
            str(row.get("text") or "").strip(), str(row.get("pros") or "").strip(),
            str(row.get("cons") or "").strip(), answer_text, True,
            bool(answer_text or row.get("isAnswered")),
            bool(photos) if isinstance(photos, list) else None, None,
            str(row.get("orderStatus") or "").strip(), "wb_seller_api", synced_at,
        ))
    if values:
        execute_values(
            cur,
            """
            INSERT INTO public.marketplace_reviews (
                review_key, source_review_id, marketplace, product_id, seller_article,
                product_name, category_name, brand, review_date, rating, review_text,
                pros, cons, answer_text, answer_available, answered, has_photo, likes,
                order_status, source, source_synced_at
            ) VALUES %s
            ON CONFLICT (review_key) DO UPDATE SET
                product_id = EXCLUDED.product_id,
                seller_article = EXCLUDED.seller_article,
                product_name = EXCLUDED.product_name,
                category_name = EXCLUDED.category_name,
                brand = EXCLUDED.brand,
                review_date = EXCLUDED.review_date,
                rating = EXCLUDED.rating,
                review_text = EXCLUDED.review_text,
                pros = EXCLUDED.pros,
                cons = EXCLUDED.cons,
                answer_text = EXCLUDED.answer_text,
                answer_available = EXCLUDED.answer_available,
                answered = EXCLUDED.answered,
                has_photo = EXCLUDED.has_photo,
                order_status = EXCLUDED.order_status,
                source_synced_at = EXCLUDED.source_synced_at
            """,
            values,
            page_size=1000,
        )
    return len(values)


def upsert_marketplace_questions(cur: Any, rows: list[dict[str, Any]]) -> int:
    synced_at = datetime.now(timezone.utc)
    values = []
    for row in rows:
        source_id = str(row.get("id") or "").strip()
        if not source_id:
            continue
        product_id, product = communication_product(row)
        answer_text = communication_answer(row)
        values.append((
            f"wb:{source_id}", source_id, "wb", product_id,
            product["seller_article"], product["product_name"], product["category_name"], product["brand"],
            parse_record_date(row), str(row.get("text") or "").strip(), answer_text,
            bool(answer_text or row.get("isAnswered")), row.get("wasViewed"),
            "wb_seller_api", synced_at, Json(row),
        ))
    if values:
        execute_values(
            cur,
            """
            INSERT INTO public.marketplace_questions (
                question_key, source_question_id, marketplace, product_id, seller_article,
                product_name, category_name, brand, question_date, question_text,
                answer_text, answered, was_viewed, source, source_synced_at, raw_payload
            ) VALUES %s
            ON CONFLICT (question_key) DO UPDATE SET
                product_id = EXCLUDED.product_id,
                seller_article = EXCLUDED.seller_article,
                product_name = EXCLUDED.product_name,
                category_name = EXCLUDED.category_name,
                brand = EXCLUDED.brand,
                question_date = EXCLUDED.question_date,
                question_text = EXCLUDED.question_text,
                answer_text = EXCLUDED.answer_text,
                answered = EXCLUDED.answered,
                was_viewed = EXCLUDED.was_viewed,
                source_synced_at = EXCLUDED.source_synced_at,
                raw_payload = EXCLUDED.raw_payload
            """,
            values,
            page_size=1000,
        )
    return len(values)


def extract_campaign_ids(
    payload: Any,
    *,
    statuses: set[int] | None = None,
) -> list[int]:
    ids: list[int] = []
    if isinstance(payload, dict):
        groups = payload.get("adverts") or payload.get("data") or []
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                if statuses is not None:
                    try:
                        group_status = int(group.get("status"))
                    except (TypeError, ValueError):
                        continue
                    if group_status not in statuses:
                        continue
                values = group.get("advert_list") or group.get("advertList") or group.get("ids") or []
                for value in values if isinstance(values, list) else []:
                    raw = value.get("advertId", value.get("id")) if isinstance(value, dict) else value
                    try:
                        ids.append(int(raw))
                    except (TypeError, ValueError):
                        pass
    return sorted(set(ids))


def campaign_status_by_id(payload: Any) -> dict[int, int]:
    statuses: dict[int, int] = {}
    if not isinstance(payload, dict):
        return statuses
    groups = payload.get("adverts") or payload.get("data") or []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        try:
            status = int(group.get("status"))
        except (TypeError, ValueError):
            continue
        values = group.get("advert_list") or group.get("advertList") or group.get("ids") or []
        for value in values if isinstance(values, list) else []:
            raw = value.get("advertId", value.get("id")) if isinstance(value, dict) else value
            try:
                statuses[int(raw)] = status
            except (TypeError, ValueError):
                continue
    return statuses


def promotion_campaign_id(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    raw = first_value(row, "id", "advertId", "advert_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def promotion_metadata_fingerprint(row: dict[str, Any]) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def promotion_fullstats_has_activity(payload: Any) -> bool:
    metric_keys = {"views", "clicks", "sum", "atbs", "orders", "shks", "sum_price"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in metric_keys:
                try:
                    if float(value or 0) != 0:
                        return True
                except (TypeError, ValueError):
                    pass
            if isinstance(value, (dict, list)) and promotion_fullstats_has_activity(value):
                return True
    elif isinstance(payload, list):
        return any(promotion_fullstats_has_activity(value) for value in payload)
    return False


def load_promotion_selection_history(
    cur: Any,
    activity_from: date,
) -> tuple[dict[int, dict[str, Any]], dict[int, date]]:
    cur.execute(
        "SELECT payload FROM public.wb_api_entities WHERE source_key = %s",
        ("promotion.campaigns",),
    )
    previous_campaigns: dict[int, dict[str, Any]] = {}
    for (payload,) in cur.fetchall():
        if isinstance(payload, str):
            payload = json.loads(payload)
        campaign_id = promotion_campaign_id(payload)
        if campaign_id is not None and isinstance(payload, dict):
            previous_campaigns[campaign_id] = payload

    cur.execute(
        """
        SELECT record_date, payload
        FROM public.wb_api_entities
        WHERE source_key = %s AND record_date >= %s
        """,
        ("promotion.fullstats", activity_from),
    )
    recent_activity: dict[int, date] = {}
    for record_date, payload in cur.fetchall():
        if isinstance(payload, str):
            payload = json.loads(payload)
        campaign_id = promotion_campaign_id(payload)
        if campaign_id is None or not isinstance(record_date, date):
            continue
        if promotion_fullstats_has_activity(payload):
            recent_activity[campaign_id] = max(record_date, recent_activity.get(campaign_id, record_date))
    return previous_campaigns, recent_activity


def load_promotion_checkpoint(
    cur: Any,
    date_from: date,
    date_to: date,
    checkpoint_key: str,
) -> Any | None:
    if cur is None:
        return None
    cur.execute(
        """
        SELECT payload
        FROM public.wb_promotion_sync_checkpoints
        WHERE date_from = %s AND date_to = %s AND checkpoint_key = %s
          AND fetched_at >= now() - make_interval(days => %s)
        """,
        (date_from, date_to, checkpoint_key, PROMOTION_CHECKPOINT_RETENTION_DAYS),
    )
    row = cur.fetchone()
    return row[0] if row else None


def save_promotion_checkpoint(
    conn: Any,
    cur: Any,
    date_from: date,
    date_to: date,
    checkpoint_key: str,
    payload: Any,
) -> None:
    if cur is None:
        return
    cur.execute(
        """
        INSERT INTO public.wb_promotion_sync_checkpoints (
            date_from, date_to, checkpoint_key, payload, fetched_at
        ) VALUES (%s, %s, %s, %s::jsonb, now())
        ON CONFLICT (date_from, date_to, checkpoint_key) DO UPDATE SET
            payload = EXCLUDED.payload,
            fetched_at = now()
        """,
        (date_from, date_to, checkpoint_key, json.dumps(payload, ensure_ascii=False, default=str)),
    )
    if conn is not None:
        conn.commit()


def purge_promotion_checkpoints(cur: Any) -> None:
    if cur is None:
        return
    cur.execute(
        """
        DELETE FROM public.wb_promotion_sync_checkpoints
        WHERE fetched_at < now() - make_interval(days => %s)
        """,
        (PROMOTION_CHECKPOINT_RETENTION_DAYS,),
    )


def promotion_batch_checkpoint_key(prefix: str, window_start: date, window_end: date, ids: list[int]) -> str:
    raw = ",".join(str(value) for value in ids)
    return f"{prefix}:{window_start.isoformat()}:{window_end.isoformat()}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def select_promotion_fullstats_campaigns(
    candidate_ids: list[int],
    adverts: list[dict[str, Any]],
    statuses: dict[int, int],
    previous_campaigns: dict[int, dict[str, Any]],
    recent_activity: dict[int, date],
    *,
    date_to: date,
    full_scan: bool = False,
    lookback_days: int = PROMOTION_ATTRIBUTION_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Select expensive fullstats requests without losing active or changed campaigns."""
    activity_from = date_to - timedelta(days=max(1, lookback_days) - 1)
    current_by_id = {
        campaign_id: row
        for row in adverts
        if (campaign_id := promotion_campaign_id(row)) is not None
    }
    decisions: list[dict[str, Any]] = []
    for campaign_id in sorted(set(candidate_ids)):
        row = current_by_id.get(campaign_id)
        raw_status = first_value(row or {}, "status")
        try:
            status = int(raw_status) if raw_status not in (None, "") else statuses.get(campaign_id)
        except (TypeError, ValueError):
            status = statuses.get(campaign_id)
        previous = previous_campaigns.get(campaign_id)
        last_activity = recent_activity.get(campaign_id)
        selected = True
        if full_scan:
            reason = "full_scan"
        elif status == 9:
            reason = "status_9_active"
        elif status == 11:
            reason = "status_11_paused"
        elif row is None:
            reason = "metadata_missing"
        elif previous is None:
            reason = "new_campaign"
        elif promotion_metadata_fingerprint(row) != promotion_metadata_fingerprint(previous):
            reason = "metadata_changed"
        elif last_activity is not None and last_activity >= activity_from:
            reason = "recent_fullstats_activity"
        else:
            selected = False
            reason = "status_7_unchanged_without_recent_activity"
        decisions.append({
            "advertId": campaign_id,
            "date": date_to.isoformat(),
            "status": status,
            "selected": selected,
            "reason": reason,
            "last_activity_date": last_activity.isoformat() if last_activity else None,
        })

    priority = {
        "status_9_active": 0,
        "status_11_paused": 1,
        "new_campaign": 2,
        "metadata_changed": 3,
        "recent_fullstats_activity": 4,
        "metadata_missing": 5,
        "full_scan": 6,
    }
    selected_ids = [
        item["advertId"]
        for item in sorted(
            (item for item in decisions if item["selected"]),
            key=lambda item: (priority.get(item["reason"], 99), -item["advertId"]),
        )
    ]
    reason_counts: dict[str, int] = {}
    for item in decisions:
        reason_counts[item["reason"]] = reason_counts.get(item["reason"], 0) + 1
    return {
        "mode": "full_scan" if full_scan else "incremental_preselection",
        "lookback_days": max(1, lookback_days),
        "activity_from": activity_from.isoformat(),
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
        "skipped_count": len(decisions) - len(selected_ids),
        "reason_counts": dict(sorted(reason_counts.items())),
        "campaigns": decisions,
    }


def extract_normquery_items(
    adverts: list[dict[str, Any]],
    *,
    include_inactive_campaigns: bool = False,
) -> list[dict[str, int]]:
    items: set[tuple[int, int]] = set()
    for advert in adverts:
        if not isinstance(advert, dict):
            continue
        if not include_inactive_campaigns:
            try:
                if int(advert.get("status")) != 9:
                    continue
            except (TypeError, ValueError):
                continue
        settings = advert.get("settings") if isinstance(advert.get("settings"), dict) else {}
        payment_type = str(settings.get("payment_type") or settings.get("paymentType") or "").lower()
        if payment_type not in {"cpm", "cpc"}:
            continue
        placements = settings.get("placements") if isinstance(settings.get("placements"), dict) else {}
        if placements and placements.get("search") is False:
            continue
        advert_id = first_value(advert, "id", "advertId", "advert_id")
        try:
            parsed_advert_id = int(advert_id)
        except (TypeError, ValueError):
            continue
        nm_settings = advert.get("nm_settings") or advert.get("nmSettings") or []
        for item in nm_settings if isinstance(nm_settings, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                items.add((parsed_advert_id, int(first_value(item, "nm_id", "nmId", "nmID"))))
            except (TypeError, ValueError):
                continue
    return [{"advertId": advert_id, "nmId": nm_id} for advert_id, nm_id in sorted(items)]


def flatten_normquery_stats(payload: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        advert_id = first_value(item, "advertId", "advert_id")
        nm_id = first_value(item, "nmId", "nm_id", "nmID")
        daily_stats = item.get("dailyStats") or item.get("daily_stats") or []
        for daily in daily_stats if isinstance(daily_stats, list) else []:
            if not isinstance(daily, dict):
                continue
            stat = daily.get("stat") if isinstance(daily.get("stat"), dict) else {}
            row = {"advertId": advert_id, "nmId": nm_id, "date": daily.get("date")}
            row.update(stat)
            if first_value(row, "normQuery", "norm_query") not in (None, ""):
                result.append(row)
    return result


def fetch_normquery_stats(
    token: str,
    adverts: list[dict[str, Any]],
    date_from: date,
    date_to: date,
    *,
    include_inactive_campaigns: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    items = extract_normquery_items(
        adverts,
        include_inactive_campaigns=include_inactive_campaigns,
    )
    if not items:
        print("ПРОГРЕСС: поисковые фразы рекламы | подходящих кампаний и товаров нет", flush=True)
        return [], [], 0

    windows: list[tuple[date, date]] = []
    window_end = date_to
    while window_end >= date_from:
        window_start = max(date_from, window_end - timedelta(days=NORMQUERY_WINDOW_DAYS - 1))
        windows.append((window_start, window_end))
        window_end = window_start - timedelta(days=1)
    batches = [
        items[index:index + NORMQUERY_ITEM_BATCH_SIZE]
        for index in range(0, len(items), NORMQUERY_ITEM_BATCH_SIZE)
    ]
    total_requests = len(windows) * len(batches)
    print(
        f"ПЛАН: поисковые фразы рекламы | пар кампания+SKU={len(items)} | "
        f"окон={len(windows)} по {NORMQUERY_WINDOW_DAYS} дней | запросов={total_requests} | "
        f"кампании={'все' if include_inactive_campaigns else 'только активные'} | "
        "лимит 10 запросов/мин, интервал около 6 сек | порядок от свежих дат к старым",
        flush=True,
    )

    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    request_number = 0
    for window_start, window_end in windows:
        for batch in batches:
            request_number += 1
            body = {
                "from": window_start.isoformat(),
                "to": window_end.isoformat(),
                "items": batch,
            }
            payload, status, _ = api_request(
                "https://advert-api.wildberries.ru",
                "/adv/v1/normquery/stats",
                token,
                method="POST",
                body=body,
                request_label=f"поисковые фразы рекламы {window_start}..{window_end}",
            )
            page_rows = flatten_normquery_stats(payload)
            rows.extend(page_rows)
            pages.append({"payload": payload, "status": status, "request": body})
            elapsed = time.monotonic() - started
            eta = (elapsed / request_number) * (total_requests - request_number) if request_number else 0
            print(
                f"ПРОГРЕСС: {request_number}/{total_requests} "
                f"({request_number / total_requests:.1%}) | {window_start}..{window_end} | "
                f"batch={len(batch)} | rows={len(page_rows)} | accumulated={len(rows)} | "
                f"errors=0 | elapsed={duration(elapsed)} | ETA={duration(eta)}",
                flush=True,
            )
    return rows, pages, request_number


def fetch_promotion(
    token: str,
    date_from: date,
    date_to: date,
    *,
    include_inactive_campaigns: bool = False,
    previous_campaigns: dict[int, dict[str, Any]] | None = None,
    recent_fullstats_activity: dict[int, date] | None = None,
    conn: Any = None,
    cur: Any = None,
    resume: bool = False,
) -> tuple[dict[str, Any], int]:
    requests = 0
    count_payload, _, _ = api_request(
        "https://advert-api.wildberries.ru", "/adv/v1/promotion/count", token,
        request_label="список рекламных кампаний",
    )
    requests += 1
    if cur is not None:
        save_promotion_checkpoint(conn, cur, date_from, date_to, "count", count_payload)
    all_ids = extract_campaign_ids(count_payload)
    # /adv/v3/fullstats принимает только завершённые, активные и приостановленные кампании.
    ids = extract_campaign_ids(count_payload, statuses={7, 9, 11})
    skipped_ids = len(all_ids) - len(ids)
    detail_batches = [ids[index:index + 50] for index in range(0, len(ids), 50)]
    print(
        f"ПЛАН: WB реклама | кампаний всего={len(all_ids)} | кандидатов 7/9/11={len(ids)} | "
        f"прочие статусы={skipped_ids} | пакетов метаданных максимум={len(detail_batches)} | "
        "после метаданных: 9 и 11 всегда, 7 только новая/изменённая/с недавней активностью | "
        "до 50 кампаний/запрос | метаданные: до 5 запросов/сек | "
        "fullstats: максимум 31 день, интервал 21 сек с запасом",
        flush=True,
    )
    cached_metadata = (
        load_promotion_checkpoint(cur, date_from, date_to, "metadata") if resume else None
    )
    metadata_by_id: dict[int, dict[str, Any]] = {}
    if isinstance(cached_metadata, dict):
        for raw_id, row in cached_metadata.items():
            campaign_id = promotion_campaign_id(row)
            if campaign_id is None:
                try:
                    campaign_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
            if isinstance(row, dict):
                metadata_by_id[campaign_id] = row
    if resume and metadata_by_id:
        print(
            f"ПРОГРЕСС: WB реклама | metadata checkpoint={len(metadata_by_id)} | "
            f"к проверке={len(ids) - len(set(ids) & set(metadata_by_id))}",
            flush=True,
        )
    missing_ids = [campaign_id for campaign_id in ids if campaign_id not in metadata_by_id]
    detail_batches = [missing_ids[index:index + 50] for index in range(0, len(missing_ids), 50)]
    adverts: list[dict[str, Any]] = []
    fullstats: list[Any] = []
    detail_started = time.monotonic()
    for batch_number, chunk in enumerate(detail_batches, start=1):
        path = "/api/advert/v2/adverts?" + urlencode({"ids": ",".join(map(str, chunk))})
        payload, _, _ = api_request("https://advert-api.wildberries.ru", path, token, request_label="кампании")
        requests += 1
        page_rows = rows_from_payload(payload)
        for row in page_rows:
            campaign_id = promotion_campaign_id(row)
            if campaign_id is not None:
                metadata_by_id[campaign_id] = row
        if cur is not None:
            save_promotion_checkpoint(
                conn,
                cur,
                date_from,
                date_to,
                "metadata",
                {str(campaign_id): row for campaign_id, row in metadata_by_id.items()},
            )
        elapsed = time.monotonic() - detail_started
        eta = (elapsed / batch_number) * (len(detail_batches) - batch_number)
        print(
            f"ПРОГРЕСС: метаданные кампаний {batch_number}/{len(detail_batches)} "
            f"({batch_number / len(detail_batches):.1%}) | batch={len(chunk)} | "
            f"получено={len(page_rows)} | накоплено={len(adverts)} | "
            f"elapsed={duration(elapsed)} | ETA={duration(eta)}",
            flush=True,
        )
    adverts = [metadata_by_id[campaign_id] for campaign_id in ids if campaign_id in metadata_by_id]
    selection = select_promotion_fullstats_campaigns(
        ids,
        adverts,
        campaign_status_by_id(count_payload),
        previous_campaigns or {},
        recent_fullstats_activity or {},
        date_to=date_to,
        full_scan=include_inactive_campaigns,
    )
    selected_ids = selection["selected_ids"]
    stats_batches = [selected_ids[index:index + 50] for index in range(0, len(selected_ids), 50)]
    windows: list[tuple[date, date]] = []
    window_start = date_from
    while window_start <= date_to and selected_ids:
        window_end = min(date_to, window_start + timedelta(days=30))
        windows.append((window_start, window_end))
        window_start = window_end + timedelta(days=1)
    stats_requests_total = len(stats_batches) * len(windows)
    print(
        f"ПЛАН: отбор fullstats | режим={selection['mode']} | "
        f"выбрано={selection['selected_count']}/{len(ids)} | пропущено={selection['skipped_count']} | "
        f"причины={json.dumps(selection['reason_counts'], ensure_ascii=False, sort_keys=True)} | "
        f"окон={len(windows)} | пакетов={len(stats_batches)} | запросов={stats_requests_total}",
        flush=True,
    )
    stats_started = time.monotonic()
    stats_request_number = 0
    for window_start, window_end in windows:
        for chunk in stats_batches:
            stats_request_number += 1
            checkpoint_key = promotion_batch_checkpoint_key(
                "fullstats", window_start, window_end, chunk,
            )
            cached_rows = (
                load_promotion_checkpoint(cur, date_from, date_to, checkpoint_key)
                if resume else None
            )
            if cached_rows is not None:
                page_rows = [row for row in cached_rows if isinstance(row, dict)]
                fullstats.extend(page_rows)
                print(
                    f"ПРОГРЕСС: статистика рекламы {stats_request_number}/{stats_requests_total} "
                    f"({stats_request_number / stats_requests_total:.1%}) | "
                    f"{window_start}..{window_end} | batch={len(chunk)} | "
                    f"получено={len(page_rows)} | накоплено={len(fullstats)} | "
                    "источник=чекпоинт | errors=0",
                    flush=True,
                )
                continue
            query = urlencode({
                "ids": ",".join(map(str, chunk)),
                "beginDate": window_start.isoformat(),
                "endDate": window_end.isoformat(),
            })
            payload, _, _ = api_request(
                "https://advert-api.wildberries.ru", f"/adv/v3/fullstats?{query}", token,
                request_label=f"реклама {window_start}..{window_end}",
            )
            requests += 1
            candidate_rows = payload if isinstance(payload, list) else [payload]
            page_rows = [row for row in candidate_rows if isinstance(row, dict)]
            fullstats.extend(page_rows)
            if cur is not None:
                save_promotion_checkpoint(
                    conn, cur, date_from, date_to, checkpoint_key, page_rows,
                )
            elapsed = time.monotonic() - stats_started
            eta = (
                (elapsed / stats_request_number) * (stats_requests_total - stats_request_number)
                if stats_request_number else 0
            )
            print(
                f"ПРОГРЕСС: статистика рекламы {stats_request_number}/{stats_requests_total} "
                f"({stats_request_number / stats_requests_total:.1%}) | "
                f"{window_start}..{window_end} | batch={len(chunk)} | "
                f"получено={len(page_rows)} | накоплено={len(fullstats)} | "
                f"elapsed={duration(elapsed)} | ETA={duration(eta)}",
                flush=True,
            )
    normquery_stats, normquery_pages, normquery_requests = fetch_normquery_stats(
        token,
        adverts,
        date_from,
        date_to,
        include_inactive_campaigns=include_inactive_campaigns,
    )
    requests += normquery_requests
    return {
        "count": count_payload,
        "campaigns": adverts,
        "fullstats": fullstats,
        "normquery_stats": normquery_stats,
        "normquery_pages": normquery_pages,
        "selection": selection,
    }, requests


def fetch_search_analytics(
    token: str,
    date_from: date,
    date_to: date,
) -> tuple[Any, int, dict[str, Any], list[tuple[Any, int, dict[str, Any], list[dict[str, Any]]]], int, date]:
    period_from = max(date_from, date_to - timedelta(days=6))
    common_body = {
        "currentPeriod": {"start": period_from.isoformat(), "end": date_to.isoformat()},
        "nmIds": [], "subjectIds": [], "brandNames": [], "tagIds": [],
        "positionCluster": "all",
        "includeSubstitutedSKUs": True,
        "includeSearchTexts": True,
    }
    print(
        f"ПЛАН: WB поисковая аналитика | период {period_from}..{date_to} | "
        f"методов 2 | детализация по товарам страницами до {SEARCH_DETAILS_PAGE_SIZE} | "
        "лимит аккаунта 3 запроса/мин | ожидаемые паузы около 20 сек",
        flush=True,
    )
    main_body = {
        **common_body,
        "orderBy": {"field": "orders", "mode": "desc"},
        "limit": SEARCH_DETAILS_PAGE_SIZE,
        "offset": 0,
    }
    main_payload, main_status, _ = api_request(
        "https://seller-analytics-api.wildberries.ru", "/api/v2/search-report/report",
        token, method="POST", body=main_body, request_label="поисковая аналитика · сводка",
    )
    requests = 1
    main_rows = rows_from_payload(main_payload)
    print(
        f"ПРОГРЕСС: запрос {requests} | поисковая сводка | groups={len(main_rows)} | errors=0",
        flush=True,
    )

    detail_pages: list[tuple[Any, int, dict[str, Any], list[dict[str, Any]]]] = []
    accumulated = 0
    offset = 0
    while True:
        detail_body = {
            "currentPeriod": common_body["currentPeriod"],
            "nmIds": [],
            "positionCluster": "all",
            "includeSubstitutedSKUs": True,
            "includeSearchTexts": True,
            "orderBy": {"field": "orders", "mode": "desc"},
            "limit": SEARCH_DETAILS_PAGE_SIZE,
            "offset": offset,
        }
        payload, status, _ = api_request(
            "https://seller-analytics-api.wildberries.ru", "/api/v2/search-report/table/details",
            token, method="POST", body=detail_body,
            request_label=f"поисковая аналитика · товары offset={offset}",
        )
        requests += 1
        page = rows_from_payload(payload)
        accumulated += len(page)
        detail_pages.append((payload, status, detail_body, page))
        print(
            f"ПРОГРЕСС: запрос {requests} | товарная детализация offset={offset} | "
            f"batch={len(page)} | accumulated={accumulated} | errors=0",
            flush=True,
        )
        if len(page) < SEARCH_DETAILS_PAGE_SIZE:
            break
        offset += SEARCH_DETAILS_PAGE_SIZE
    return main_payload, main_status, main_body, detail_pages, requests, period_from


def fetch_stock_history_csv(token: str, date_from: date, date_to: date) -> tuple[list[dict[str, str]], uuid.UUID, int, Path]:
    report_id = uuid.uuid4()
    body = {
        "id": str(report_id),
        "reportType": "STOCK_HISTORY_DAILY_CSV",
        "userReportName": f"{CLIENT_KEY} stock history {date_from} - {date_to}",
        "params": {
            "nmIDs": [], "subjectIds": [], "brandNames": [], "tagIds": [],
            "currentPeriod": {"start": date_from.isoformat(), "end": date_to.isoformat()},
            "stockType": "",
            "timezone": "Europe/Moscow", "skipDeletedNm": False,
        },
    }
    _, _, _ = api_request(
        "https://seller-analytics-api.wildberries.ru", "/api/v2/nm-report/downloads",
        token, method="POST", body=body, request_label="создание истории остатков",
    )
    requests = 1
    status = ""
    for poll in range(1, 19):
        sleep_progress(20, f"история остатков | ожидание готовности | poll={poll}")
        query = urlencode({"filter[downloadIds][]": str(report_id)})
        payload, _, _ = api_request(
            "https://seller-analytics-api.wildberries.ru", f"/api/v2/nm-report/downloads?{query}",
            token, request_label="статус истории остатков",
        )
        requests += 1
        status_rows = rows_from_payload(payload)
        status = str(status_rows[0].get("status") if status_rows else "").upper()
        print(f"ПРОГРЕСС: Stock CSV poll={poll}/18 | status={status or 'UNKNOWN'}", flush=True)
        if status == "SUCCESS":
            break
        if status == "FAILED":
            raise RuntimeError(f"WB не сформировал историю остатков: report_id={report_id}")
    if status != "SUCCESS":
        raise RuntimeError(f"История остатков не готова за 6 минут: report_id={report_id}")
    archive, _, _ = api_request(
        "https://seller-analytics-api.wildberries.ru", f"/api/v2/nm-report/downloads/file/{report_id}",
        token, binary=True, request_label="скачивание истории остатков",
    )
    requests += 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f"wb_stock_history_{date_from}_{date_to}_{report_id}.zip"
    zip_path.write_bytes(archive)
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for name in bundle.namelist():
            if not name.lower().endswith(".csv"):
                continue
            raw = bundle.read(name)
            text = raw.decode("utf-8-sig", "replace")
            delimiter = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
            rows.extend(dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter))
    return rows, report_id, requests, zip_path


def normalized_csv_value(row: dict[str, str], *names: str) -> str | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return None


def parse_stock_history_date(value: object) -> date | None:
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], date_format).date()
        except ValueError:
            continue
    return None


def parse_stock_history_quantity(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def stock_history_values(
    rows: list[dict[str, str]],
    report_id: uuid.UUID,
) -> list[tuple[Any, ...]]:
    values: list[tuple[Any, ...]] = []
    for index, row in enumerate(rows, start=1):
        raw_nm = normalized_csv_value(row, "nmID", "nmId", "Артикул WB")
        try:
            nm_id = int(raw_nm) if raw_nm else None
        except ValueError:
            nm_id = None
        vendor_code = normalized_csv_value(row, "vendorCode", "supplierArticle", "Артикул продавца")
        warehouse = normalized_csv_value(row, "warehouseName", "officeName", "Склад")
        chrt_id = normalized_csv_value(row, "chrtID", "chrtId")
        size_name = normalized_csv_value(row, "sizeName", "Размер")
        key_seed = f"{nm_id}|{vendor_code}|{chrt_id}|{size_name}|{warehouse}"
        if not any((nm_id, vendor_code, chrt_id, size_name, warehouse)):
            key_seed = f"source-row-{index}"
        key = hashlib.sha256(key_seed.encode("utf-8")).hexdigest()[:32]

        raw_date = normalized_csv_value(row, "date", "dt", "snapshotDate", "Дата")
        snapshot_date = parse_stock_history_date(raw_date)
        if snapshot_date:
            quantity = parse_stock_history_quantity(
                normalized_csv_value(row, "quantity", "stockCount", "stocksCount", "Остаток", "Количество")
            )
            values.append((snapshot_date, key, nm_id, vendor_code, warehouse, quantity, str(report_id), Json(row)))
            continue

        # STOCK_HISTORY_DAILY_CSV is a wide matrix: one product/size/warehouse
        # per row and one quantity column for every report date.
        for column, raw_quantity in row.items():
            snapshot_date = parse_stock_history_date(column)
            if snapshot_date is None:
                continue
            quantity = parse_stock_history_quantity(raw_quantity)
            values.append((snapshot_date, key, nm_id, vendor_code, warehouse, quantity, str(report_id), Json(row)))
    return values


def import_stock_history_values(cur: Any, values: list[tuple[Any, ...]]) -> int:
    if values:
        execute_values(
            cur,
            """
            INSERT INTO public.wb_inventory_history_daily_csv (
                snapshot_date, entity_key, nm_id, vendor_code, warehouse_name,
                quantity, source_report_id, raw_row
            ) VALUES %s
            ON CONFLICT (snapshot_date, entity_key) DO UPDATE SET
                nm_id = EXCLUDED.nm_id,
                vendor_code = EXCLUDED.vendor_code,
                warehouse_name = EXCLUDED.warehouse_name,
                quantity = EXCLUDED.quantity,
                source_report_id = EXCLUDED.source_report_id,
                raw_row = EXCLUDED.raw_row,
                imported_at = now()
            """,
            values,
            page_size=1000,
        )
    return len(values)


def import_stock_history_csv(cur: Any, rows: list[dict[str, str]], report_id: uuid.UUID) -> int:
    return import_stock_history_values(cur, stock_history_values(rows, report_id))


def run_step(
    step: str,
    date_from: date,
    date_to: date,
    *,
    overwrite: bool = False,
    include_inactive_campaigns: bool = False,
    finance_period: str = "daily",
    resume: bool = False,
) -> dict[str, Any]:
    if finance_period not in {"daily", "weekly"}:
        raise ValueError(f"Некорректный период финансового отчёта WB: {finance_period}")
    token = token_value()
    started = time.monotonic()
    requests = 0
    rows = 0
    overwritten_rows = 0
    outputs: list[str] = []
    run_status = "ok"
    run_error = ""
    with db_connection() as conn, conn.cursor() as cur:
        ensure_schema(cur)
        if step == "content":
            payload, requests = fetch_content_cards(token)
            rows = save_snapshot(cur, "content.cards", payload, None, None, 200, {"method": "POST"})
            upsert_entities(cur, "content.cards", payload, replace=True)
        elif step == "statistics":
            collected = 0
            for key, path in (
                ("statistics.orders", "/api/v1/supplier/orders"),
                ("statistics.sales", "/api/v1/supplier/sales"),

            ):
                query = urlencode({"dateFrom": f"{date_from.isoformat()}T00:00:00", "flag": 0})
                payload, status, _ = api_request(
                    "https://statistics-api.wildberries.ru", f"{path}?{query}", token,
                    request_label=key,
                )
                requests += 1
                page = rows_from_payload(payload)
                save_snapshot(cur, key, payload, date_from, date_to, status, {"method": "GET", "flag": 0})
                if overwrite:
                    overwritten_rows += delete_entity_period(cur, key, date_from, date_to)
                upsert_entities(cur, key, page, replace=False)
                collected += len(page)
                print(f"ПРОГРЕСС: {key} | rows={len(page)} | accumulated={collected}", flush=True)
            rows = collected
        elif step == "marketplace":
            collected = 0
            for key, path in (
                ("marketplace.warehouses", "/api/v3/warehouses"),
                ("marketplace.orders_new", "/api/v3/orders/new"),
            ):
                payload, status, _ = api_request("https://marketplace-api.wildberries.ru", path, token, request_label=key)
                requests += 1
                page = rows_from_payload(payload)
                save_snapshot(cur, key, payload, None, None, status, {"method": "GET"})
                upsert_entities(cur, key, page, replace=True)
                collected += len(page)
            rows = collected
        elif step == "promotion":
            activity_from = date_to - timedelta(days=PROMOTION_ATTRIBUTION_LOOKBACK_DAYS - 1)
            previous_campaigns, recent_fullstats_activity = load_promotion_selection_history(cur, activity_from)
            payload, requests = fetch_promotion(
                token,
                date_from,
                date_to,
                include_inactive_campaigns=include_inactive_campaigns,
                previous_campaigns=previous_campaigns,
                recent_fullstats_activity=recent_fullstats_activity,
                conn=conn,
                cur=cur,
                resume=resume,
            )
            campaigns = payload.get("campaigns", [])
            fullstats = payload.get("fullstats", [])
            normquery_stats = payload.get("normquery_stats", [])
            bundle = {key: value for key, value in payload.items() if key != "normquery_pages"}
            save_snapshot(cur, "promotion.bundle", bundle, date_from, date_to, 200, {"method": "GET+POST"})
            upsert_entities(cur, "promotion.campaigns", campaigns, replace=True)
            upsert_entities(
                cur,
                "promotion.selection",
                payload.get("selection", {}).get("campaigns", []),
                replace=True,
            )
            fullstats_days, fullstats_overwritten = replace_promotion_fullstats(
                cur,
                fullstats,
                date_from=date_from,
                date_to=date_to,
                overwrite=overwrite,
            )
            overwritten_rows += fullstats_overwritten
            for page in payload.get("normquery_pages", []):
                save_snapshot(
                    cur,
                    "promotion.normquery_stats",
                    page["payload"],
                    date.fromisoformat(page["request"]["from"]),
                    date.fromisoformat(page["request"]["to"]),
                    page["status"],
                    page["request"],
                )
            if normquery_stats:
                if overwrite:
                    overwritten_rows += delete_entity_period(
                        cur, "promotion.normquery_stats", date_from, date_to,
                    )
                upsert_entities(cur, "promotion.normquery_stats", normquery_stats, replace=False)
            rebuild_ad_search_view(cur)
            # The product-ad dashboard reads this materialized view, not the
            # JSONB API entities directly. Rebuild it in the same transaction
            # so a successful targeted promotion sync is immediately visible.
            rebuild_wb_advertising_view(cur)
            purge_promotion_checkpoints(cur)
            rows = len(campaigns) + len(fullstats) + len(normquery_stats)
            outputs.extend([
                f"promotion.campaigns={len(campaigns)}",
                f"promotion.fullstats_campaign_blocks={len(fullstats)}",
                f"promotion.fullstats_days={fullstats_days}",
                f"promotion.normquery_stats={len(normquery_stats)}",
                f"promotion.fullstats_selected={payload.get('selection', {}).get('selected_count', 0)}",
                f"promotion.fullstats_skipped={payload.get('selection', {}).get('skipped_count', 0)}",
            ])
        elif step == "analytics":
            if os.environ.get("WB_JAM_STATUS", "").strip().lower() == "inactive":
                run_status = "blocked"
                run_error = "WB Jam subscription is inactive"
                outputs.append("skipped: WB Jam inactive (preflight)")
                print(
                    "ПРОГРЕСС: analytics | нет доступа: preflight подтвердил неактивную подписку WB Jam | requests=0 | errors=0",
                    flush=True,
                )
            else:
                try:
                    main_payload, main_status, main_body, detail_pages, requests, period_from = fetch_search_analytics(
                        token, date_from, date_to,
                    )
                except RuntimeError as exc:
                    detail = str(exc)
                    if "WB HTTP 403" not in detail or "Jam subscription" not in detail:
                        raise
                    requests = 1
                    run_status = "blocked"
                    run_error = "WB API reports Jam subscription unavailable for this seller account"
                    outputs.append("blocked: analytics.search_report requires WB Jam")
                    print(
                        "ПРОГРЕСС: analytics | нет доступа: WB сообщает, что Jam недоступен для этого кабинета | errors=0",
                        flush=True,
                    )
                else:
                    main_rows = rows_from_payload(main_payload)
                    save_snapshot(
                        cur, "analytics.search_report", main_payload,
                        period_from, date_to, main_status, main_body,
                    )
                    if main_rows:
                        upsert_entities(cur, "analytics.search_report", main_rows, replace=True)

                    detail_rows: list[dict[str, Any]] = []
                    for payload, status, body, page in detail_pages:
                        save_snapshot(
                            cur, "analytics.search_details", payload,
                            period_from, date_to, status, body,
                        )
                        detail_rows.extend(page)
                    if detail_rows:
                        upsert_entities(cur, "analytics.search_details", detail_rows, replace=True)
                    rows = len(main_rows) + len(detail_rows)
                    outputs.extend([
                        f"analytics.search_report groups={len(main_rows)}",
                        f"analytics.search_details products={len(detail_rows)}",
                    ])
        elif step == "communication":
            try:
                communication = fetch_wb_communication(token, date_from, date_to)
            except RuntimeError as exc:
                if "WB HTTP 403" not in str(exc):
                    raise
                requests = 1
                run_status = "blocked"
                run_error = "WB token lacks Feedbacks and Questions category"
                outputs.append("skipped: WB token lacks Feedbacks and Questions category")
                print(
                    "ПРЕДУПРЕЖДЕНИЕ: отзывы и вопросы WB недоступны | "
                    "у токена нет категории «Вопросы и отзывы» | requests=1 | rows=0",
                    flush=True,
                )
            else:
                requests = int(communication["requests"])
                feedbacks = communication["feedbacks"]
                questions = communication["questions"]
                for page in communication["pages"]:
                    save_snapshot(
                        cur,
                        page["source_key"],
                        page["payload"],
                        page["date_from"],
                        page["date_to"],
                        page["status"],
                        page["meta"],
                    )
                if overwrite:
                    for source_key in ("communication.feedbacks", "communication.questions"):
                        overwritten_rows += delete_entity_period(cur, source_key, date_from, date_to)
                    cur.execute(
                        "DELETE FROM public.marketplace_reviews "
                        "WHERE marketplace = 'wb' AND review_date BETWEEN %s AND %s",
                        (date_from, date_to),
                    )
                    overwritten_rows += cur.rowcount
                    cur.execute(
                        "DELETE FROM public.marketplace_questions "
                        "WHERE marketplace = 'wb' AND question_date BETWEEN %s AND %s",
                        (date_from, date_to),
                    )
                    overwritten_rows += cur.rowcount
                upsert_entities(cur, "communication.feedbacks", feedbacks, replace=False)
                upsert_entities(cur, "communication.questions", questions, replace=False)
                review_rows = upsert_marketplace_reviews(cur, feedbacks)
                question_rows = upsert_marketplace_questions(cur, questions)
                rows = review_rows + question_rows
                outputs.extend([
                    f"communication.feedbacks={review_rows}",
                    f"communication.questions={question_rows}",
                ])
                print(
                    f"ПРОГРЕСС: отзывы и вопросы | отзывы={review_rows:,} | "
                    f"вопросы={question_rows:,} | requests={requests} | errors=0",
                    flush=True,
                )
        elif step == "finance":
            balance, status, _ = api_request(
                "https://finance-api.wildberries.ru", "/api/v1/account/balance", token,
                request_label="финансовый баланс",
            )
            requests += 1
            rows += save_snapshot(cur, "finance.balance", balance, None, None, status, {"method": "GET"})
            report_body = {
                "dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat(),
                "limit": 1000, "offset": 0, "period": finance_period,
            }
            reports, status, _ = api_request(
                "https://finance-api.wildberries.ru", "/api/finance/v1/sales-reports/list",
                token, method="POST", body=report_body, request_label="список отчётов реализации",
            )
            requests += 1
            report_rows = rows_from_payload(reports)
            rows += save_snapshot(cur, "finance.sales_reports", reports, date_from, date_to, status, report_body)
            if overwrite:
                overwritten_rows += delete_entity_period(cur, "finance.sales_reports", date_from, date_to)
            upsert_entities(cur, "finance.sales_reports", report_rows, replace=False)
            if not report_rows:
                run_status = "source_not_ready"
                run_error = (
                    f"WB ещё не сформировал {finance_period}-отчёт реализации "
                    f"за {date_from}..{date_to}"
                )
                outputs.append("WB_FINANCE_SOURCE_NOT_READY")
                print(
                    f"ОГРАНИЧЕНИЕ: {run_error} | WB_FINANCE_SOURCE_NOT_READY | "
                    f"requests={requests} | rows=0 | retry=no",
                    flush=True,
                )
            else:
                detail_rows, detail_pages, detail_requests = fetch_wb_finance_details(
                    token, date_from, date_to, period=finance_period,
                )
                requests += detail_requests
                for page in detail_pages:
                    save_snapshot(
                        cur,
                        "finance.sales_report_details",
                        page["payload"],
                        date_from,
                        date_to,
                        page["status"],
                        page["request"],
                    )
                normalized_rows = 0
                if detail_rows:
                    if overwrite:
                        overwritten_rows += delete_entity_period(
                            cur, "finance.sales_report_details", date_from, date_to,
                        )
                        cur.execute(
                            "DELETE FROM public.wb_finance_lines "
                            "WHERE operation_date BETWEEN %s AND %s",
                            (date_from, date_to),
                        )
                        overwritten_rows += cur.rowcount
                    upsert_entities(
                        cur, "finance.sales_report_details", detail_rows, replace=False,
                    )
                    normalized_rows = import_wb_finance_lines(cur, detail_rows)
                    rows += normalized_rows
                else:
                    outputs.append("finance.sales_report_details=complete_empty")
                    print(
                        "ПРОГРЕСС: отчёт WB сформирован, но детальных операций нет; "
                        "период считается проверенным, прежние строки не удалены",
                        flush=True,
                    )
                outputs.extend([
                    f"finance.period={finance_period}",
                    f"finance.sales_reports={len(report_rows)}",
                    f"finance.sales_report_details={normalized_rows}",
                ])
                print(
                    f"ПРОГРЕСС: финансы WB завершены | режим={finance_period} | "
                    f"реестр={len(report_rows):,} | детальные строки={normalized_rows:,} | "
                    f"requests={requests} | errors=0",
                    flush=True,
                )
        elif step == "stock_history":
            csv_rows, report_id, requests, zip_path = fetch_stock_history_csv(token, date_from, date_to)
            parsed_values = stock_history_values(csv_rows, report_id)
            if not parsed_values:
                raise RuntimeError(
                    "История остатков WB пустая: CSV не содержит распознаваемых дневных остатков; "
                    "предыдущие данные сохранены"
                )
            if overwrite:
                cur.execute(
                    "DELETE FROM public.wb_inventory_history_daily_csv WHERE snapshot_date BETWEEN %s AND %s",
                    (date_from, date_to),
                )
                overwritten_rows += cur.rowcount
            rows = import_stock_history_values(cur, parsed_values)
            published_rows = inventory_history.publish_wb_stock_history_csv(
                conn,
                date_from,
                date_to,
                replace=overwrite,
            )
            print(
                f"ПРОГРЕСС: история остатков опубликована в витрину | "
                f"source_rows={rows:,} | dashboard_rows={published_rows:,} | errors=0",
                flush=True,
            )
            outputs.append(str(zip_path))
            save_snapshot(
                cur, "analytics.stock_history_daily_csv",
                {
                    "report_id": str(report_id),
                    "file": str(zip_path),
                    "source_rows": len(csv_rows),
                    "rows": rows,
                    "dashboard_rows": published_rows,
                },
                date_from, date_to, 200, {"reportType": "STOCK_HISTORY_DAILY_CSV"},
            )
        else:
            raise ValueError(f"Неизвестный шаг: {step}")
        if overwrite:
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: режим перезаписи | шаг={step} | "
                f"удалено прежних строк периода={overwritten_rows:,} | ручные данные не затронуты",
                flush=True,
            )
        cur.execute(
            """
            INSERT INTO public.km_wb_extended_api_runs (
                step, date_from, date_to, requests_count, rows_count, status, error, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NULLIF(%s, ''), now())
            """,
            (
                "finance_weekly" if step == "finance" and finance_period == "weekly" else step,
                date_from, date_to, requests, rows, run_status, run_error,
            ),
        )
        conn.commit()
    return {
        "step": step,
        "rows": rows,
        "requests": requests,
        "status": run_status,
        "error": run_error,
        "elapsed": time.monotonic() - started,
        "outputs": outputs,
    }


def main() -> None:
    configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--step",
        choices=("content", "statistics", "marketplace", "promotion", "analytics", "communication", "finance", "stock_history", "all"),
        default="all",
    )
    parser.add_argument("--date-from", default=(date.today() - timedelta(days=89)).isoformat())
    parser.add_argument("--date-to", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Продолжить WB promotion из сохранённых checkpoint-пакетов",
    )
    parser.add_argument(
        "--finance-period",
        choices=("daily", "weekly"),
        default="daily",
        help="daily для ежедневной догрузки; weekly для отдельного закрывающего недельного отчёта",
    )
    parser.add_argument(
        "--include-inactive-campaigns",
        action="store_true",
        help="Контрольный полный fullstats по всем 7/9/11 и поисковые фразы неактивных кампаний",
    )
    args = parser.parse_args()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    steps = [
        "content", "statistics", "marketplace", "promotion", "analytics",
        "communication", "finance", "stock_history",
    ] if args.step == "all" else [args.step]
    started = time.monotonic()
    print(
        f"ПЛАН: {CLIENT_KEY} WB extended | шагов={len(steps)} | период={date_from}..{date_to} | "
        f"read-only API | storage={TARGET_DB} | resume={'yes' if args.resume else 'no'} | "
        "stock CSV polling=20s до 6 минут",
        flush=True,
    )
    total_rows = 0
    total_requests = 0
    completed = 0
    outputs: list[str] = []
    limitations = 0
    try:
        for index, step in enumerate(steps, start=1):
            elapsed = time.monotonic() - started
            eta = elapsed / completed * (len(steps) - completed) if completed else 0
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({(index - 1) / len(steps) * 100:.1f}%) | {step} | "
                f"rows={total_rows} | requests={total_requests} | errors=0 | elapsed={duration(elapsed)} | ETA={duration(eta)}",
                flush=True,
            )
            result = run_step(
                step,
                date_from,
                date_to,
                overwrite=args.overwrite,
                include_inactive_campaigns=args.include_inactive_campaigns,
                finance_period=args.finance_period,
                resume=args.resume,
            )
            completed += 1
            total_rows += int(result["rows"])
            total_requests += int(result["requests"])
            outputs.extend(result["outputs"])
            if result["status"] == "source_not_ready":
                limitations += 1
            print(
                f"[{index}/{len(steps)}] {step}: imported={result['rows']} | requests={result['requests']} | "
                f"errors=0 | limitations={limitations} | status={result['status']} | "
                f"elapsed={duration(result['elapsed'])}",
                flush=True,
            )
    except Exception as exc:
        print(
            f"ИТОГ: completed={completed}/{len(steps)} | rows={total_rows} | requests={total_requests} | "
            f"errors=1 | partial=yes | elapsed={duration(time.monotonic() - started)} | error={exc}",
            flush=True,
        )
        raise
    print(
        f"ИТОГ: completed={completed}/{len(steps)} | rows={total_rows} | requests={total_requests} | "
        f"errors=0 | limitations={limitations} | partial={'yes' if limitations else 'no'} | "
        f"outputs={outputs or ['PostgreSQL']} | elapsed={duration(time.monotonic() - started)}",
        flush=True,
    )


if __name__ == "__main__":
    main()




