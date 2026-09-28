#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Wildberries Analytics import for an isolated client database.

The script reads a client-specific token, calls only seller analytics endpoints,
and writes only to the selected dashboard database. It deliberately avoids
falling back to another client's WB token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))

import app  # noqa: E402
import inventory_history  # noqa: E402
from build_dashboard_aggregates import INDEX_SQL, ROLLUP_SQL  # noqa: E402
from import_wb_funnel_reports import COLUMNS, DB_COLUMNS  # noqa: E402
from rebuild_sportmaster_wb_base_views import PRODUCT_ABC_SQL  # noqa: E402
from wb_rate_limit import TokenBucketLimiter  # noqa: E402
from wb_advertising_view import rebuild_wb_advertising_view  # noqa: E402


TARGET_DB = os.environ.get("DASHBOARD_DB_NAME", "km_trade_products")
CLIENT_KEY = os.environ.get("DASHBOARD_CLIENT", "km_trade")
CLIENT_SUFFIX = CLIENT_KEY.upper()
TOKEN_ENV = os.environ.get("WB_API_TOKEN_ENV") or (
    "WB_API_TOKEN_KM_TRADE" if CLIENT_KEY == "km_trade" else f"WB_API_TOKEN_{CLIENT_SUFFIX}"
)
ANALYTICS_BASE_URL = "https://seller-analytics-api.wildberries.ru"
FUNNEL_PRODUCTS_PATH = "/api/analytics/v3/sales-funnel/products"
FUNNEL_PATH = "/api/analytics/v3/sales-funnel/products/history"
STOCK_PATH = "/api/v2/stocks-report/products/products"
MAX_FUNNEL_DAYS = 7
FUNNEL_NMID_BATCH_SIZE = 20
MAX_RETRIES = 4
REQUEST_TIMEOUT_SECONDS = 120
STOCK_PAGE_SIZE = 1000
ANALYTICS_LIMITER = TokenBucketLimiter()


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def token_value() -> str:
    values = app.read_app_env_file()
    token = str(os.environ.get(TOKEN_ENV) or values.get(TOKEN_ENV) or "").strip()
    if not token:
        token = str(app.registered_client_credential(CLIENT_KEY, "wb_api_token") or "").strip()
    if not token and CLIENT_KEY == getattr(app, "DEFAULT_CLIENT", "gloria_jeans"):
        fallback_env = app.wb_api_token_env(CLIENT_KEY)
        token = str(os.environ.get(fallback_env) or values.get(fallback_env) or "").strip()
    if not token:
        raise RuntimeError(
            f"Для клиента {CLIENT_KEY} не сохранён WB API токен ({TOKEN_ENV}). "
            "Сохраните токен категории «Аналитика» в Админке или registry credentials."
        )
    return token


def db_connection():
    config = app.read_db_config(CLIENT_KEY)
    config["database"] = TARGET_DB
    config["application_name"] = f"{CLIENT_KEY}_wb_api_sync"
    conn = psycopg2.connect(**config)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual_db = cur.fetchone()[0]
    if actual_db != TARGET_DB:
        conn.close()
        raise RuntimeError(f"Защитная проверка БД: ожидалась {TARGET_DB}, получена {actual_db}")
    return conn


def retry_wait_seconds(exc: HTTPError, attempt: int) -> int:
    raw = exc.headers.get("X-Ratelimit-Retry") or exc.headers.get("Retry-After")
    try:
        return max(1, min(3600, int(float(raw))))
    except (TypeError, ValueError):
        return min(120, 20 * attempt)


def sleep_with_progress(seconds: int, label: str) -> None:
    remaining = max(0, int(seconds))
    while remaining:
        print(f"ЛИМИТ API: {label} | ожидание {remaining}s", flush=True)
        chunk = min(10, remaining)
        time.sleep(chunk)
        remaining -= chunk


def post_json(path: str, token: str, body: dict[str, Any], *, request_no: int, request_total: int) -> Any:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, MAX_RETRIES + 1):
        ANALYTICS_LIMITER.acquire(
            path,
            capacity=3,
            period_seconds=63,
            wait=lambda seconds: sleep_with_progress(
                max(1, int(seconds + 0.999)),
                f"запрос {request_no}/{request_total} | превентивный лимит",
            ),
        )
        request = Request(
            ANALYTICS_BASE_URL + path,
            data=payload,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 429 and attempt < MAX_RETRIES:
                wait = retry_wait_seconds(exc, attempt)
                sleep_with_progress(
                    wait,
                    f"запрос {request_no}/{request_total} | HTTP 429 | попытка {attempt}/{MAX_RETRIES}",
                )
                continue
            if exc.code in {500, 502, 503, 504} and attempt < MAX_RETRIES:
                wait = min(120, 20 * attempt)
                sleep_with_progress(
                    wait,
                    f"запрос {request_no}/{request_total} | HTTP {exc.code} | попытка {attempt}/{MAX_RETRIES}",
                )
                continue
            raise RuntimeError(f"WB Analytics HTTP {exc.code}: {detail}") from exc
        except (TimeoutError, URLError) as exc:
            if attempt < MAX_RETRIES:
                wait = min(120, 20 * attempt)
                sleep_with_progress(
                    wait,
                    f"запрос {request_no}/{request_total} | сеть | попытка {attempt}/{MAX_RETRIES}",
                )
                continue
            raise RuntimeError(f"WB Analytics connection error: {exc}") from exc
    raise RuntimeError("WB Analytics retry limit reached")


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None


def first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return None


def parse_date(value: Any, fallback: date) -> date:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


def payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "products", "cards"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    for key in ("items", "products", "cards"):
        if isinstance(payload.get(key), list):
            return [item for item in payload[key] if isinstance(item, dict)]
    return []


def product_payload(item: dict[str, Any]) -> dict[str, Any]:
    product = item.get("product")
    if isinstance(product, dict):
        return product
    return item


def history_payload(item: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("history", "statistics", "items"):
        rows = item.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def funnel_record(product: dict[str, Any], metric: dict[str, Any], fallback_date: date) -> dict[str, Any]:
    ordered_units = number(first(metric, "orderCount", "ordersCount", "orders"))
    ordered_amount = number(first(metric, "orderSum", "ordersSum", "orderSumRub"))
    bought_units = number(first(metric, "buyoutCount", "buyoutsCount", "buyouts"))
    bought_amount = number(first(metric, "buyoutSum", "buyoutsSum", "buyoutSumRub"))
    cancelled_units = number(first(metric, "cancelCount", "cancelledCount", "cancellations"))
    cancelled_amount = number(first(metric, "cancelSum", "cancelledSum", "cancelSumRub"))
    result: dict[str, Any] = {column: None for column in DB_COLUMNS}
    result.update(
        {
            "seller_article": text(first(product, "vendorCode", "vendor_code", "supplierArticle")),
            "wb_nmid": text(first(product, "nmId", "nmID", "nm_id")),
            "product_name": text(first(product, "title", "name", "productName")),
            "category_name": text(first(product, "subjectName", "subject_name", "categoryName")),
            "brand": text(first(product, "brandName", "brand_name", "brand")),
            "is_deleted": text(first(product, "isDeleted", "deleted")),
            "card_rating": number(first(product, "rating", "cardRating")),
            "review_rating": number(first(product, "feedbackRating", "reviewRating")),
            # The v3 history method exposes card opens, not WB impressions.
            "impressions_total": None,
            "card_visits": number(first(metric, "openCount", "openCardCount", "cardOpenCount")),
            "cart_adds": number(first(metric, "cartCount", "addToCartCount")),
            "favorites_adds": number(first(metric, "addToWishlistCount", "addToWishList")),
            "ordered_units": ordered_units,
            "bought_units": bought_units,
            "cancelled_units": cancelled_units,
            "ordered_amount_rub": ordered_amount,
            "bought_amount_rub": bought_amount,
            "cancelled_amount_rub": cancelled_amount,
            "buyout_pct": number(first(metric, "buyoutPercent", "buyoutPct")),
            "card_to_cart_pct": number(first(metric, "addToCartConversion", "cardToCartConversion")),
            "cart_to_order_pct": number(first(metric, "cartToOrderConversion", "cartToOrderPct")),
            "avg_price_rub": (
                ordered_amount / ordered_units
                if ordered_amount is not None and ordered_units not in (None, 0)
                else None
            ),
        }
    )
    result["report_date"] = parse_date(first(metric, "date", "dt"), fallback_date)
    return result


def parse_funnel_response(payload: Any, fallback_date: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in payload_items(payload):
        product = product_payload(item)
        for metric in history_payload(item):
            record = funnel_record(product, metric, fallback_date)
            if record.get("wb_nmid"):
                records.append(record)
    return records


def stock_record(item: dict[str, Any], snapshot_date: date) -> dict[str, Any] | None:
    product = product_payload(item)
    metrics = item.get("metrics")
    if not isinstance(metrics, dict):
        metrics = product.get("metrics") if isinstance(product.get("metrics"), dict) else {}
    nmid = text(first(product, "nmID", "nmId", "nm_id"))
    if not nmid:
        return None
    current_price = metrics.get("currentPrice")
    if not isinstance(current_price, dict):
        current_price = {}
    return {
        "snapshot_date": snapshot_date,
        "wb_nmid": nmid,
        "seller_article": text(first(product, "vendorCode", "vendor_code", "supplierArticle")),
        "product_name": text(first(product, "title", "name", "productName")),
        "category_name": text(first(product, "subjectName", "subject_name", "categoryName")),
        "brand": text(first(product, "brandName", "brand_name", "brand")),
        "subject_id": integer(first(product, "subjectID", "subjectId", "subject_id")),
        "stock_qty": number(first(metrics, "stockCount", "stocksCount")),
        "stock_amount_rub": number(first(metrics, "stockSum", "stocksSum")),
        "to_customer_qty": number(first(metrics, "toClientCount", "toCustomerCount")),
        "from_customer_qty": number(first(metrics, "fromClientCount", "fromCustomerCount")),
        "orders_qty": number(first(metrics, "ordersCount", "orderCount")),
        "orders_amount_rub": number(first(metrics, "ordersSum", "orderSum")),
        "buyouts_qty": number(first(metrics, "buyoutCount", "buyoutsCount")),
        "buyouts_amount_rub": number(first(metrics, "buyoutSum", "buyoutsSum")),
        "buyout_pct": number(first(metrics, "buyoutPercent", "buyoutPct")),
        "price_min_rub": number(first(current_price, "minPrice", "min")),
        "price_max_rub": number(first(current_price, "maxPrice", "max")),
    }


def parse_stock_response(payload: Any, snapshot_date: date) -> list[dict[str, Any]]:
    rows = []
    for item in payload_items(payload):
        row = stock_record(item, snapshot_date)
        if row:
            rows.append(row)
    return rows


def ensure_schema(cur: Any) -> None:
    numeric_columns = "\n".join(
        f"            {column.db_name} numeric,"
        for column in COLUMNS
        if column.kind == "numeric"
    )
    text_columns = "\n".join(
        f"            {column.db_name} text,"
        for column in COLUMNS
        if column.kind == "text"
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.wb_funnel_daily (
            report_date date NOT NULL,
{text_columns}
{numeric_columns}
            source_file text NOT NULL,
            source_inner_file text,
            source_row_num integer,
            imported_at timestamp without time zone DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS public.wb_funnel_import_files (
            source_file text PRIMARY KEY,
            report_date date,
            rows_imported integer NOT NULL DEFAULT 0,
            file_size_bytes bigint,
            file_mtime timestamp without time zone,
            imported_at timestamp without time zone DEFAULT now(),
            status text NOT NULL DEFAULT 'ok',
            error text
        );
        CREATE TABLE IF NOT EXISTS public.wb_stock_api_current (
            snapshot_date date NOT NULL,
            wb_nmid text PRIMARY KEY,
            seller_article text,
            product_name text,
            category_name text,
            brand text,
            subject_id bigint,
            stock_qty numeric,
            stock_amount_rub numeric,
            to_customer_qty numeric,
            from_customer_qty numeric,
            orders_qty numeric,
            orders_amount_rub numeric,
            buyouts_qty numeric,
            buyouts_amount_rub numeric,
            buyout_pct numeric,
            price_min_rub numeric,
            price_max_rub numeric,
            imported_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS public.wb_funnel_sync_batches (
            date_from date NOT NULL,
            date_to date NOT NULL,
            batch_key text NOT NULL,
            batch_index integer NOT NULL,
            nm_count integer NOT NULL,
            rows_json jsonb NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (date_from, date_to, batch_key)
        );
        CREATE TABLE IF NOT EXISTS public.km_wb_api_runs (
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
    ensure_product_compatibility_schema(cur)
    for query in (
        "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_report_date ON public.wb_funnel_daily(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_nmid ON public.wb_funnel_daily(wb_nmid)",
        "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_seller_article ON public.wb_funnel_daily(seller_article)",
        "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_category ON public.wb_funnel_daily(category_name)",
    ):
        cur.execute(query)


def ensure_product_compatibility_schema(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.categories (
            category_id bigint PRIMARY KEY,
            category_name text
        );
        CREATE TABLE IF NOT EXISTS public.products (
            product_id bigint PRIMARY KEY,
            artikul_wb text,
            artikul_ozon text,
            artikul_prodavtsa text,
            barkod text,
            brend text,
            ves_s_upakovkoy_kg text,
            video text,
            vysota_upakovki text,
            gruppa text,
            data_okonchaniya_deystviya_sertifikata_deklaratsii text,
            data_registratsii_sertifikata_deklaratsii text,
            dlina_upakovki text,
            ikpu text,
            kategoriya_prodavtsa text,
            kod_upakovki text,
            komplektatsiya text,
            naimenovanie text,
            nomer_deklaratsii_sootvetstviya text,
            nomer_sertifikata_sootvetstviya text,
            opisanie text,
            stavka_nds text,
            strana_proizvodstva text,
            foto text,
            shirina_upakovki text,
            category_id bigint,
            seller_category_name text
        );
        CREATE TABLE IF NOT EXISTS public.common_attributes (
            attribute_id bigint PRIMARY KEY,
            attribute_name text
        );
        CREATE TABLE IF NOT EXISTS public.category_attributes (
            category_id bigint NOT NULL,
            attribute_id bigint NOT NULL,
            attribute_name text,
            PRIMARY KEY (category_id, attribute_id)
        );
        CREATE TABLE IF NOT EXISTS public.product_attributes (
            product_id bigint NOT NULL,
            attribute_id bigint NOT NULL,
            value_text text,
            PRIMARY KEY (product_id, attribute_id)
        );
        """
    )


def upsert_products(cur: Any, records: Iterable[dict[str, Any]]) -> int:
    products: dict[str, tuple[Any, ...]] = {}
    categories: dict[int, str | None] = {}
    for record in records:
        nmid = text(record.get("wb_nmid"))
        if not nmid or not nmid.isdigit():
            continue
        category_name = text(record.get("category_name"))
        subject_id = integer(record.get("subject_id"))
        if subject_id is not None:
            categories[subject_id] = category_name
        products[nmid] = (
            int(nmid),
            nmid,
            text(record.get("seller_article")),
            text(record.get("brand")),
            category_name,
            text(record.get("product_name")),
            subject_id,
            category_name,
        )
    if categories:
        execute_values(
            cur,
            """
            INSERT INTO public.categories (category_id, category_name) VALUES %s
            ON CONFLICT (category_id) DO UPDATE SET category_name = EXCLUDED.category_name
            """,
            [(key, value) for key, value in categories.items()],
        )
    if products:
        execute_values(
            cur,
            """
            INSERT INTO public.products (
                product_id, artikul_wb, artikul_prodavtsa, brend,
                kategoriya_prodavtsa, naimenovanie, category_id, seller_category_name
            ) VALUES %s
            ON CONFLICT (product_id) DO UPDATE SET
                artikul_wb = EXCLUDED.artikul_wb,
                artikul_prodavtsa = EXCLUDED.artikul_prodavtsa,
                brend = EXCLUDED.brend,
                kategoriya_prodavtsa = EXCLUDED.kategoriya_prodavtsa,
                naimenovanie = EXCLUDED.naimenovanie,
                category_id = EXCLUDED.category_id,
                seller_category_name = EXCLUDED.seller_category_name
            """,
            list(products.values()),
        )
    return len(products)


def import_funnel(cur: Any, records: list[dict[str, Any]], date_from: date, date_to: date) -> int:
    if not records:
        raise RuntimeError("WB вернул пустую воронку; существующие строки не удалены")
    source_file = f"wb-api:{FUNNEL_PATH}:{date_from.isoformat()}:{date_to.isoformat()}"
    cur.execute(
        "DELETE FROM public.wb_funnel_daily WHERE report_date BETWEEN %s AND %s",
        (date_from, date_to),
    )
    columns = ["report_date", *DB_COLUMNS, "source_file", "source_inner_file", "source_row_num"]
    values = []
    for row_num, record in enumerate(records, start=1):
        values.append(
            (
                record["report_date"],
                *(record.get(column) for column in DB_COLUMNS),
                source_file,
                "seller-analytics-v3",
                row_num,
            )
        )
    execute_values(
        cur,
        f"INSERT INTO public.wb_funnel_daily ({', '.join(columns)}) VALUES %s",
        values,
        page_size=1000,
    )
    cur.execute(
        """
        INSERT INTO public.wb_funnel_import_files (
            source_file, report_date, rows_imported, imported_at, status, error
        ) VALUES (%s, %s, %s, now(), 'ok', NULL)
        ON CONFLICT (source_file) DO UPDATE SET
            report_date = EXCLUDED.report_date,
            rows_imported = EXCLUDED.rows_imported,
            imported_at = now(),
            status = 'ok',
            error = NULL
        """,
        (source_file, date_from, len(values)),
    )
    upsert_products(cur, records)
    return len(values)


def import_stock(cur: Any, records: list[dict[str, Any]]) -> int:
    if not records:
        raise RuntimeError("WB вернул пустой снимок остатков; существующий снимок не заменён")
    columns = [
        "snapshot_date",
        "wb_nmid",
        "seller_article",
        "product_name",
        "category_name",
        "brand",
        "subject_id",
        "stock_qty",
        "stock_amount_rub",
        "to_customer_qty",
        "from_customer_qty",
        "orders_qty",
        "orders_amount_rub",
        "buyouts_qty",
        "buyouts_amount_rub",
        "buyout_pct",
        "price_min_rub",
        "price_max_rub",
    ]
    cur.execute("TRUNCATE public.wb_stock_api_current")
    execute_values(
        cur,
        f"INSERT INTO public.wb_stock_api_current ({', '.join(columns)}) VALUES %s",
        [tuple(record.get(column) for column in columns) for record in records],
        page_size=1000,
    )
    upsert_products(cur, records)
    return len(records)


def drop_wb_views(cur: Any) -> None:
    for view_name in (
        "mv_wb_funnel_daily_summary_rollup",
        "mv_wb_funnel_daily_product_rollup",
        "mv_product_abc_wb",
        "mv_category_stock_sku_attribute_stats",
        "mv_sku_card_scoring_wb",
        "mv_wb_adv_daily_by_article_category",
        "mv_wb_abc_product_stock_base",
        "mv_wb_abc_product_orders_base",
        "mv_wb_funnel_product_options",
        "mv_wb_funnel_filter_options",
        "mv_wb_funnel_daily_by_article_category",
    ):
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name} CASCADE")


def rebuild_views(cur: Any) -> None:
    started = time.monotonic()
    stages = 6
    print(f"ПЛАН: {CLIENT_KEY} WB витрины | этапов {stages} | БД {TARGET_DB}", flush=True)
    drop_wb_views(cur)
    print("ПРОГРЕСС: 1/6 (16.7%) | старые WB-витрины удалены | errors=0", flush=True)
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_funnel_daily_by_article_category AS
        SELECT
            f.report_date,
            f.wb_nmid::text AS sku,
            NULL::text AS barcode,
            f.seller_article,
            coalesce(nullif(f.seller_article, ''), f.wb_nmid::text) AS product_artikul,
            max(f.product_name) AS product_name,
            NULL::text AS category_level_1,
            NULL::text AS category_level_2,
            max(f.category_name) AS category_level_3,
            max(coalesce(nullif(f.category_name, ''), 'Без категории')) AS category_name,
            max(coalesce(nullif(f.category_name, ''), 'Без категории')) AS subcategory_name,
            max(f.brand) AS brand,
            NULL::text AS model,
            NULL::text AS work_schema,
            NULL::text AS abc_orders_amount,
            NULL::text AS abc_orders_qty,
            coalesce(sum(f.ordered_amount_rub), 0)::numeric AS ordered_amount_rub,
            NULL::numeric AS ordered_amount_dynamic,
            NULL::numeric AS search_catalog_position,
            NULL::numeric AS search_catalog_position_dynamic,
            coalesce(sum(f.impressions_total), 0)::numeric AS impressions_total,
            NULL::numeric AS impressions_total_dynamic,
            coalesce(sum(f.impressions_total), 0)::numeric AS impressions_search_catalog,
            NULL::numeric AS impressions_search_catalog_dynamic,
            coalesce(sum(f.card_visits), 0)::numeric AS card_visits,
            NULL::numeric AS card_visits_dynamic,
            coalesce(sum(f.cart_adds), 0)::numeric AS cart_adds,
            NULL::numeric AS cart_adds_dynamic,
            coalesce(sum(f.ordered_units), 0)::numeric AS ordered_units,
            NULL::numeric AS ordered_units_dynamic,
            coalesce(sum(f.bought_units), 0)::numeric AS bought_units,
            coalesce(sum(f.bought_amount_rub), 0)::numeric AS bought_amount_rub,
            coalesce(sum(f.favorites_adds), 0)::numeric AS favorites_adds,
            coalesce(sum(f.cancelled_units), 0)::numeric AS cancelled_units,
            coalesce(sum(f.cancelled_amount_rub), 0)::numeric AS cancelled_amount_rub,
            coalesce(sum(f.wb_club_ordered_units), 0)::numeric AS wb_club_ordered_units,
            coalesce(sum(f.wb_club_bought_units), 0)::numeric AS wb_club_bought_units,
            coalesce(sum(f.wb_club_cancelled_units), 0)::numeric AS wb_club_cancelled_units,
            coalesce(sum(f.wb_club_ordered_amount_rub), 0)::numeric AS wb_club_ordered_amount_rub,
            coalesce(sum(f.wb_club_bought_amount_rub), 0)::numeric AS wb_club_bought_amount_rub,
            coalesce(sum(f.wb_club_cancelled_amount_rub), 0)::numeric AS wb_club_cancelled_amount_rub,
            CASE WHEN coalesce(sum(f.impressions_total), 0) <> 0
                THEN round(sum(f.card_visits)::numeric / sum(f.impressions_total)::numeric * 100, 2)
                ELSE 0 END AS search_to_card_visit_pct,
            CASE WHEN coalesce(sum(f.impressions_total), 0) <> 0
                THEN round(sum(f.card_visits)::numeric / sum(f.impressions_total)::numeric * 100, 2)
                ELSE 0 END AS total_impression_to_card_visit_pct,
            CASE WHEN coalesce(sum(f.card_visits), 0) <> 0
                THEN round(sum(f.cart_adds)::numeric / sum(f.card_visits)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_cart_pct,
            CASE WHEN coalesce(sum(f.cart_adds), 0) <> 0
                THEN round(sum(f.ordered_units)::numeric / sum(f.cart_adds)::numeric * 100, 2)
                ELSE 0 END AS cart_to_order_pct,
            CASE WHEN coalesce(sum(f.card_visits), 0) <> 0
                THEN round(sum(f.ordered_units)::numeric / sum(f.card_visits)::numeric * 100, 2)
                ELSE 0 END AS card_visit_to_order_pct,
            CASE WHEN coalesce(sum(f.ordered_units), 0) <> 0
                THEN round(sum(f.ordered_amount_rub)::numeric / sum(f.ordered_units)::numeric, 2)
                ELSE 0 END AS ordered_amount_per_unit_rub
        FROM public.wb_funnel_daily f
        WHERE f.wb_nmid IS NOT NULL
        GROUP BY f.report_date, f.wb_nmid::text, f.seller_article
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_funnel_filter_options AS
        SELECT
            min(report_date) AS date_from,
            max(report_date) AS date_to,
            array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
            count(DISTINCT category_name) AS categories
        FROM public.mv_wb_funnel_daily_by_article_category
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_funnel_product_options AS
        SELECT DISTINCT category_name, subcategory_name, product_name, product_artikul,
               seller_article, sku, barcode
        FROM public.mv_wb_funnel_daily_by_article_category
        WHERE product_name IS NOT NULL
        """
    )
    print("ПРОГРЕСС: 2/6 (33.3%) | воронка, фильтры и товары | errors=0", flush=True)
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_abc_product_orders_base AS
        SELECT
            report_date,
            sku::text AS artikul_wb,
            max(coalesce(product_name, sku::text)) AS naimenovanie,
            max(coalesce(category_name, 'Без категории')) AS category_name,
            max(coalesce(subcategory_name, category_name, 'Без категории')) AS subcategory_name,
            coalesce(sum(ordered_units), 0)::numeric AS zakazano_sht,
            coalesce(sum(ordered_amount_rub), 0)::numeric AS zakazano_rub,
            NULL::numeric AS reyting_kartochki,
            NULL::numeric AS reyting_po_otzyvam,
            NULL::text AS gj_wb_article,
            NULL::text AS gj_ozon_sku,
            NULL::text AS gj_model,
            NULL::text AS assortment_bia,
            NULL::text AS tg,
            NULL::text AS tg_plus,
            NULL::text AS cg,
            NULL::text AS season
        FROM public.mv_wb_funnel_daily_by_article_category
        WHERE sku IS NOT NULL
        GROUP BY report_date, sku::text
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_wb_abc_product_stock_base AS
        SELECT
            s.wb_nmid::text AS artikul_wb,
            max(coalesce(s.product_name, s.wb_nmid::text)) AS naimenovanie,
            max(coalesce(s.category_name, 'Без категории')) AS category_name,
            max(coalesce(s.category_name, 'Без категории')) AS subcategory_name,
            coalesce(sum(s.stock_qty), 0)::numeric AS total_stock_qty,
            NULL::numeric AS reyting_kartochki,
            NULL::numeric AS reyting_po_otzyvam,
            NULL::text AS gj_wb_article,
            NULL::text AS gj_ozon_sku,
            NULL::text AS gj_model,
            NULL::text AS assortment_bia,
            NULL::text AS tg,
            NULL::text AS tg_plus,
            NULL::text AS cg,
            NULL::text AS season
        FROM public.wb_stock_api_current s
        GROUP BY s.wb_nmid::text
        """
    )
    print("ПРОГРЕСС: 3/6 (50.0%) | базы ABC заказов и остатков | errors=0", flush=True)
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_sku_card_scoring_wb AS
        WITH funnel AS (
            SELECT
                wb_nmid::text AS artikul_wb,
                max(product_name) AS naimenovanie,
                max(coalesce(category_name, 'Без категории')) AS category_name,
                max(brand) AS brand,
                max(card_rating) AS reyting_kartochki,
                max(review_rating) AS reyting_po_otzyvam,
                coalesce(sum(ordered_units), 0)::numeric AS zakazano_sht,
                coalesce(sum(ordered_amount_rub), 0)::numeric AS zakazano_rub
            FROM public.wb_funnel_daily
            WHERE wb_nmid IS NOT NULL
            GROUP BY wb_nmid::text
        ),
        content AS (
            SELECT DISTINCT ON ((payload->>'nmID')::bigint)
                (payload->>'nmID')::text AS artikul_wb,
                coalesce(
                    nullif(payload->>'subjectID', ''),
                    'name:' || lower(btrim(coalesce(payload->>'subjectName', '')))
                ) AS category_key,
                payload
            FROM public.wb_api_entities
            WHERE source_key = 'content.cards'
              AND nullif(payload->>'nmID', '') IS NOT NULL
            ORDER BY (payload->>'nmID')::bigint, captured_at DESC, entity_key DESC
        ),
        content_attributes AS (
            SELECT
                c.artikul_wb,
                c.category_key,
                coalesce(
                    nullif(item->>'id', ''),
                    'name:' || lower(btrim(coalesce(item->>'name', '')))
                ) AS attribute_key,
                CASE jsonb_typeof(item->'value')
                    WHEN 'array' THEN jsonb_array_length(item->'value') > 0
                    WHEN 'string' THEN btrim(item->>'value') <> ''
                    WHEN 'null' THEN false
                    ELSE item ? 'value'
                END AS is_filled
            FROM content c
            CROSS JOIN LATERAL jsonb_array_elements(CASE
                WHEN jsonb_typeof(c.payload->'characteristics') = 'array' THEN c.payload->'characteristics'
                ELSE '[]'::jsonb
            END) item
            WHERE nullif(item->>'id', '') IS NOT NULL
               OR nullif(btrim(item->>'name'), '') IS NOT NULL
        ),
        category_schema AS (
            SELECT
                category_key,
                count(DISTINCT attribute_key)::bigint AS category_attrs_total
            FROM content_attributes
            GROUP BY category_key
        ),
        product_category_metrics AS (
            SELECT
                artikul_wb,
                count(DISTINCT attribute_key) FILTER (WHERE is_filled)::bigint AS category_attrs_filled
            FROM content_attributes
            GROUP BY artikul_wb
        ),
        content_metrics AS (
            SELECT
                c.artikul_wb,
                c.payload,
                coalesce(cs.category_attrs_total, 0::bigint) AS category_attrs_total,
                coalesce(pm.category_attrs_filled, 0::bigint) AS category_attrs_filled,
                jsonb_array_length(CASE
                    WHEN jsonb_typeof(payload->'photos') = 'array' THEN payload->'photos'
                    ELSE '[]'::jsonb
                END)::integer AS foto_count,
                (
                    (nullif(btrim(payload->>'title'), '') IS NOT NULL)::integer
                    + (nullif(btrim(payload->>'description'), '') IS NOT NULL)::integer
                    + (nullif(btrim(payload->>'vendorCode'), '') IS NOT NULL)::integer
                    + (nullif(btrim(payload->>'brand'), '') IS NOT NULL)::integer
                    + (nullif(btrim(payload->>'subjectName'), '') IS NOT NULL)::integer
                    + (payload#>'{dimensions,weightBrutto}' IS NOT NULL)::integer
                    + (payload#>'{dimensions,length}' IS NOT NULL)::integer
                    + (payload#>'{dimensions,width}' IS NOT NULL)::integer
                    + (payload#>'{dimensions,height}' IS NOT NULL)::integer
                    + (jsonb_array_length(CASE
                        WHEN jsonb_typeof(payload->'sizes') = 'array' THEN payload->'sizes'
                        ELSE '[]'::jsonb
                    END) > 0)::integer
                    + (EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(CASE
                            WHEN jsonb_typeof(payload->'sizes') = 'array' THEN payload->'sizes'
                            ELSE '[]'::jsonb
                        END) size_row
                        WHERE jsonb_array_length(CASE
                            WHEN jsonb_typeof(size_row->'skus') = 'array' THEN size_row->'skus'
                            ELSE '[]'::jsonb
                        END) > 0
                    ))::integer
                    + (jsonb_array_length(CASE
                        WHEN jsonb_typeof(payload->'photos') = 'array' THEN payload->'photos'
                        ELSE '[]'::jsonb
                    END) > 0)::integer
                    + (nullif(btrim(payload->>'video'), '') IS NOT NULL)::integer
                )::bigint AS common_attrs_filled
            FROM content c
            LEFT JOIN category_schema cs ON cs.category_key = c.category_key
            LEFT JOIN product_category_metrics pm ON pm.artikul_wb = c.artikul_wb
        ),
        keys AS (
            SELECT artikul_wb FROM funnel
            UNION
            SELECT wb_nmid::text FROM public.wb_stock_api_current
            UNION
            SELECT artikul_wb FROM content_metrics
        )
        SELECT
            coalesce(nullif(c.payload->>'subjectName', ''), f.category_name, s.category_name, 'Без категории') AS category_name,
            coalesce(nullif(c.payload->>'subjectName', ''), f.category_name, s.category_name, 'Без категории') AS subcategory_name,
            k.artikul_wb,
            coalesce(nullif(c.payload->>'title', ''), f.naimenovanie, s.product_name, k.artikul_wb) AS naimenovanie,
            length(coalesce(nullif(c.payload->>'title', ''), f.naimenovanie, s.product_name, ''))::integer AS naimenovanie_len,
            length(coalesce(c.payload->>'description', ''))::integer AS opisanie_len,
            CASE WHEN c.artikul_wb IS NULL THEN 0 ELSE 13 END::bigint AS common_attrs_total,
            coalesce(c.common_attrs_filled, 0::bigint) AS common_attrs_filled,
            coalesce(c.category_attrs_total, 0::bigint) AS category_attrs_total,
            coalesce(c.category_attrs_filled, 0::bigint) AS category_attrs_filled,
            coalesce(c.foto_count, 0::integer) AS foto_count,
            coalesce(s.stock_qty, 0)::numeric AS total_stock_qty,
            coalesce(f.zakazano_sht, 0)::numeric AS zakazano_sht,
            coalesce(f.zakazano_rub, 0)::numeric AS zakazano_rub,
            f.reyting_kartochki,
            f.reyting_po_otzyvam
        FROM keys k
        LEFT JOIN funnel f ON f.artikul_wb = k.artikul_wb
        LEFT JOIN public.wb_stock_api_current s ON s.wb_nmid = k.artikul_wb
        LEFT JOIN content_metrics c ON c.artikul_wb = k.artikul_wb
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_category_stock_sku_attribute_stats AS
        SELECT
            category_name,
            count(DISTINCT artikul_wb)::bigint AS sku_count,
            sum(total_stock_qty)::numeric AS total_stock_qty,
            avg(common_attrs_total)::numeric AS common_attrs_total,
            avg(common_attrs_filled)::numeric AS common_attrs_filled,
            avg(category_attrs_total)::numeric AS category_attrs_total,
            avg(category_attrs_filled)::numeric AS category_attrs_filled,
            sum(zakazano_sht)::numeric AS zakazano_sht,
            sum(zakazano_rub)::numeric AS zakazano_rub
        FROM public.mv_sku_card_scoring_wb
        GROUP BY category_name
        """
    )
    cur.execute("CREATE MATERIALIZED VIEW public.mv_product_abc_wb AS " + PRODUCT_ABC_SQL)
    print("ПРОГРЕСС: 4/6 (66.7%) | SKU и товарная ABC | errors=0", flush=True)
    for view_name in (
        "mv_wb_funnel_daily_product_rollup",
        "mv_wb_funnel_daily_summary_rollup",
    ):
        cur.execute(ROLLUP_SQL[view_name])
        for query in INDEX_SQL[view_name]:
            cur.execute(query)
    print("ПРОГРЕСС: 5/6 (83.3%) | недельные rollup-витрины | errors=0", flush=True)
    cur.execute("SELECT to_regclass('public.wb_api_entities')")
    has_promotion_entities = bool(cur.fetchone()[0])
    if has_promotion_entities:
        cur.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_adv_daily_by_article_category AS
            WITH expanded AS (
                SELECT
                    (day_payload->>'date')::timestamptz::date AS report_date,
                    coalesce(entity.payload->>'advertId', entity.entity_key) AS campaign_id,
                    nullif(nm_payload->>'nmId', '') AS wb_nmid,
                    nm_payload,
                    entity.captured_at
                FROM public.wb_api_entities entity
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(entity.payload->'days') = 'array'
                         THEN entity.payload->'days' ELSE '[]'::jsonb END
                ) day_payload
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(day_payload->'apps') = 'array'
                         THEN day_payload->'apps' ELSE '[]'::jsonb END
                ) app_payload
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(app_payload->'nms') = 'array'
                         THEN app_payload->'nms' ELSE '[]'::jsonb END
                ) nm_payload
                WHERE entity.source_key = 'promotion.fullstats'
                  AND nullif(day_payload->>'date', '') IS NOT NULL
                  AND nullif(nm_payload->>'nmId', '') IS NOT NULL
            ), aggregated AS (
                SELECT
                    report_date,
                    campaign_id,
                    wb_nmid,
                    max(nullif(nm_payload->>'name', '')) AS api_product_name,
                    coalesce(sum(nullif(nm_payload->>'views', '')::numeric), 0) AS impressions,
                    coalesce(sum(nullif(nm_payload->>'clicks', '')::numeric), 0) AS clicks,
                    coalesce(sum(nullif(nm_payload->>'sum', '')::numeric), 0) AS expense_rub,
                    coalesce(sum(nullif(nm_payload->>'atbs', '')::numeric), 0) AS added_to_cart,
                    coalesce(sum(nullif(nm_payload->>'orders', '')::numeric), 0) AS orders_qty,
                    coalesce(sum(nullif(nm_payload->>'sum_price', '')::numeric), 0) AS orders_amount_rub,
                    max(captured_at) AS imported_at
                FROM expanded
                GROUP BY report_date, campaign_id, wb_nmid
            )
            SELECT
                a.report_date,
                a.wb_nmid AS wb_marketplace_article,
                NULL::text AS ozon_marketplace_article,
                a.wb_nmid AS sku,
                stock.seller_article,
                coalesce(stock.seller_article, a.wb_nmid) AS product_artikul,
                coalesce(stock.product_name, a.api_product_name, a.wb_nmid) AS product_name,
                stock.category_name,
                stock.category_name AS subcategory_name,
                stock.brand,
                NULL::text AS barcode,
                a.impressions,
                a.clicks,
                CASE WHEN a.impressions <> 0 THEN round(a.clicks / a.impressions * 100, 4) END AS ctr_pct,
                a.expense_rub,
                a.expense_rub AS fact_expense_rub,
                CASE WHEN a.clicks <> 0 THEN round(a.expense_rub / a.clicks, 4) END AS cpc_rub,
                CASE WHEN a.impressions <> 0 THEN round(a.expense_rub / a.impressions * 1000, 4) END AS cpm_rub,
                a.added_to_cart,
                a.orders_qty,
                CASE WHEN a.clicks <> 0 THEN round(a.orders_qty / a.clicks * 100, 4) END AS cr_pct,
                a.orders_amount_rub,
                CASE WHEN a.orders_qty <> 0 THEN round(a.expense_rub / a.orders_qty, 4) END AS cpa_rub,
                CASE WHEN a.orders_amount_rub <> 0 THEN round(a.expense_rub / a.orders_amount_rub * 100, 4) END AS drr_pct,
                a.orders_qty AS total_orders_qty,
                a.orders_amount_rub AS total_orders_amount_rub,
                CASE WHEN a.orders_amount_rub <> 0 THEN round(a.expense_rub / a.orders_amount_rub * 100, 4) END AS total_drr_pct,
                CASE WHEN a.orders_qty <> 0 THEN round(a.expense_rub / a.orders_qty, 4) END AS total_cpa_rub,
                'api://wb/promotion/fullstats/' || a.campaign_id AS source_file,
                'nm-daily'::text AS source_sheet,
                row_number() OVER (ORDER BY a.report_date, a.campaign_id, a.wb_nmid)::integer AS source_row_num,
                NULL::bigint AS file_size_bytes,
                NULL::timestamp AS file_mtime,
                a.imported_at::timestamp AS imported_at
            FROM aggregated a
            LEFT JOIN public.wb_stock_api_current stock ON stock.wb_nmid = a.wb_nmid
            """
        )
    else:
        cur.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_adv_daily_by_article_category AS
            SELECT NULL::date report_date, NULL::text wb_marketplace_article,
                   NULL::text ozon_marketplace_article, NULL::text sku,
                   NULL::text seller_article, NULL::text product_artikul,
                   NULL::text product_name, NULL::text category_name,
                   NULL::text subcategory_name, NULL::text brand, NULL::text barcode,
                   NULL::numeric impressions, NULL::numeric clicks, NULL::numeric ctr_pct,
                   NULL::numeric expense_rub, NULL::numeric fact_expense_rub,
                   NULL::numeric cpc_rub, NULL::numeric cpm_rub,
                   NULL::numeric added_to_cart, NULL::numeric orders_qty,
                   NULL::numeric cr_pct, NULL::numeric orders_amount_rub,
                   NULL::numeric cpa_rub, NULL::numeric drr_pct,
                   NULL::numeric total_orders_qty, NULL::numeric total_orders_amount_rub,
                   NULL::numeric total_drr_pct, NULL::numeric total_cpa_rub,
                   NULL::text source_file, NULL::text source_sheet,
                   NULL::integer source_row_num, NULL::bigint file_size_bytes,
                   NULL::timestamp file_mtime, NULL::timestamp imported_at
            WHERE false
            """
        )
    # Единая витрина сохраняет XLSX-историю и заменяет новые даты API-данными.
    rebuild_wb_advertising_view(cur)
    indexes = (
        "CREATE INDEX IF NOT EXISTS idx_km_wb_funnel_date ON public.mv_wb_funnel_daily_by_article_category(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_km_wb_funnel_sku ON public.mv_wb_funnel_daily_by_article_category(sku)",
        "CREATE INDEX IF NOT EXISTS idx_km_wb_funnel_category ON public.mv_wb_funnel_daily_by_article_category(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_km_wb_sku_category ON public.mv_sku_card_scoring_wb(category_name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_km_wb_sku_article ON public.mv_sku_card_scoring_wb(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_km_wb_product_category ON public.mv_product_abc_wb(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_km_wb_product_article ON public.mv_product_abc_wb(artikul_wb)",
    )
    for query in indexes:
        cur.execute(query)
    for relation in (
        "wb_funnel_daily",
        "wb_stock_api_current",
        "mv_wb_funnel_daily_by_article_category",
        "mv_wb_funnel_filter_options",
        "mv_wb_funnel_product_options",
        "mv_wb_abc_product_orders_base",
        "mv_wb_abc_product_stock_base",
        "mv_sku_card_scoring_wb",
        "mv_category_stock_sku_attribute_stats",
        "mv_product_abc_wb",
        "mv_wb_funnel_daily_product_rollup",
        "mv_wb_funnel_daily_summary_rollup",
        "mv_wb_adv_daily_by_article_category",
    ):
        cur.execute(f"ANALYZE public.{relation}")
    print(
        f"ПРОГРЕСС: 6/6 (100.0%) | индексы и совместимость рекламы | errors=0 | "
        f"elapsed={format_duration(time.monotonic() - started)}",
        flush=True,
    )


def discover_funnel_products(
    token: str,
    date_from: date,
    date_to: date,
) -> tuple[list[int], int]:
    nm_ids: list[int] = []
    offset = 0
    requests_count = 0
    while True:
        requests_count += 1
        body = {
            "selectedPeriod": {"start": date_from.isoformat(), "end": date_to.isoformat()},
            "nmIds": [],
            "brandNames": [],
            "subjectIds": [],
            "tagIds": [],
            "skipDeletedNm": True,
            "orderBy": {"field": "openCard", "mode": "desc"},
            "limit": 1000,
            "offset": offset,
        }
        payload = post_json(
            FUNNEL_PRODUCTS_PATH,
            token,
            body,
            request_no=requests_count,
            request_total=requests_count,
        )
        items = payload_items(payload)
        page_ids = []
        for item in items:
            product = product_payload(item)
            nmid = integer(first(product, "nmId", "nmID", "nm_id"))
            if nmid is not None:
                page_ids.append(nmid)
        nm_ids.extend(page_ids)
        print(
            f"ПРОГРЕСС: запрос {requests_count} | список товаров offset={offset} | "
            f"batch={len(page_ids)} | accumulated={len(nm_ids)} | errors=0",
            flush=True,
        )
        if len(items) < 1000:
            break
        offset += 1000
    return sorted(set(nm_ids)), requests_count


FUNNEL_CHECKPOINT_RETENTION_DAYS = 14


def funnel_batch_key(batch: list[int]) -> str:
    """Stable id of a nmID batch, independent of its position in the run."""
    raw = ",".join(str(value) for value in sorted(batch))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_funnel_checkpoint(cur: Any, date_from: date, date_to: date) -> dict[str, list[dict[str, Any]]]:
    if cur is None:
        return {}
    cur.execute(
        """
        SELECT batch_key, rows_json
        FROM public.wb_funnel_sync_batches
        WHERE date_from = %s AND date_to = %s
        """,
        (date_from, date_to),
    )
    done: dict[str, list[dict[str, Any]]] = {}
    for batch_key, rows_json in cur.fetchall():
        done[str(batch_key)] = list(rows_json or [])
    return done


def save_funnel_batch(
    conn: Any,
    cur: Any,
    date_from: date,
    date_to: date,
    batch_key: str,
    batch_index: int,
    nm_count: int,
    rows: list[dict[str, Any]],
) -> None:
    if cur is None:
        return
    cur.execute(
        """
        INSERT INTO public.wb_funnel_sync_batches (
            date_from, date_to, batch_key, batch_index, nm_count, rows_json, fetched_at
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
        ON CONFLICT (date_from, date_to, batch_key) DO UPDATE SET
            batch_index = EXCLUDED.batch_index,
            nm_count = EXCLUDED.nm_count,
            rows_json = EXCLUDED.rows_json,
            fetched_at = now()
        """,
        (
            date_from,
            date_to,
            batch_key,
            batch_index,
            nm_count,
            json.dumps(rows, ensure_ascii=False, default=str),
        ),
    )
    if conn is not None:
        conn.commit()


def clear_funnel_checkpoint(cur: Any, date_from: date, date_to: date) -> None:
    if cur is None:
        return
    cur.execute(
        """
        DELETE FROM public.wb_funnel_sync_batches
        WHERE (date_from = %s AND date_to = %s)
           OR fetched_at < now() - make_interval(days => %s)
        """,
        (date_from, date_to, FUNNEL_CHECKPOINT_RETENTION_DAYS),
    )


def purge_funnel_checkpoints(cur: Any) -> None:
    """Keep the completed window briefly so a later retry can reuse its batches."""
    if cur is None:
        return
    cur.execute(
        """
        DELETE FROM public.wb_funnel_sync_batches
        WHERE fetched_at < now() - make_interval(days => %s)
        """,
        (FUNNEL_CHECKPOINT_RETENTION_DAYS,),
    )


def fetch_funnel(
    token: str,
    date_from: date,
    date_to: date,
    *,
    conn: Any = None,
    cur: Any = None,
) -> tuple[list[dict[str, Any]], int]:
    days = (date_to - date_from).days + 1
    if days > MAX_FUNNEL_DAYS:
        raise ValueError(f"WB v3 history отдаёт максимум {MAX_FUNNEL_DAYS} дней за один запуск")
    earliest = date.today() - timedelta(days=MAX_FUNNEL_DAYS)
    if date_from < earliest:
        raise ValueError(
            f"WB v3 history доступна только за последнюю неделю; минимальная дата {earliest.isoformat()}"
        )
    nm_ids, requests_count = discover_funnel_products(token, date_from, date_to)
    if not nm_ids:
        raise RuntimeError(f"WB не вернул список товаров клиента {CLIENT_KEY}; существующие строки не изменены")
    batches = [
        nm_ids[index:index + FUNNEL_NMID_BATCH_SIZE]
        for index in range(0, len(nm_ids), FUNNEL_NMID_BATCH_SIZE)
    ]
    records: list[dict[str, Any]] = []
    request_total = requests_count + len(batches)
    done_batches = load_funnel_checkpoint(cur, date_from, date_to)
    batch_keys = [funnel_batch_key(batch) for batch in batches]
    resumed_batches = sum(1 for key in batch_keys if key in done_batches)
    print(
        f"ПЛАН: воронка WB | батчей {len(batches)} | из чекпоинта {resumed_batches} | "
        f"к загрузке {len(batches) - resumed_batches} | "
        f"период {date_from.isoformat()}..{date_to.isoformat()}",
        flush=True,
    )
    performed_requests = requests_count
    for batch_index, (batch, batch_key) in enumerate(zip(batches, batch_keys), start=1):
        cached_rows = done_batches.get(batch_key)
        if cached_rows is not None:
            records.extend(cached_rows)
            print(
                f"ПРОГРЕСС: батч {batch_index}/{len(batches)} | nmID batch={len(batch)} | "
                f"rows={len(cached_rows)} | accumulated={len(records)} | "
                f"источник=чекпоинт | errors=0",
                flush=True,
            )
            continue
        body = {
            "selectedPeriod": {"start": date_from.isoformat(), "end": date_to.isoformat()},
            "nmIds": batch,
            "skipDeletedNm": True,
            "aggregationLevel": "day",
        }
        request_no = requests_count + batch_index
        payload = post_json(
            FUNNEL_PATH,
            token,
            body,
            request_no=request_no,
            request_total=request_total,
        )
        performed_requests += 1
        batch_rows = parse_funnel_response(payload, date_from)
        records.extend(batch_rows)
        save_funnel_batch(
            conn,
            cur,
            date_from,
            date_to,
            batch_key,
            batch_index,
            len(batch),
            batch_rows,
        )
        print(
            f"ПРОГРЕСС: запрос {request_no}/{request_total} | батч {batch_index}/{len(batches)} | "
            f"nmID batch={len(batch)} | rows={len(batch_rows)} | accumulated={len(records)} | "
            f"чекпоинт=сохранён | errors=0",
            flush=True,
        )
    return records, performed_requests


def fetch_stock(token: str, date_from: date, date_to: date) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    offset = 0
    requests_count = 0
    while True:
        requests_count += 1
        body = {
            "nmIDs": [],
            "currentPeriod": {"start": date_from.isoformat(), "end": date_to.isoformat()},
            "stockType": "",
            "skipDeletedNm": True,
            "orderBy": {"field": "stockCount", "mode": "desc"},
            "availabilityFilters": [],
            "limit": STOCK_PAGE_SIZE,
            "offset": offset,
        }
        payload = post_json(
            STOCK_PATH,
            token,
            body,
            request_no=requests_count,
            request_total=max(requests_count, 1),
        )
        page_rows = parse_stock_response(payload, date.today())
        rows.extend(page_rows)
        print(
            f"ПРОГРЕСС: запрос {requests_count} | остатки offset={offset} | "
            f"batch={len(page_rows)} | accumulated={len(rows)} | errors=0",
            flush=True,
        )
        if len(page_rows) < STOCK_PAGE_SIZE:
            break
        offset += STOCK_PAGE_SIZE
        sleep_with_progress(20, f"остатки | запрос {requests_count + 1}")
    return rows, requests_count


def current_stock_period(today: date | None = None) -> tuple[date, date]:
    """WB stock-report returns a current snapshot, not historical inventory."""
    current = today or date.today()
    return current, current


def run_step(step: str, date_from: date, date_to: date) -> dict[str, Any]:
    started = time.monotonic()
    token = token_value() if step != "views" else ""
    requests_count = 0
    rows_count = 0
    effective_date_from = date_from
    effective_date_to = date_to
    with db_connection() as conn, conn.cursor() as cur:
        ensure_schema(cur)
        if step == "funnel":
            rows, requests_count = fetch_funnel(token, date_from, date_to, conn=conn, cur=cur)
            rows_count = import_funnel(cur, rows, date_from, date_to)
            # Keep the completed checkpoint for a short TTL: a retry after a
            # later transaction/view failure must not download every nmID batch.
            purge_funnel_checkpoints(cur)
        elif step == "stock":
            effective_date_from, effective_date_to = current_stock_period()
            rows, requests_count = fetch_stock(token, effective_date_from, effective_date_to)
            rows_count = import_stock(cur, rows)
            published_rows = inventory_history.publish_wb_current_stock_table(conn)
            print(
                f"ПРОГРЕСС: текущие остатки опубликованы в витрину | "
                f"source_rows={rows_count:,} | dashboard_rows={published_rows:,} | errors=0",
                flush=True,
            )
        elif step == "views":
            rebuild_views(cur)
        else:
            raise ValueError(f"Неизвестный шаг: {step}")
        cur.execute(
            """
            INSERT INTO public.km_wb_api_runs (
                step, date_from, date_to, requests_count, rows_count,
                status, finished_at
            ) VALUES (%s, %s, %s, %s, %s, 'ok', now())
            """,
            (step, effective_date_from, effective_date_to, requests_count, rows_count),
        )
        conn.commit()
    return {
        "step": step,
        "rows": rows_count,
        "requests": requests_count,
        "elapsed": time.monotonic() - started,
    }


def main() -> None:
    configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=("funnel", "stock", "views", "all"), default="all")
    parser.add_argument("--date-from", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--date-to", default=(date.today() - timedelta(days=1)).isoformat())
    args = parser.parse_args()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    steps = ["funnel", "stock", "views"] if args.step == "all" else [args.step]
    started = time.monotonic()
    print(
        f"ПЛАН: {CLIENT_KEY} WB API | БД {TARGET_DB} | шагов {len(steps)} | "
        f"период {date_from.isoformat()}..{date_to.isoformat()} | "
        f"запросы read-only, запись только в {TARGET_DB} | "
        "лимиты: Retry-After/X-Ratelimit-Retry",
        flush=True,
    )
    total_rows = 0
    total_requests = 0
    completed = 0
    try:
        for index, step in enumerate(steps, start=1):
            elapsed = time.monotonic() - started
            eta = elapsed / completed * (len(steps) - completed) if completed else 0
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({(index - 1) / len(steps) * 100:.1f}%) | "
                f"{step} | rows={total_rows} | requests={total_requests} | errors=0 | "
                f"elapsed={format_duration(elapsed)} | ETA={format_duration(eta)}",
                flush=True,
            )
            result = run_step(step, date_from, date_to)
            completed += 1
            total_rows += int(result["rows"])
            total_requests += int(result["requests"])
            print(
                f"[{index}/{len(steps)}] {step}: imported={result['rows']} | "
                f"requests={result['requests']} | errors=0 | "
                f"elapsed={format_duration(float(result['elapsed']))}",
                flush=True,
            )
    except Exception:
        print(
            f"ИТОГ: {CLIENT_KEY} WB | completed={completed}/{len(steps)} | "
            f"rows={total_rows} | requests={total_requests} | errors=1 | "
            f"elapsed={format_duration(time.monotonic() - started)} | stopped=no | partial=yes",
            flush=True,
        )
        raise
    print(
        f"ИТОГ: {CLIENT_KEY} WB | completed={completed}/{len(steps)} | rows={total_rows} | "
        f"requests={total_requests} | errors=0 | output_db={TARGET_DB} | "
        f"elapsed={format_duration(time.monotonic() - started)} | stopped=no | partial=no",
        flush=True,
    )


if __name__ == "__main__":
    main()
