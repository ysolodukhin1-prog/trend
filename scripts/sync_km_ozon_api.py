#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Ozon API pipeline for the isolated KM Trade database."""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
TARGET_DB = os.environ.get("DASHBOARD_DB_NAME", "km_trade_products")
CLIENT_KEY = os.environ.get("DASHBOARD_CLIENT", "km_trade")
CLIENT_LABEL = os.environ.get("DASHBOARD_CLIENT_LABEL", CLIENT_KEY)
API_SOURCE_PREFIX = "ozon_api://"
SELLER_BASE_URL = os.environ.get("OZON_SELLER_API_BASE_URL", "https://api-seller.ozon.ru").rstrip("/")
PERFORMANCE_BASE_URL = os.environ.get(
    "OZON_PERFORMANCE_API_BASE_URL", "https://api-performance.ozon.ru"
).rstrip("/")
SELLER_ANALYTICS_INTERVAL = float(os.environ.get("OZON_SELLER_ANALYTICS_INTERVAL_SECONDS", "75"))
SELLER_DEFAULT_INTERVAL = float(os.environ.get("OZON_SELLER_DEFAULT_INTERVAL_SECONDS", "1.1"))
PERFORMANCE_INTERVAL = float(os.environ.get("OZON_PERFORMANCE_INTERVAL_SECONDS", "1.1"))
ADAPTIVE_RATE_LIMIT_MAX_INTERVAL = max(
    SELLER_ANALYTICS_INTERVAL,
    float(os.environ.get("OZON_API_ADAPTIVE_MAX_INTERVAL_SECONDS", "300")),
)
ADAPTIVE_RATE_LIMIT_STEADY_MAX_INTERVAL = max(
    SELLER_ANALYTICS_INTERVAL,
    min(
        ADAPTIVE_RATE_LIMIT_MAX_INTERVAL,
        float(os.environ.get("OZON_API_ADAPTIVE_STEADY_MAX_INTERVAL_SECONDS", "120")),
    ),
)
ADAPTIVE_RATE_LIMIT_RECOVERY_STEP = 15.0
RATE_LIMIT_PROGRESS_TICK_SECONDS = 1.0
PERFORMANCE_MAX_WINDOW_DAYS = min(
    62, max(1, int(os.environ.get("OZON_PERFORMANCE_MAX_WINDOW_DAYS", "62")))
)
PERFORMANCE_REFRESH_DAYS = max(
    0, int(os.environ.get("OZON_PERFORMANCE_REFRESH_DAYS", "7"))
)
PERFORMANCE_REPORT_POLL_ATTEMPTS = max(
    1, int(os.environ.get("OZON_PERFORMANCE_REPORT_POLL_ATTEMPTS", "30"))
)
PERFORMANCE_REPORT_POLL_INTERVAL = max(
    0.0, float(os.environ.get("OZON_PERFORMANCE_REPORT_POLL_INTERVAL_SECONDS", "2"))
)
MAX_RETRIES = int(os.environ.get("OZON_API_MAX_RETRIES", "6"))
MAX_RATE_LIMIT_RETRIES = max(
    MAX_RETRIES, int(os.environ.get("OZON_API_MAX_RATE_LIMIT_RETRIES", "24"))
)
HTTP_TIMEOUT = int(os.environ.get("OZON_API_HTTP_TIMEOUT_SECONDS", "90"))
FUNNEL_PAGE_SIZE = 1000
FUNNEL_CHECKPOINT_SIZE = max(
    FUNNEL_PAGE_SIZE,
    int(os.environ.get("OZON_FUNNEL_CHECKPOINT_SIZE", "5000")),
)


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or not math.isfinite(seconds):
        return "—"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м"
    if minutes:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


class ProgressLine:
    """Keep normal progress on one terminal line; promote state changes to a new line."""

    def __init__(self) -> None:
        self.active = False
        self.last_text = ""

    def update(self, text: str) -> None:
        rendered = str(text).replace("\r", " ").replace("\n", " ")
        padding = max(0, len(self.last_text) - len(rendered))
        print(f"\r{rendered}{' ' * padding}", end="", flush=True)
        self.last_text = rendered
        self.active = True

    def event(self, text: str) -> None:
        if self.active:
            print(flush=True)
        print(str(text).replace("\r", " ").replace("\n", " "), flush=True)
        self.last_text = ""
        self.active = False


@dataclass
class FunnelEta:
    target_rows: int | None = None
    baseline_rows_per_second: float | None = None
    started_at: float = field(default_factory=time.monotonic)

    def remaining_seconds(self, loaded_rows: int, current_rows: int) -> float | None:
        if self.target_rows is None:
            return None
        remaining_rows = max(0, self.target_rows - current_rows)
        elapsed = max(0.0, time.monotonic() - self.started_at)
        current_speed = loaded_rows / elapsed if elapsed >= 5 and loaded_rows > 0 else None
        speed = current_speed or self.baseline_rows_per_second
        return remaining_rows / speed if speed and speed > 0 else None

    def speed(self, loaded_rows: int) -> float | None:
        elapsed = max(0.0, time.monotonic() - self.started_at)
        current_speed = loaded_rows / elapsed if elapsed >= 5 and loaded_rows > 0 else None
        return current_speed or self.baseline_rows_per_second

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)
STOCK_PAGE_SIZE = 1000
PERFORMANCE_CAMPAIGN_BATCH = 10
PERFORMANCE_ALL_SKU_MAX_WINDOW_DAYS = 31
ADV_SOURCE_PRODUCT = "performance/statistics/products/sku"
ADV_SOURCE_CAMPAIGN = "performance/statistics/daily/json"
ADV_SOURCE_ALL_SKU_ORDERS = "performance/statistics/all_sku_promo/orders"
ADV_CAMPAIGN_RAW_COLUMNS = [
    "report_date",
    "campaign_id",
    "campaign_title",
    "impressions",
    "clicks",
    "ctr_pct",
    "expense_rub",
    "cpc_rub",
    "cpm_rub",
    "orders_qty",
    "cr_pct",
    "orders_amount_rub",
    "cpa_rub",
    "drr_pct",
    "source_file",
    "source_sheet",
    "source_row_num",
]

# Force the client/database before importing the dashboard configuration.
os.environ["PGDATABASE"] = TARGET_DB
os.environ["DASHBOARD_CLIENT"] = CLIENT_KEY
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402
import inventory_history  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402


FUNNEL_STANDARD_METRICS = ["revenue", "ordered_units"]
FUNNEL_ADVANCED_METRICS = [
    "revenue",
    "ordered_units",
    "hits_view_search",
    "hits_view_pdp",
    "hits_view",
    "hits_tocart_search",
    "hits_tocart_pdp",
    "hits_tocart",
    "session_view_search",
    "session_view_pdp",
    "session_view",
    "returns",
    "cancellations",
    "delivered_units",
]
FUNNEL_METRICS = FUNNEL_STANDARD_METRICS
FUNNEL_STANDARD_CONTRACT = "standard-v1"
FUNNEL_ADVANCED_CONTRACT = "advanced-v1"


class PipelineError(RuntimeError):
    pass


class ApiRequestError(PipelineError):
    def __init__(self, status: int, method: str, url: str, response_body: str) -> None:
        self.status = status
        self.method = method
        self.url = url
        self.response_body = response_body
        super().__init__(f"Ozon API HTTP {status} для {method} {url}: {response_body}")


@dataclass(frozen=True)
class FunnelCapability:
    mode: str
    contract_key: str
    metrics: tuple[str, ...]
    premium: bool
    premium_plus: bool
    initial_payload: Any
    evidence: str


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError("Ozon API вернул некорректный JSON") from exc


def retry_header_wait_seconds(retry_after: str | None) -> float:
    value = str(retry_after or "").strip()
    if not value:
        return 0.0
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        if numeric > 86_400:
            numeric -= time.time()
        return max(0.0, numeric)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            now = datetime.now(retry_at.tzinfo) if retry_at.tzinfo else datetime.now()
            return max(0.0, (retry_at - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def retry_wait_seconds(status: int, attempt: int, retry_after: str | None, interval: float) -> float:
    header_wait = retry_header_wait_seconds(retry_after)
    if status == 429:
        local_wait = min(ADAPTIVE_RATE_LIMIT_MAX_INTERVAL, max(interval * 2, SELLER_ANALYTICS_INTERVAL))
        return max(header_wait, local_wait)
    return max(header_wait, min(2 ** attempt, 60))


class SerialApiClient:
    def __init__(self) -> None:
        self.last_request_at: dict[str, float] = {}
        self.adaptive_intervals: dict[str, float] = {}
        self.bucket_contexts: dict[str, str] = {}
        self.request_count = 0
        self.progress = ProgressLine()

    def set_context(self, bucket: str, context: str | None) -> None:
        clean = str(context or "").strip()
        if clean:
            self.bucket_contexts[bucket] = clean[:180]
        else:
            self.bucket_contexts.pop(bucket, None)

    def _context_suffix(self, bucket: str) -> str:
        context = self.bucket_contexts.get(bucket)
        return f" | {context}" if context else ""

    def effective_interval(self, bucket: str, configured_interval: float) -> float:
        return max(configured_interval, self.adaptive_intervals.get(bucket, 0.0))

    def increase_interval(self, bucket: str, configured_interval: float, cooldown: float) -> float:
        current = self.effective_interval(bucket, configured_interval)
        steady_cap = max(configured_interval, ADAPTIVE_RATE_LIMIT_STEADY_MAX_INTERVAL)
        adapted = min(
            steady_cap,
            max(configured_interval, cooldown, current * 1.5),
        )
        self.adaptive_intervals[bucket] = adapted
        return adapted

    def recover_interval(self, bucket: str, configured_interval: float) -> float:
        current = self.effective_interval(bucket, configured_interval)
        recovered = max(configured_interval, current - ADAPTIVE_RATE_LIMIT_RECOVERY_STEP)
        self.adaptive_intervals[bucket] = recovered
        return recovered

    def _wait(self, bucket: str, interval: float) -> None:
        previous = self.last_request_at.get(bucket)
        if previous is None:
            return
        remaining = interval - (time.monotonic() - previous)
        announced = False
        while remaining > 0:
            chunk = min(remaining, RATE_LIMIT_PROGRESS_TICK_SECONDS)
            text = (
                f"ПРОГРЕСС: плановая пауза API | {bucket}{self._context_suffix(bucket)} | "
                f"осталось {remaining:.1f} сек. | запросов {self.request_count}"
            )
            if not announced:
                self.progress.event(text)
                announced = True
            else:
                self.progress.update(text)
            time.sleep(chunk)
            remaining = interval - (time.monotonic() - previous)

    def _cooldown(self, bucket: str, status: int, attempt: int, total_attempts: int, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        announced = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            text = (
                f"ПРОГРЕСС: ожидание лимита API | {bucket}{self._context_suffix(bucket)} | HTTP {status} | "
                f"повтор {attempt}/{total_attempts} | осталось {remaining:.1f} сек. | "
                f"запросов {self.request_count}"
            )
            if not announced:
                self.progress.event(text)
                announced = True
            else:
                self.progress.update(text)
            time.sleep(min(remaining, RATE_LIMIT_PROGRESS_TICK_SECONDS))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: Any = None,
        bucket: str,
        interval: float,
        context: str | None = None,
    ) -> ApiResponse:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise PipelineError(f"Метод {method} запрещён read-only политикой")
        if context is not None:
            self.set_context(bucket, context)
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            attempt += 1
            active_interval = self.effective_interval(bucket, interval)
            self._wait(bucket, active_interval)
            self.request_count += 1
            started = time.monotonic()
            try:
                response = urlopen(
                    Request(url, data=data, headers=request_headers, method=method),
                    timeout=HTTP_TIMEOUT,
                )
                body = response.read()
                self.last_request_at[bucket] = time.monotonic()
                self.recover_interval(bucket, interval)
                return ApiResponse(
                    status=response.status,
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                )
            except HTTPError as exc:
                self.last_request_at[bucket] = time.monotonic()
                error_body = exc.read().decode("utf-8", errors="replace")[:1200]
                retryable = exc.code == 429 or 500 <= exc.code < 600
                retry_limit = MAX_RATE_LIMIT_RETRIES if exc.code == 429 else MAX_RETRIES
                if not retryable or attempt >= retry_limit:
                    raise ApiRequestError(
                        exc.code, method, url, error_body
                    ) from exc
                retry_after = (
                    exc.headers.get("Retry-After")
                    or exc.headers.get("X-Ratelimit-Retry")
                    or exc.headers.get("X-RateLimit-Reset")
                )
                header_wait = retry_header_wait_seconds(retry_after)
                wait_seconds = retry_wait_seconds(exc.code, attempt, retry_after, active_interval)
                adapted_interval = active_interval
                if exc.code == 429:
                    adapted_interval = self.increase_interval(bucket, interval, wait_seconds)
                wait_source = "заголовок Ozon" if header_wait >= wait_seconds and header_wait > 0 else "локальный backoff"
                self.progress.event(
                    f"ПРОГРЕСС: повтор API {attempt}/{retry_limit} | HTTP {exc.code} | "
                    f"{bucket}{self._context_suffix(bucket)} | "
                    f"пауза {wait_seconds:.1f} сек. ({wait_source}) | "
                    f"следующий интервал не менее {adapted_interval:.1f} сек. | "
                    f"запросов {self.request_count} | прошло {time.monotonic() - started:.1f} сек.",
                )
                self._cooldown(bucket, exc.code, attempt, retry_limit, wait_seconds)
            except (URLError, TimeoutError) as exc:
                self.last_request_at[bucket] = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise PipelineError(f"Сетевая ошибка Ozon API для {method} {url}: {exc}") from exc
                wait_seconds = min(2 ** attempt, 60)
                self.progress.event(
                    f"ПРОГРЕСС: повтор API {attempt}/{MAX_RETRIES} | сеть | "
                    f"{bucket}{self._context_suffix(bucket)} | "
                    f"ожидание {wait_seconds} сек.",
                )
                time.sleep(wait_seconds)
        raise PipelineError("Исчерпаны повторы Ozon API")


def env_value(name: str) -> str:
    return str(os.environ.get(name) or app.read_app_env_file().get(name) or "").strip()


def seller_credentials() -> tuple[str, str]:
    suffix = re.sub(r"[^A-Z0-9]+", "_", CLIENT_KEY.upper()).strip("_")
    client_id = env_value(f"OZON_SELLER_CLIENT_ID_{suffix}")
    api_key = env_value(f"OZON_SELLER_API_KEY_{suffix}")
    if not client_id or not api_key:
        raise PipelineError(f"Для {CLIENT_KEY} не сохранены Seller API Client-Id/Api-Key")
    return client_id, api_key


def performance_credentials() -> tuple[str, str]:
    suffix = re.sub(r"[^A-Z0-9]+", "_", CLIENT_KEY.upper()).strip("_")
    client_id = env_value(f"OZON_PERFORMANCE_CLIENT_ID_{suffix}")
    client_secret = env_value(f"OZON_PERFORMANCE_CLIENT_SECRET_{suffix}")
    if not client_id:
        client_id = app.registered_client_credential(CLIENT_KEY, "ozon_performance_client_id")
    if not client_secret:
        client_secret = app.registered_client_credential(CLIENT_KEY, "ozon_performance_client_secret")
    if (not client_id or not client_secret) and CLIENT_KEY in getattr(app, "ADMIN_CLIENTS", {}):
        fallback_id, fallback_secret = app.ozon_performance_credentials_value(CLIENT_KEY)
        client_id = client_id or fallback_id
        client_secret = client_secret or fallback_secret
    if not client_id or not client_secret:
        raise PipelineError(f"Для рекламы {CLIENT_KEY} нужны Performance API Client ID/Client Secret")
    return client_id, client_secret

def seller_headers() -> dict[str, str]:
    client_id, api_key = seller_credentials()
    return {"Client-Id": client_id, "Api-Key": api_key}


def connect_km():
    config = dict(app.read_db_config())
    config["database"] = TARGET_DB
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    if actual != TARGET_DB:
        conn.close()
        raise PipelineError(
            f"Защита клиента: ожидалась БД {TARGET_DB}, подключение открыло {actual}"
        )
    return conn


def validate_date_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise PipelineError("Дата начала позже даты окончания")
    yesterday = date.today() - timedelta(days=1)
    if date_to > yesterday:
        raise PipelineError(f"Дата окончания не может быть позже вчерашнего дня ({yesterday})")


def resolve_stock_snapshot_date(
    requested_from: date | None = None,
    requested_to: date | None = None,
    *,
    actual_date: date | None = None,
) -> date:
    """Return the observation day; Seller stock API cannot backfill history."""
    del requested_from, requested_to
    return actual_date or date.today()


def default_range(conn, step: str) -> tuple[date, date]:
    table = "ozon_adv_daily_raw" if step == "advertising" else "ozon_funnel_daily"
    with conn.cursor() as cur:
        cur.execute(f"SELECT max(report_date) FROM public.{table}")
        last_date = cur.fetchone()[0]
    end = date.today() - timedelta(days=1)
    start = (last_date + timedelta(days=1)) if last_date else end
    return start, end


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def performance_date_windows(
    date_from: date,
    date_to: date,
    max_days: int = PERFORMANCE_MAX_WINDOW_DAYS,
) -> list[tuple[date, date]]:
    if date_from > date_to:
        raise PipelineError("Дата начала позже даты окончания")
    window_days = min(62, max(1, int(max_days)))
    windows: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        window_to = min(date_to, current + timedelta(days=window_days - 1))
        windows.append((current, window_to))
        current = window_to + timedelta(days=1)
    return windows


def decimal_value(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise PipelineError(f"Некорректное число в ответе Ozon API: {value!r}") from exc


def int_value(value: Any) -> int:
    return int(decimal_value(value))


def dimension_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("value") or value.get("name") or "").strip()
    return str(value or "").strip()


def ensure_funnel_api_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_funnel_daily (
                row_key text PRIMARY KEY,
                id bigserial,
                report_date date NOT NULL,
                period_from date,
                period_to date,
                product_name text,
                seller_article text,
                category_level_1 text,
                category_level_2 text,
                category_level_3 text,
                brand text,
                model text,
                work_schema text,
                sku text,
                barcode text,
                abc_orders_amount text,
                abc_orders_qty text,
                ordered_amount_rub numeric,
                ordered_amount_dynamic numeric,
                search_catalog_position numeric,
                search_catalog_position_dynamic numeric,
                impressions_total bigint,
                impressions_total_dynamic numeric,
                impressions_search_catalog bigint,
                impressions_search_catalog_dynamic numeric,
                card_visits bigint,
                card_visits_dynamic numeric,
                cart_adds bigint,
                cart_adds_dynamic numeric,
                ordered_units bigint,
                ordered_units_dynamic numeric,
                source_file text,
                source_sheet text,
                source_row_num integer,
                imported_at timestamp without time zone DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_funnel_import_files (
                source_file text PRIMARY KEY,
                period_from date,
                period_to date,
                rows_imported integer,
                file_size_bytes bigint,
                file_mtime timestamp without time zone,
                imported_at timestamp without time zone DEFAULT now(),
                status text,
                error text
            )
            """
        )
        for column_sql in (
            "ADD COLUMN IF NOT EXISTS impressions_card bigint",
            "ADD COLUMN IF NOT EXISTS cart_adds_search_catalog bigint",
            "ADD COLUMN IF NOT EXISTS cart_adds_card bigint",
            "ADD COLUMN IF NOT EXISTS sessions_total bigint",
            "ADD COLUMN IF NOT EXISTS sessions_search_catalog bigint",
            "ADD COLUMN IF NOT EXISTS sessions_card bigint",
            "ADD COLUMN IF NOT EXISTS returned_units bigint",
            "ADD COLUMN IF NOT EXISTS cancelled_units bigint",
            "ADD COLUMN IF NOT EXISTS delivered_units bigint",
            "ADD COLUMN IF NOT EXISTS funnel_contract text",
        ):
            cur.execute(f"ALTER TABLE public.ozon_funnel_daily {column_sql}")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_seller_capabilities (
                client_key text PRIMARY KEY,
                checked_at timestamp without time zone NOT NULL DEFAULT now(),
                premium boolean NOT NULL DEFAULT false,
                premium_plus boolean NOT NULL DEFAULT false,
                analytics_mode text NOT NULL,
                contract_key text NOT NULL,
                available_metrics text[] NOT NULL DEFAULT ARRAY[]::text[],
                evidence text NOT NULL,
                last_error text
            )
            """
        )
    conn.commit()


def funnel_metric_count(payload: Any) -> int:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    lengths: list[int] = []
    totals = result.get("totals") if isinstance(result, dict) else None
    if isinstance(totals, list):
        lengths.append(len(totals))
    for row in (result.get("data") or []) if isinstance(result, dict) else []:
        values = row.get("metrics") if isinstance(row, dict) else None
        if isinstance(values, list):
            lengths.append(len(values))
    return max(lengths, default=0)


def cached_funnel_capability(conn) -> FunnelCapability | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT analytics_mode, contract_key, available_metrics,
                   premium, premium_plus, evidence
            FROM public.ozon_seller_capabilities
            WHERE client_key = %s
              AND analytics_mode IN ('standard', 'advanced')
            """,
            (CLIENT_KEY,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return FunnelCapability(
        mode=row[0],
        contract_key=row[1],
        metrics=tuple(row[2] or []),
        premium=bool(row[3]),
        premium_plus=bool(row[4]),
        initial_payload=None,
        evidence=f"cached:{row[5]}",
    )


def store_funnel_capability(conn, capability: FunnelCapability, last_error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.ozon_seller_capabilities (
                client_key, checked_at, premium, premium_plus,
                analytics_mode, contract_key, available_metrics, evidence, last_error
            ) VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (client_key) DO UPDATE SET
                checked_at = now(), premium = EXCLUDED.premium,
                premium_plus = EXCLUDED.premium_plus,
                analytics_mode = EXCLUDED.analytics_mode,
                contract_key = EXCLUDED.contract_key,
                available_metrics = EXCLUDED.available_metrics,
                evidence = EXCLUDED.evidence,
                last_error = EXCLUDED.last_error
            """,
            (
                CLIENT_KEY,
                capability.premium,
                capability.premium_plus,
                capability.mode,
                capability.contract_key,
                list(capability.metrics),
                capability.evidence,
                last_error[:2000] if last_error else None,
            ),
        )
    conn.commit()


def analytics_payload(
    date_from: date,
    date_to: date,
    metrics: Iterable[str],
    *,
    limit: int = FUNNEL_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "metrics": list(metrics),
        "dimension": ["sku", "day"],
        "filters": [],
        "sort": [{"key": "day", "order": "ASC"}],
        "limit": limit,
        "offset": offset,
    }


def detect_funnel_capability(
    conn,
    api: SerialApiClient,
    date_from: date,
    date_to: date,
) -> FunnelCapability:
    probe_from, probe_to, reuse_probe = capability_probe_range(date_from, date_to)
    try:
        rating = api.request(
            "POST",
            f"{SELLER_BASE_URL}/v1/rating/summary",
            headers=seller_headers(),
            payload={},
            bucket="seller-default",
            interval=SELLER_DEFAULT_INTERVAL,
        ).json()
        premium = bool(rating.get("premium")) if isinstance(rating, dict) else False
        premium_plus = bool(rating.get("premium_plus")) if isinstance(rating, dict) else False
        probe = api.request(
            "POST",
            f"{SELLER_BASE_URL}/v1/analytics/data",
            headers=seller_headers(),
            payload=analytics_payload(probe_from, probe_to, FUNNEL_ADVANCED_METRICS),
            bucket="seller-analytics-data",
            interval=SELLER_ANALYTICS_INTERVAL,
            context=f"preflight capability | окно {probe_from.isoformat()}..{probe_to.isoformat()}",
        ).json()
    except PipelineError as exc:
        conn.rollback()
        cached = cached_funnel_capability(conn)
        if cached is None:
            raise
        print(
            f"ПРОГРЕСС: capability Ozon | live preflight недоступен | "
            f"используется последний подтверждённый режим={cached.mode} | "
            f"причина={str(exc)[:300]}",
            flush=True,
        )
        return cached

    metric_count = funnel_metric_count(probe)
    if metric_count >= len(FUNNEL_ADVANCED_METRICS):
        capability = FunnelCapability(
            "advanced",
            FUNNEL_ADVANCED_CONTRACT,
            tuple(FUNNEL_ADVANCED_METRICS),
            premium,
            premium_plus,
            probe if reuse_probe else None,
            f"rating+analytics:{metric_count}",
        )
    elif metric_count == len(FUNNEL_STANDARD_METRICS) and not premium_plus:
        capability = FunnelCapability(
            "standard",
            FUNNEL_STANDARD_CONTRACT,
            tuple(FUNNEL_STANDARD_METRICS),
            premium,
            premium_plus,
            probe if reuse_probe else None,
            f"rating+analytics-truncated:{metric_count}",
        )
    elif premium_plus:
        raise PipelineError(
            "Ozon подтвердил Premium Plus, но /v1/analytics/data не вернул "
            f"все {len(FUNNEL_ADVANCED_METRICS)} метрик (получено {metric_count})"
        )
    else:
        raise PipelineError(
            "Не удалось определить доступ к расширенной аналитике Ozon: "
            f"ответ содержит {metric_count} значений метрик"
        )
    store_funnel_capability(conn, capability)
    print(
        f"ПРОГРЕСС: capability Ozon | premium={premium} | "
        f"premium_plus={premium_plus} | режим={capability.mode} | "
        f"контракт={capability.contract_key} | метрик={len(capability.metrics)}",
        flush=True,
    )
    return capability

def standard_history_cutoff(today: date | None = None) -> date:
    current = today or date.today()
    month_index = current.year * 12 + current.month - 1 - 3
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def capability_probe_range(
    date_from: date,
    date_to: date,
    *,
    today: date | None = None,
) -> tuple[date, date, bool]:
    current = today or date.today()
    yesterday = current - timedelta(days=1)
    cutoff = standard_history_cutoff(current)
    if date_from >= cutoff and date_to <= yesterday:
        return date_from, date_to, True
    probe_to = yesterday
    probe_from = max(cutoff, probe_to - timedelta(days=6))
    return probe_from, probe_to, False


def effective_funnel_range(
    capability: FunnelCapability,
    date_from: date,
    date_to: date,
) -> tuple[date | None, date]:
    if capability.mode != "standard":
        return date_from, date_to
    cutoff = standard_history_cutoff()
    effective_from = max(date_from, cutoff)
    if effective_from > date_to:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: Standard-воронка Ozon пропущена | "
            f"выбранный период {date_from}..{date_to} раньше доступной даты {cutoff} | "
            "остальные шаги pipeline продолжаются",
            flush=True,
        )
        return None, date_to
    if effective_from != date_from:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: Standard-воронка Ozon ограничена периодом "
            f"{effective_from}..{date_to} вместо {date_from}..{date_to} | "
            "показы, сессии и корзины для более ранних дат Seller API не предоставляет",
            flush=True,
        )
    return effective_from, date_to


def parse_funnel_page(payload: Any, metrics: list[str]) -> list[dict[str, Any]]:
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    data = result.get("data", []) if isinstance(result, dict) else []
    rows: list[dict[str, Any]] = []
    for item in data or []:
        dimensions = item.get("dimensions") or []
        values = item.get("metrics") or []
        if len(dimensions) < 2 or len(values) != len(metrics):
            raise PipelineError(
                "Неожиданная структура строки /v1/analytics/data: "
                f"ожидалось метрик {len(metrics)}, получено {len(values)}"
            )
        sku = dimension_id(dimensions[0])
        report_day = dimension_id(dimensions[1])[:10]
        if not sku or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_day):
            raise PipelineError("В ответе воронки нет ожидаемых измерений sku/day")
        metric_values = dict(zip(metrics, values))
        advanced = "hits_view" in metric_values
        rows.append(
            {
                "report_date": date.fromisoformat(report_day),
                "sku": sku,
                "ordered_amount_rub": decimal_value(metric_values.get("revenue")),
                "ordered_units": int_value(metric_values.get("ordered_units")),
                "impressions_total": int_value(metric_values.get("hits_view")) if advanced else None,
                "impressions_search_catalog": int_value(metric_values.get("hits_view_search")) if advanced else None,
                "impressions_card": int_value(metric_values.get("hits_view_pdp")) if advanced else None,
                "card_visits": int_value(metric_values.get("session_view_pdp")) if advanced else None,
                "cart_adds": int_value(metric_values.get("hits_tocart")) if advanced else None,
                "cart_adds_search_catalog": int_value(metric_values.get("hits_tocart_search")) if advanced else None,
                "cart_adds_card": int_value(metric_values.get("hits_tocart_pdp")) if advanced else None,
                "sessions_total": int_value(metric_values.get("session_view")) if advanced else None,
                "sessions_search_catalog": int_value(metric_values.get("session_view_search")) if advanced else None,
                "sessions_card": int_value(metric_values.get("session_view_pdp")) if advanced else None,
                "returned_units": int_value(metric_values.get("returns")) if advanced else None,
                "cancelled_units": int_value(metric_values.get("cancellations")) if advanced else None,
                "delivered_units": int_value(metric_values.get("delivered_units")) if advanced else None,
            }
        )
    return rows


def funnel_page_context(page: list[dict[str, Any]], fallback_from: date, fallback_to: date) -> str:
    page_dates = sorted(
        {
            row.get("report_date")
            for row in page
            if isinstance(row.get("report_date"), date)
        }
    )
    if page_dates:
        first_day = page_dates[0].isoformat()
        last_day = page_dates[-1].isoformat()
        if first_day == last_day:
            return f"текущая дата {first_day}"
        return f"текущие даты {first_day}..{last_day}"
    return f"окно {fallback_from.isoformat()}..{fallback_to.isoformat()}"


def fetch_funnel(
    api: SerialApiClient,
    date_from: date,
    date_to: date,
    *,
    metrics: Iterable[str] | None = None,
    initial_payload: Any = None,
    on_page: Any = None,
    start_offset: int = 0,
    database_rows_before_run: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    requested_metrics = list(metrics or FUNNEL_METRICS)
    rows: list[dict[str, Any]] = []
    offset = max(0, int(start_offset))
    accumulated = offset
    request_number = offset // FUNNEL_PAGE_SIZE
    reusable_payload = initial_payload if offset == 0 else None
    api.progress.event(
        f"ПРОГРЕСС: Ozon /v1/analytics/data | метрик {len(requested_metrics)} | "
        f"режим={'advanced' if len(requested_metrics) > 2 else 'standard'}",
    )
    api.set_context(
        "seller-analytics-data",
        f"окно {date_from.isoformat()}..{date_to.isoformat()} | offset {offset}",
    )
    while True:
        request_number += 1
        request_context = f"окно {date_from.isoformat()}..{date_to.isoformat()} | offset {offset}"
        api.progress.update(
            f"ПРОГРЕСС: запрос {request_number}/? | воронка {date_from}..{date_to} | "
            f"{request_context} | получено в текущем API-окне {accumulated:,}"
            + (
                f" | в БД до запуска {database_rows_before_run:,}"
                if database_rows_before_run is not None
                else ""
            ),
        )
        if reusable_payload is not None:
            payload = reusable_payload
            reusable_payload = None
        else:
            payload = api.request(
                "POST",
                f"{SELLER_BASE_URL}/v1/analytics/data",
                headers=seller_headers(),
                payload=analytics_payload(
                    date_from,
                    date_to,
                    requested_metrics,
                    offset=offset,
                ),
                bucket="seller-analytics-data",
                interval=SELLER_ANALYTICS_INTERVAL,
            ).json()
        page = parse_funnel_page(payload, requested_metrics)
        page_context = funnel_page_context(page, date_from, date_to)
        api.set_context(
            "seller-analytics-data",
            f"{page_context} | следующий offset {offset + len(page)}",
        )
        if on_page is None:
            rows.extend(page)
        else:
            on_page(page, offset, request_number)
        accumulated += len(page)
        if len(page) < FUNNEL_PAGE_SIZE:
            break
        offset += FUNNEL_PAGE_SIZE
    return rows, request_number

def existing_manual_funnel_keys(conn, date_from: date, date_to: date) -> set[tuple[date, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT report_date, sku
            FROM public.ozon_funnel_daily
            WHERE report_date BETWEEN %s AND %s
              AND COALESCE(source_file, '') NOT LIKE %s
            """,
            (date_from, date_to, f"{API_SOURCE_PREFIX}%"),
        )
        return {(row[0], str(row[1] or "")) for row in cur.fetchall()}


def funnel_source(date_from: date, date_to: date, contract_key: str) -> str:
    return f"{API_SOURCE_PREFIX}seller/v1/analytics/data/{contract_key}/{date_from}/{date_to}"


def funnel_contract_state(
    conn,
    date_from: date,
    date_to: date,
    contract_key: str,
) -> tuple[int, date | None]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*), MAX(report_date)
            FROM public.ozon_funnel_daily
            WHERE report_date BETWEEN %s AND %s
              AND funnel_contract = %s
            """,
            (date_from, date_to, contract_key),
        )
        rows, latest_date = cur.fetchone()
    return int(rows or 0), latest_date


def store_funnel_page(
    conn,
    rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
    *,
    manual_keys: set[tuple[date, str]],
    contract_key: str = FUNNEL_STANDARD_CONTRACT,
    row_offset: int = 0,
    dry_run: bool,
) -> int:
    filtered = [row for row in rows if (row["report_date"], row["sku"]) not in manual_keys]
    if dry_run or not filtered:
        return len(filtered)
    source = funnel_source(date_from, date_to, contract_key)
    values = [
        (
            f"ozon-api:{row['report_date']}:{row['sku']}",
            row["report_date"], date_from, date_to, row["sku"],
            row["ordered_amount_rub"], row["impressions_total"],
            row["impressions_search_catalog"], row["impressions_card"],
            row["card_visits"], row["cart_adds"],
            row["cart_adds_search_catalog"], row["cart_adds_card"],
            row["sessions_total"], row["sessions_search_catalog"],
            row["sessions_card"], row["returned_units"],
            row["cancelled_units"], row["delivered_units"],
            row["ordered_units"], contract_key, source, "api", row_offset + index,
        )
        for index, row in enumerate(filtered, start=1)
    ]
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_funnel_daily (
                    row_key, report_date, period_from, period_to, sku,
                    ordered_amount_rub, impressions_total,
                    impressions_search_catalog, impressions_card, card_visits,
                    cart_adds, cart_adds_search_catalog, cart_adds_card,
                    sessions_total, sessions_search_catalog, sessions_card,
                    returned_units, cancelled_units, delivered_units,
                    ordered_units, funnel_contract, source_file, source_sheet, source_row_num
                ) VALUES %s
                ON CONFLICT (row_key) DO UPDATE SET
                    ordered_amount_rub = EXCLUDED.ordered_amount_rub,
                    impressions_total = EXCLUDED.impressions_total,
                    impressions_search_catalog = EXCLUDED.impressions_search_catalog,
                    impressions_card = EXCLUDED.impressions_card,
                    card_visits = EXCLUDED.card_visits,
                    cart_adds = EXCLUDED.cart_adds,
                    cart_adds_search_catalog = EXCLUDED.cart_adds_search_catalog,
                    cart_adds_card = EXCLUDED.cart_adds_card,
                    sessions_total = EXCLUDED.sessions_total,
                    sessions_search_catalog = EXCLUDED.sessions_search_catalog,
                    sessions_card = EXCLUDED.sessions_card,
                    returned_units = EXCLUDED.returned_units,
                    cancelled_units = EXCLUDED.cancelled_units,
                    delivered_units = EXCLUDED.delivered_units,
                    ordered_units = EXCLUDED.ordered_units,
                    funnel_contract = EXCLUDED.funnel_contract,
                    source_file = EXCLUDED.source_file,
                    source_sheet = EXCLUDED.source_sheet,
                    source_row_num = EXCLUDED.source_row_num,
                    imported_at = now()
                """,
                values,
                page_size=1000,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(values)


def record_funnel_import(
    conn,
    date_from: date,
    date_to: date,
    rows_imported: int,
    *,
    contract_key: str = FUNNEL_STANDARD_CONTRACT,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    source = funnel_source(date_from, date_to, contract_key)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.ozon_funnel_import_files (
                    source_file, period_from, period_to, rows_imported,
                    imported_at, status, error
                ) VALUES (%s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    rows_imported = EXCLUDED.rows_imported,
                    imported_at = now(), status = 'ok', error = NULL
                """,
                (source, date_from, date_to, rows_imported),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def finalize_funnel_contract(
    conn,
    date_from: date,
    date_to: date,
    contract_key: str,
    *,
    dry_run: bool,
) -> int:
    if dry_run:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.ozon_funnel_daily
            WHERE report_date BETWEEN %s AND %s
              AND COALESCE(source_file, '') LIKE %s
              AND funnel_contract IS DISTINCT FROM %s
            """,
            (date_from, date_to, f"{API_SOURCE_PREFIX}%", contract_key),
        )
        removed = cur.rowcount
    conn.commit()
    return removed


def checkpoint_run(conn, run_id: int, rows: int, requests: int, checkpoint_offset: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.km_ozon_api_runs
            SET rows_loaded = %s, requests_made = %s, checkpoint_offset = %s
            WHERE id = %s AND status = 'running'
            """,
            (rows, requests, checkpoint_offset, run_id),
        )
    conn.commit()


def funnel_resume_checkpoint(
    conn,
    date_from: date,
    date_to: date,
    *,
    run_id: int,
    contract_key: str = FUNNEL_STANDARD_CONTRACT,
    dry_run: bool,
) -> tuple[int, int]:
    if dry_run:
        return 0, 0
    source = funnel_source(date_from, date_to, contract_key)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(MAX(checkpoint_offset), 0)
            FROM public.km_ozon_api_runs
            WHERE id <> %s AND step = 'funnel'
              AND date_from = %s AND date_to = %s
              AND contract_key = %s
            """,
            (run_id, date_from, date_to, contract_key),
        )
        run_offset = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT COALESCE(MAX(source_row_num), 0)
            FROM public.ozon_funnel_daily
            WHERE source_file = %s
            """,
            (source,),
        )
        source_offset = int(cur.fetchone()[0] or 0)
        saved_offset = max(run_offset, source_offset)
        resume_offset = saved_offset // FUNNEL_CHECKPOINT_SIZE * FUNNEL_CHECKPOINT_SIZE
        if not resume_offset:
            return 0, 0
        cur.execute(
            """
            SELECT COUNT(*)
            FROM public.ozon_funnel_daily
            WHERE source_file = %s AND source_row_num <= %s
            """,
            (source, resume_offset),
        )
        saved_rows = int(cur.fetchone()[0] or 0)
    return resume_offset, saved_rows


def previous_funnel_speed(conn, run_id: int) -> float | None:
    """Return rows/sec from the latest usable funnel run for the ETA baseline."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rows_loaded, EXTRACT(EPOCH FROM (finished_at - started_at))
            FROM public.km_ozon_api_runs
            WHERE id <> %s
              AND step = 'funnel'
              AND rows_loaded > 0
              AND started_at IS NOT NULL
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()
    if not row or not row[1] or float(row[1]) <= 0:
        return None
    return float(row[0]) / float(row[1])


def estimate_funnel_target_rows(
    existing_rows: int,
    full_range_from: date,
    latest_saved_date: date | None,
    date_to: date,
) -> int | None:
    if existing_rows <= 0 or latest_saved_date is None:
        return None
    covered_days = (latest_saved_date - full_range_from).days + 1
    total_days = (date_to - full_range_from).days + 1
    if covered_days <= 0 or total_days <= covered_days:
        return existing_rows
    return max(existing_rows, math.ceil(existing_rows / covered_days * total_days))


def sync_funnel_incremental(
    conn,
    api: SerialApiClient,
    date_from: date,
    date_to: date,
    *,
    run_id: int,
    capability: FunnelCapability | None = None,
    dry_run: bool,
    overwrite: bool = False,
) -> tuple[int, int]:
    capability = capability or FunnelCapability(
        "standard",
        FUNNEL_STANDARD_CONTRACT,
        tuple(FUNNEL_STANDARD_METRICS),
        False,
        False,
        None,
        "legacy-default",
    )
    effective_from, date_to = effective_funnel_range(capability, date_from, date_to)
    if effective_from is None:
        return 0, 0
    full_range_from = effective_from
    date_from = full_range_from
    existing_rows, latest_saved_date = funnel_contract_state(
        conn,
        full_range_from,
        date_to,
        capability.contract_key,
    )
    if existing_rows:
        print(
            f"БАЗА: до запуска сохранено {existing_rows:,} строк воронки "
            f"контракта {capability.contract_key} за {full_range_from}..{date_to} | "
            f"последняя дата {latest_saved_date} | API-диапазон от {full_range_from} "
            "к дальней дате через checkpoint offset",
            flush=True,
        )
    else:
        print(
            f"БАЗА: до запуска нет строк воронки контракта {capability.contract_key} "
            f"за {full_range_from}..{date_to}",
            flush=True,
        )
    manual_keys = existing_manual_funnel_keys(conn, date_from, date_to)
    if overwrite:
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM public.ozon_funnel_daily
                    WHERE report_date BETWEEN %s AND %s
                      AND COALESCE(source_file, '') LIKE %s
                      AND funnel_contract = %s
                    """,
                    (date_from, date_to, f"{API_SOURCE_PREFIX}%", capability.contract_key),
                )
                removed_for_overwrite = cur.rowcount
            conn.commit()
        else:
            removed_for_overwrite = 0
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: режим перезаписи | удалено API-строк {removed_for_overwrite:,} | "
            "checkpoint сброшен | ручные источники сохранены",
            flush=True,
        )
        existing_rows = 0
        latest_saved_date = None
        resume_offset, loaded = 0, 0
    else:
        resume_offset, loaded = funnel_resume_checkpoint(
            conn,
            date_from,
            date_to,
            run_id=run_id,
            contract_key=capability.contract_key,
            dry_run=dry_run,
        )
    before_requests = api.request_count
    pending_rows: list[dict[str, Any]] = []
    pending_offset = resume_offset
    db_rows_total = existing_rows
    eta = FunnelEta(
        target_rows=estimate_funnel_target_rows(
            existing_rows,
            full_range_from,
            latest_saved_date,
            date_to,
        ),
        baseline_rows_per_second=previous_funnel_speed(conn, run_id),
    )
    if resume_offset:
        print(
            f"ПРОГРЕСС: возобновление | следующая страница {resume_offset // FUNNEL_PAGE_SIZE + 1} | "
            f"offset {resume_offset} | уже сохранено строк {loaded:,} | durable=yes",
            flush=True,
        )

    def persist_page(page, offset, request_number):
        nonlocal loaded, pending_offset, db_rows_total
        page_context = funnel_page_context(page, date_from, date_to)
        if not pending_rows:
            pending_offset = offset
        pending_rows.extend(page)
        is_final_page = len(page) < FUNNEL_PAGE_SIZE
        if len(pending_rows) < FUNNEL_CHECKPOINT_SIZE and not is_final_page:
            visible_loaded = loaded + len(pending_rows)
            visible_db_rows = db_rows_total + len(pending_rows)
            visible_speed = eta.speed(visible_loaded)
            api.progress.update(
                f"ПРОГРЕСС: буфер checkpoint | страница {request_number} | "
                f"{page_context} | "
                f"загружено {visible_loaded:,} | в БД {visible_db_rows:,} | "
                f"запросов {api.request_count - before_requests} | "
                f"осталось ~{format_eta(eta.remaining_seconds(visible_loaded, visible_db_rows))} | "
                f"скорость {visible_speed:.1f} стр/с"
                if visible_speed
                else
                f"загружено {visible_loaded:,} | в БД {visible_db_rows:,} | "
                f"запросов {api.request_count - before_requests} | осталось ~—",
            )
            return
        if not pending_rows:
            return
        batch_rows = list(pending_rows)
        batch_offset = pending_offset
        loaded += store_funnel_page(
            conn,
            batch_rows,
            date_from,
            date_to,
            manual_keys=manual_keys,
            contract_key=capability.contract_key,
            row_offset=batch_offset,
            dry_run=dry_run,
        )
        db_rows_total, _ = funnel_contract_state(
            conn,
            full_range_from,
            date_to,
            capability.contract_key,
        )
        requests = api.request_count - before_requests
        next_offset = offset + len(page)
        checkpoint_run(conn, run_id, loaded, requests, next_offset)
        total_days = max(1, (date_to - date_from).days + 1)
        page_dates = [row.get("report_date") for row in batch_rows if row.get("report_date")]
        page_to = max(page_dates) if page_dates else date_from
        if eta.target_rows is None and page_dates:
            observed_days = max(1, (page_to - full_range_from).days + 1)
            total_days = max(1, (date_to - full_range_from).days + 1)
            observed_rows = max(db_rows_total, existing_rows + loaded)
            eta.target_rows = max(
                observed_rows,
                math.ceil(observed_rows / observed_days * total_days),
            )
        covered_days = max(0, min(total_days, (page_to - date_from).days + 1))
        step_pct = 100.0 if len(page) < FUNNEL_PAGE_SIZE else covered_days / total_days * 100
        speed = eta.speed(loaded)
        eta_seconds = eta.remaining_seconds(loaded, db_rows_total)
        api.progress.update(
            f"ПРОГРЕСС: checkpoint | страница {request_number} | "
            f"обработано за запуск {loaded:,} | всего в БД {db_rows_total:,} | "
            f"уже в БД {db_rows_total:,} | "
            f"запросов {requests} | "
            f"{funnel_page_context(batch_rows, date_from, date_to)} | "
            f"период до {page_to.isoformat()} | step_pct={step_pct:.1f} | "
            f"осталось ~{format_eta(eta_seconds)} | скорость {speed:.1f} стр/с | "
            f"прошло {format_eta(eta.elapsed())} | durable=yes"
            if speed
            else
            f"период до {page_to.isoformat()} | step_pct={step_pct:.1f} | "
            f"осталось ~{format_eta(eta_seconds)} | прошло {format_eta(eta.elapsed())} | durable=yes",
        )
        pending_rows.clear()
        pending_offset = next_offset

    fetch_funnel(
        api,
        date_from,
        date_to,
        metrics=capability.metrics,
        initial_payload=(
            capability.initial_payload if date_from == full_range_from else None
        ),
        on_page=persist_page,
        start_offset=resume_offset,
        database_rows_before_run=existing_rows,
    )
    requests = api.request_count - before_requests
    record_funnel_import(
        conn,
        date_from,
        date_to,
        loaded,
        contract_key=capability.contract_key,
        dry_run=dry_run,
    )
    removed = finalize_funnel_contract(
        conn,
        full_range_from,
        date_to,
        capability.contract_key,
        dry_run=dry_run,
    )
    if removed:
        api.progress.event(
            f"ПРОГРЕСС: финализация воронки | удалено устаревших API-строк {removed:,}",
        )
    api.progress.event(
        f"ПРОГРЕСС: воронка завершена | загружено {loaded:,} | "
        f"всего в БД {db_rows_total:,} | запросов {requests} | осталось 0с",
    )
    return loaded, requests


def store_funnel(
    conn,
    rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
    *,
    contract_key: str = FUNNEL_STANDARD_CONTRACT,
    dry_run: bool,
) -> int:
    manual_keys = existing_manual_funnel_keys(conn, date_from, date_to)
    return store_funnel_page(
        conn,
        rows,
        date_from,
        date_to,
        manual_keys=manual_keys,
        contract_key=contract_key,
        dry_run=dry_run,
    )

def parse_stock_page(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    modern_items = payload.get("items")
    if not isinstance(modern_items, list):
        result = payload.get("result")
        modern_items = result.get("items") if isinstance(result, dict) else None
    if isinstance(modern_items, list):
        parsed: list[dict[str, Any]] = []
        for item in modern_items:
            if not isinstance(item, dict):
                continue
            article = str(item.get("offer_id") or "").strip() or None
            product_id = str(item.get("product_id") or "").strip()
            for stock in item.get("stocks") or []:
                if not isinstance(stock, dict):
                    continue
                sku = str(stock.get("sku") or product_id).strip()
                if not sku:
                    continue
                stock_type = str(stock.get("type") or "unknown").strip().lower() or "unknown"
                shipment_type = str(stock.get("shipment_type") or "").strip()
                warehouse_ids = [
                    str(value).strip()
                    for value in (stock.get("warehouse_ids") or [])
                    if str(value).strip()
                ]
                parsed.append(
                    {
                        "sku": sku,
                        "article": article,
                        "product_name": None,
                        "warehouse_name": stock_type.upper(),
                        "cluster_name": shipment_type or None,
                        "available_to_sell": int_value(stock.get("present", 0)),
                        "preparing_to_sell": 0,
                        "reserved": int_value(stock.get("reserved", 0)),
                        "stock_type": stock_type,
                        "warehouse_ids": warehouse_ids,
                    }
                )
        return parsed

    result = payload.get("result", payload)
    rows = result.get("rows") or [] if isinstance(result, dict) else []
    parsed: list[dict[str, Any]] = []
    for item in rows:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        parsed.append(
            {
                "sku": sku,
                "article": str(item.get("item_code") or item.get("offer_id") or "").strip() or None,
                "product_name": str(item.get("item_name") or item.get("name") or "").strip() or None,
                "warehouse_name": str(item.get("warehouse_name") or "").strip() or None,
                "cluster_name": str(item.get("cluster_name") or "").strip() or None,
                "available_to_sell": int_value(
                    item.get("free_to_sell_amount", item.get("available_stock_count", 0))
                ),
                "preparing_to_sell": int_value(item.get("promised_amount", 0)),
                "reserved": int_value(item.get("reserved_amount", 0)),
            }
        )
    return parsed


def fetch_stock(api: SerialApiClient) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    request_number = 0
    fetched_items = 0
    while True:
        request_number += 1
        print(
            f"ПРОГРЕСС: запрос {request_number}/? | остатки v4 | "
            f"cursor {'есть' if cursor else 'старт'} | "
            f"накоплено {len(rows):,}",
            flush=True,
        )
        response = api.request(
            "POST",
            f"{SELLER_BASE_URL}/v4/product/info/stocks",
            headers=seller_headers(),
            payload={
                "cursor": cursor,
                "filter": {"visibility": "ALL"},
                "limit": STOCK_PAGE_SIZE,
            },
            bucket="seller-stock",
            interval=SELLER_DEFAULT_INTERVAL,
        )
        payload = response.json()
        page = parse_stock_page(payload)
        rows.extend(page)
        result = payload.get("result") if isinstance(payload, dict) else None
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list) and isinstance(result, dict):
            raw_items = result.get("items")
        page_items = len(raw_items) if isinstance(raw_items, list) else len(page)
        fetched_items += page_items
        next_cursor = str(
            payload.get("cursor")
            or (result.get("cursor") if isinstance(result, dict) else "")
            or ""
        ).strip()
        total = int_value(payload.get("total", 0))
        print(
            f"ПРОГРЕСС: остатки v4 | страница {request_number} | "
            f"товаров {page_items:,} | строк остатков {len(page):,} | "
            f"накоплено товаров {fetched_items:,}/{total or '?'} | "
            f"строк остатков {len(rows):,}",
            flush=True,
        )
        if total and fetched_items >= total:
            break
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            if total and fetched_items < total:
                raise PipelineError(f"Неполный снимок Ozon: получено {fetched_items} из {total} товаров. Предыдущие остатки и архив сохранены.")
            if next_cursor and (next_cursor == cursor or next_cursor in seen_cursors):
                raise PipelineError("Ozon повторил курсор остатков; неполный снимок не сохранён")
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if not rows:
        raise PipelineError(
            "Ozon вернул пустой снимок остатков; текущие таблицы не изменены"
        )
    return rows, request_number


def aggregate_stock(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = grouped.setdefault(
            row["sku"],
            {
                "sku": row["sku"],
                "article": row["article"],
                "product_name": row["product_name"],
                "available_to_sell": 0,
                "preparing_to_sell": 0,
            },
        )
        target["article"] = target["article"] or row["article"]
        target["product_name"] = target["product_name"] or row["product_name"]
        target["available_to_sell"] += row["available_to_sell"]
        target["preparing_to_sell"] += row["preparing_to_sell"]
    return list(grouped.values())


def enrich_stock_metadata(
    conn, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not rows:
        return [], {"source_skus": 0, "matched_skus": 0, "filled_fields": 0}
    skus = sorted({str(row.get("sku") or "").strip() for row in rows} - {""})
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.ozon_cat_products')")
        if not cur.fetchone()[0]:
            return rows, {
                "source_skus": len(skus),
                "matched_skus": 0,
                "filled_fields": 0,
            }
        cur.execute(
            """
            SELECT DISTINCT ON (trim(sku))
                   trim(sku) AS sku,
                   nullif(trim(artikul), '') AS seller_article,
                   nullif(trim(nazvanie_tovara), '') AS product_name,
                   nullif(trim(category_name), '') AS category_name
            FROM public.ozon_cat_products
            WHERE nullif(trim(sku), '') IS NOT NULL
              AND trim(sku) = ANY(%s)
            ORDER BY trim(sku), updated_at DESC NULLS LAST,
                     imported_at DESC NULLS LAST, product_id DESC
            """,
            (skus,),
        )
        metadata = {
            str(sku): {
                "article": seller_article,
                "product_name": product_name,
                "category_name": category_name,
            }
            for sku, seller_article, product_name, category_name in cur.fetchall()
        }
    enriched: list[dict[str, Any]] = []
    filled_fields = 0
    for source_row in rows:
        row = dict(source_row)
        sku = str(row.get("sku") or "").strip()
        reference = metadata.get(sku) or {}
        for target_key in ("article", "product_name", "category_name"):
            if not str(row.get(target_key) or "").strip() and reference.get(target_key):
                row[target_key] = reference[target_key]
                filled_fields += 1
        enriched.append(row)
    return enriched, {
        "source_skus": len(skus),
        "matched_skus": len(metadata),
        "filled_fields": filled_fields,
    }


def store_stock(
    conn,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool,
    snapshot_date: date | None = None,
) -> int:
    snapshot_day = snapshot_date or date.today()
    if not rows:
        raise PipelineError("Пустой снимок: текущие остатки и история не изменены")
    if dry_run:
        return len(rows)
    rows, metadata_stats = enrich_stock_metadata(conn, rows)
    products = aggregate_stock(rows)
    source = f"{API_SOURCE_PREFIX}seller/v4/product/info/stocks/{datetime.now():%Y-%m-%dT%H:%M:%S}"
    try:
        inventory_history.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE public.ozon_stock_products,
                         public.ozon_stock_product_clusters,
                         public.ozon_stock_product_warehouses,
                         public.ozon_stock_clusters
                """
            )
            cur.execute(
                """
                DELETE FROM public.inventory_history_daily
                WHERE marketplace = 'ozon' AND snapshot_date = %s
                """,
                (snapshot_day,),
            )
            replaced_history_rows = cur.rowcount
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_stock_products (
                    source_file, source_sheet, source_row_num, article,
                    product_name, sku, available_to_sell, preparing_to_sell
                ) VALUES %s
                """,
                [
                    (
                        source,
                        "api-products",
                        index,
                        row["article"],
                        row["product_name"],
                        row["sku"],
                        row["available_to_sell"],
                        row["preparing_to_sell"],
                    )
                    for index, row in enumerate(products, start=1)
                ],
            )
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_stock_product_warehouses (
                    source_file, source_sheet, source_row_num, article,
                    product_name, sku, cluster_name, warehouse_name,
                    available_to_sell, preparing_to_sell
                ) VALUES %s
                """,
                [
                    (
                        source,
                        "api-warehouses",
                        index,
                        row["article"],
                        row["product_name"],
                        row["sku"],
                        row["cluster_name"],
                        row["warehouse_name"],
                        row["available_to_sell"],
                        row["preparing_to_sell"],
                    )
                    for index, row in enumerate(rows, start=1)
                ],
            )
            cluster_rows: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                if not row["cluster_name"]:
                    continue
                key = (row["sku"], row["cluster_name"])
                target = cluster_rows.setdefault(
                    key,
                    {
                        **row,
                        "available_to_sell": 0,
                        "preparing_to_sell": 0,
                    },
                )
                target["available_to_sell"] += row["available_to_sell"]
                target["preparing_to_sell"] += row["preparing_to_sell"]
            if cluster_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO public.ozon_stock_product_clusters (
                        source_file, source_sheet, source_row_num, article,
                        product_name, sku, cluster_name,
                        available_to_sell, preparing_to_sell
                    ) VALUES %s
                    """,
                    [
                        (
                            source,
                            "api-clusters",
                            index,
                            row["article"],
                            row["product_name"],
                            row["sku"],
                            row["cluster_name"],
                            row["available_to_sell"],
                            row["preparing_to_sell"],
                        )
                        for index, row in enumerate(cluster_rows.values(), start=1)
                    ],
                )
        inventory_history.store_ozon_api_snapshot(
            conn,
            rows,
            snapshot_day,
            source,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(
        "ПРОГРЕСС: история остатков Ozon | "
        f"snapshot_date={snapshot_day} | replace_same_day=true | "
        "previous_days_preserved=true | "
        f"удалено старых строк {replaced_history_rows:,} | "
        f"SKU источника {metadata_stats['source_skus']:,} | "
        f"SKU со справочником {metadata_stats['matched_skus']:,} | "
        f"заполнено полей метаданных {metadata_stats['filled_fields']:,}",
        flush=True,
    )
    return len(rows)


def performance_token(api: SerialApiClient) -> str:
    client_id, client_secret = performance_credentials()
    response = api.request(
        "POST",
        f"{PERFORMANCE_BASE_URL}/api/client/token",
        payload={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        bucket="performance-token",
        interval=PERFORMANCE_INTERVAL,
    )
    token = str(response.json().get("access_token") or "").strip()
    if not token:
        raise PipelineError("Performance API не вернул access_token")
    return token


def performance_campaign_rows(api: SerialApiClient, token: str) -> list[dict[str, Any]]:
    response = api.request(
        "GET",
        f"{PERFORMANCE_BASE_URL}/api/client/campaign",
        headers={"Authorization": f"Bearer {token}"},
        bucket="performance-campaigns",
        interval=PERFORMANCE_INTERVAL,
    )
    payload = response.json()
    raw = payload.get("list", []) if isinstance(payload, dict) else payload
    rows = [item for item in (raw or []) if isinstance(item, dict)]
    if not rows:
        raise PipelineError("Performance API не вернул рекламные кампании")
    return rows


def performance_campaigns(api: SerialApiClient, token: str) -> list[str]:
    return sorted(
        {
            str(item.get("id") or item.get("campaignId") or "").strip()
            for item in performance_campaign_rows(api, token)
        }
        - {""}
    )


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_value(item: dict[str, Any], names: Iterable[str]) -> Any:
    normalized = {re.sub(r"[^a-z0-9]", "", str(key).lower()): value for key, value in item.items()}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in normalized:
            return normalized[key]
    return None


def parse_performance_product_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in walk_dicts(payload):
        raw_date = first_value(item, ("date", "day", "reportDate"))
        raw_sku = first_value(item, ("sku", "productId", "ozonId", "article"))
        if raw_date is None or raw_sku is None:
            continue
        day_text = str(raw_date)[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
            continue
        sku = str(raw_sku).strip()
        campaign = str(first_value(item, ("campaignId", "campaign", "id")) or "").strip()
        key = (day_text, sku, campaign)
        if not sku or sku.startswith("campaign:") or key in seen:
            continue
        seen.add(key)
        impressions = int_value(first_value(item, ("impressions", "views", "shows")) or 0)
        clicks = int_value(first_value(item, ("clicks",)) or 0)
        expense = decimal_value(first_value(item, ("moneySpent", "spend", "expense", "cost")) or 0)
        carts = int_value(first_value(item, ("addToCart", "addedToCart", "cartAdds", "toCart")) or 0)
        direct_orders = int_value(first_value(item, ("orders", "ordersQty", "orderCount")) or 0)
        direct_amount = decimal_value(
            first_value(item, ("revenue", "ordersMoney", "ordersAmount", "sales")) or 0
        )
        model_orders = int_value(
            first_value(item, ("modelOrders", "modelOrderCount", "ordersModel")) or 0
        )
        model_amount = decimal_value(
            first_value(item, ("modelSales", "modelRevenue", "modelOrdersMoney", "salesModel")) or 0
        )
        total_orders = direct_orders + model_orders
        total_amount = direct_amount + model_amount
        seller_article = first_value(item, ("offerId", "sellerArticle", "offer"))
        ctr = (Decimal(clicks) / Decimal(impressions) * 100) if impressions else None
        cr = (Decimal(total_orders) / Decimal(clicks) * 100) if clicks else None
        cpc = (expense / Decimal(clicks)) if clicks else None
        cpm = (expense / Decimal(impressions) * 1000) if impressions else None
        cpa = (expense / Decimal(total_orders)) if total_orders else None
        drr = (expense / total_amount * 100) if total_amount else None
        rows.append(
            {
                "report_date": date.fromisoformat(day_text),
                "sku": sku,
                "seller_article": str(seller_article).strip() if seller_article else None,
                "campaign_id": campaign or None,
                "impressions": impressions,
                "clicks": clicks,
                "ctr_pct": ctr,
                "expense_rub": expense,
                "cpc_rub": cpc,
                "cpm_rub": cpm,
                "added_to_cart": carts,
                "orders_qty": total_orders,
                "direct_orders_qty": direct_orders,
                "indirect_orders_qty": model_orders,
                "cr_pct": cr,
                "orders_amount_rub": total_amount,
                "direct_orders_amount_rub": direct_amount,
                "indirect_orders_amount_rub": model_amount,
                "cpa_rub": cpa,
                "drr_pct": drr,
                "total_orders_qty": total_orders,
                "total_orders_amount_rub": total_amount,
                "total_cpa_rub": cpa,
                "total_drr_pct": drr,
            }
        )
    return rows


def parse_performance_campaign_rows(payload: Any) -> list[dict[str, Any]]:
    raw_rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    for item in raw_rows or []:
        if not isinstance(item, dict):
            continue
        campaign_id = str(first_value(item, ("id", "campaignId")) or "").strip()
        day_text = str(first_value(item, ("date", "day", "reportDate")) or "")[:10]
        if not campaign_id or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
            continue
        impressions = int_value(first_value(item, ("views", "impressions", "shows")) or 0)
        clicks = int_value(first_value(item, ("clicks",)) or 0)
        expense = decimal_value(first_value(item, ("moneySpent", "spend", "expense", "cost")) or 0)
        orders = int_value(first_value(item, ("orders", "ordersQty", "orderCount")) or 0)
        amount = decimal_value(first_value(item, ("ordersMoney", "revenue", "ordersAmount")) or 0)
        campaign_title = str(first_value(item, ("title", "campaignTitle")) or "").strip()
        rows.append(
            {
                "report_date": date.fromisoformat(day_text),
                "campaign_id": campaign_id,
                "campaign_title": campaign_title or None,
                "impressions": impressions,
                "clicks": clicks,
                "ctr_pct": Decimal(clicks) / Decimal(impressions) * 100 if impressions else None,
                "expense_rub": expense,
                "cpc_rub": expense / Decimal(clicks) if clicks else None,
                "cpm_rub": expense / Decimal(impressions) * 1000 if impressions else None,
                "orders_qty": orders,
                "cr_pct": Decimal(orders) / Decimal(clicks) * 100 if clicks else None,
                "orders_amount_rub": amount,
                "cpa_rub": expense / Decimal(orders) if orders else None,
                "drr_pct": expense / amount * 100 if amount else None,
            }
        )
    return rows


def decode_performance_csv(body: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise PipelineError("Ozon Performance API вернул CSV в неизвестной кодировке")


def normalized_csv_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "").strip().lower())


def csv_row_value(row: dict[str, str], *names: str) -> str:
    normalized = {normalized_csv_header(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(normalized_csv_header(name))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def csv_decimal_value(value: Any) -> Decimal:
    cleaned = str(value or "").replace("\u00a0", "").replace("₽", "").strip()
    if cleaned in {"", "-", "—"}:
        return Decimal("0")
    return decimal_value(cleaned)


def parse_all_sku_orders_csv(
    body: bytes,
    date_from: date,
    date_to: date,
    campaign_ids: list[str],
) -> list[dict[str, Any]]:
    text = decode_performance_csv(body)
    parsed_lines = list(csv.reader(io.StringIO(text), delimiter=";"))
    header_index = next(
        (
            index
            for index, cells in enumerate(parsed_lines)
            if "дата" in {normalized_csv_header(cell) for cell in cells}
            and "sku" in {normalized_csv_header(cell) for cell in cells}
        ),
        None,
    )
    if header_index is None:
        raise PipelineError("В all-SKU orders отчёте Ozon не найдены колонки Дата и SKU")

    header = [str(cell).strip() for cell in parsed_lines[header_index]]
    campaign_id = campaign_ids[0] if len(campaign_ids) == 1 else "ALL_SKU_PROMO"
    rows: list[dict[str, Any]] = []
    for cells in parsed_lines[header_index + 1 :]:
        if not any(str(cell).strip() for cell in cells):
            continue
        padded = list(cells) + [""] * max(0, len(header) - len(cells))
        item = dict(zip(header, padded))
        raw_date = csv_row_value(item, "Дата")
        sku = csv_row_value(item, "SKU")
        if not raw_date or not sku:
            continue
        day_text = raw_date.split()[0]
        try:
            report_date = (
                date.fromisoformat(day_text)
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text)
                else datetime.strptime(day_text, "%d.%m.%Y").date()
            )
        except ValueError as exc:
            raise PipelineError(
                f"Некорректная дата в all-SKU orders отчёте Ozon: {raw_date!r}"
            ) from exc
        if report_date < date_from or report_date > date_to:
            continue

        quantity = int_value(csv_row_value(item, "Количество") or 0)
        amount = csv_decimal_value(
            csv_row_value(item, "Стоимость продажи, ₽", "Стоимость, ₽")
        )
        expense = csv_decimal_value(csv_row_value(item, "Расход, ₽", "Расход"))
        promoted_sku = csv_row_value(item, "SKU продвигаемого товара")
        is_direct = not promoted_sku or promoted_sku == sku
        direct_quantity = quantity if is_direct else 0
        indirect_quantity = 0 if is_direct else quantity
        direct_amount = amount if is_direct else Decimal("0")
        indirect_amount = Decimal("0") if is_direct else amount
        rows.append(
            {
                "report_date": report_date,
                "sku": sku,
                "seller_article": csv_row_value(item, "Артикул") or None,
                "campaign_id": campaign_id,
                "promoted_sku": promoted_sku or None,
                "impressions": 0,
                "clicks": 0,
                "ctr_pct": None,
                "expense_rub": expense,
                "cpc_rub": None,
                "cpm_rub": None,
                "added_to_cart": None,
                "orders_qty": quantity,
                "direct_orders_qty": direct_quantity,
                "indirect_orders_qty": indirect_quantity,
                "cr_pct": None,
                "orders_amount_rub": amount,
                "direct_orders_amount_rub": direct_amount,
                "indirect_orders_amount_rub": indirect_amount,
                "cpa_rub": expense / Decimal(quantity) if quantity else None,
                "drr_pct": expense / amount * 100 if amount else None,
                "total_orders_qty": quantity,
                "total_orders_amount_rub": amount,
                "total_cpa_rub": expense / Decimal(quantity) if quantity else None,
                "total_drr_pct": expense / amount * 100 if amount else None,
                "source_file": advertising_all_sku_orders_window_source(date_from, date_to),
                "source_sheet": "api-all-sku-orders",
            }
        )
    return rows


def performance_report_uuid(payload: Any) -> str:
    for item in walk_dicts(payload):
        report_uuid = first_value(item, ("UUID", "uuid", "reportUuid"))
        if report_uuid:
            return str(report_uuid).strip()
    return ""


def performance_report_state(payload: Any) -> str:
    for item in walk_dicts(payload):
        state = first_value(item, ("state", "status"))
        if state:
            return str(state).strip().upper()
    return ""


def fetch_all_sku_orders_window(
    api: SerialApiClient,
    token: str,
    campaign_ids: list[str],
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    if not campaign_ids:
        return []
    window_days = (date_to - date_from).days + 1
    if window_days > PERFORMANCE_ALL_SKU_MAX_WINDOW_DAYS:
        windows = performance_date_windows(
            date_from, date_to, PERFORMANCE_ALL_SKU_MAX_WINDOW_DAYS
        )
        rows: list[dict[str, Any]] = []
        print(
            f"ПЛАН: all-SKU orders | период {date_from}..{date_to} | "
            f"окон {len(windows)} до {PERFORMANCE_ALL_SKU_MAX_WINDOW_DAYS} дней",
            flush=True,
        )
        for index, (window_from, window_to) in enumerate(windows, start=1):
            window_rows = fetch_all_sku_orders_window(
                api, token, campaign_ids, window_from, window_to
            )
            rows.extend(window_rows)
            print(
                f"ПРОГРЕСС: all-SKU orders окно {index}/{len(windows)} "
                f"({index / len(windows) * 100:.1f}%) | "
                f"{window_from}..{window_to} | строк {len(window_rows):,} | "
                f"накоплено {len(rows):,}",
                flush=True,
            )
        return rows
    query = urlencode(
        {
            "timeBounds.from": f"{date_from.isoformat()}T00:00:00Z",
            "timeBounds.to": f"{date_to.isoformat()}T00:00:00Z",
        }
    )
    print(
        f"ПРОГРЕСС: all-SKU orders | период {date_from}..{date_to} | "
        f"кампаний {len(campaign_ids)} | генерация отчёта",
        flush=True,
    )
    generated = api.request(
        "GET",
        f"{PERFORMANCE_BASE_URL}/api/client/statistics/all_sku_promo/orders/generate?{query}",
        headers={"Authorization": f"Bearer {token}"},
        bucket="performance-all-sku-generate",
        interval=PERFORMANCE_INTERVAL,
    ).json()
    report_uuid = performance_report_uuid(generated)
    if not report_uuid:
        raise PipelineError("Ozon не вернул UUID all-SKU orders отчёта")

    state = ""
    for attempt in range(1, PERFORMANCE_REPORT_POLL_ATTEMPTS + 1):
        status_payload = api.request(
            "GET",
            f"{PERFORMANCE_BASE_URL}/api/client/statistics/{report_uuid}",
            headers={"Authorization": f"Bearer {token}"},
            bucket="performance-all-sku-status",
            interval=max(PERFORMANCE_INTERVAL, PERFORMANCE_REPORT_POLL_INTERVAL),
        ).json()
        state = performance_report_state(status_payload)
        print(
            f"ПРОГРЕСС: all-SKU orders | статус {attempt}/"
            f"{PERFORMANCE_REPORT_POLL_ATTEMPTS} | state={state or 'UNKNOWN'}",
            flush=True,
        )
        if state == "OK":
            break
        if state in {"ERROR", "FAILED", "CANCELLED", "CANCELED"}:
            raise PipelineError(f"Ozon не сформировал all-SKU orders отчёт: state={state}")
    else:
        raise PipelineError(
            f"All-SKU orders отчёт Ozon не готов после "
            f"{PERFORMANCE_REPORT_POLL_ATTEMPTS} проверок; последний state={state or 'UNKNOWN'}"
        )

    report = api.request(
        "GET",
        f"{PERFORMANCE_BASE_URL}/api/client/statistics/report?"
        f"{urlencode({'UUID': report_uuid})}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/csv, application/octet-stream",
        },
        bucket="performance-all-sku-download",
        interval=PERFORMANCE_INTERVAL,
    )
    rows = parse_all_sku_orders_csv(
        report.body, date_from, date_to, campaign_ids
    )
    print(
        f"ПРОГРЕСС: all-SKU orders | период {date_from}..{date_to} | "
        f"товарных строк {len(rows):,}",
        flush=True,
    )
    return rows

def parse_performance_daily_rows(
    payload: Any, campaign_to_sku: dict[str, str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in parse_performance_campaign_rows(payload):
        sku = campaign_to_sku.get(row["campaign_id"])
        if not sku:
            continue
        rows.append(
            {
                "report_date": row["report_date"],
                "sku": sku,
                "seller_article": row["campaign_title"] if sku.startswith("campaign:") else None,
                "campaign_id": row["campaign_id"],
                "impressions": row["impressions"],
                "clicks": row["clicks"],
                "ctr_pct": row["ctr_pct"],
                "expense_rub": row["expense_rub"],
                "cpc_rub": row["cpc_rub"],
                "cpm_rub": row["cpm_rub"],
                "added_to_cart": None,
                "orders_qty": row["orders_qty"],
                "direct_orders_qty": row["orders_qty"],
                "indirect_orders_qty": 0,
                "cr_pct": row["cr_pct"],
                "orders_amount_rub": row["orders_amount_rub"],
                "direct_orders_amount_rub": row["orders_amount_rub"],
                "indirect_orders_amount_rub": Decimal("0"),
                "cpa_rub": row["cpa_rub"],
                "drr_pct": row["drr_pct"],
                "total_orders_qty": row["orders_qty"],
                "total_orders_amount_rub": row["orders_amount_rub"],
                "total_cpa_rub": row["cpa_rub"],
                "total_drr_pct": row["drr_pct"],
            }
        )
    return rows

def performance_campaign_skus(
    api: SerialApiClient, token: str, campaign_ids: list[str]
) -> tuple[dict[str, str], int, int]:
    mapping: dict[str, str] = {}
    inaccessible = 0
    ambiguous = 0
    for index, campaign_id in enumerate(campaign_ids, start=1):
        try:
            response = api.request(
                "GET",
                f"{PERFORMANCE_BASE_URL}/api/client/campaign/{campaign_id}/v2/products",
                headers={"Authorization": f"Bearer {token}"},
                bucket="performance-products",
                interval=PERFORMANCE_INTERVAL,
            )
        except PipelineError as exc:
            if "HTTP 400" in str(exc) or "HTTP 404" in str(exc):
                inaccessible += 1
                continue
            raise
        payload = response.json()
        products = payload.get("products", []) if isinstance(payload, dict) else payload
        skus = {
            str(item.get("sku") or "").strip()
            for item in (products or [])
            if isinstance(item, dict) and str(item.get("sku") or "").strip()
        }
        if len(skus) == 1:
            mapping[campaign_id] = next(iter(skus))
        else:
            ambiguous += 1
        if index == 1 or index % 10 == 0 or index == len(campaign_ids):
            print(
                f"ПРОГРЕСС: SKU-карты {index}/{len(campaign_ids)} "
                f"({index / max(len(campaign_ids), 1) * 100:.1f}%) | "
                f"однозначно {len(mapping)} | недоступно {inaccessible} | "
                f"несколько/нет SKU {ambiguous}",
                flush=True,
            )
    return mapping, inaccessible, ambiguous


def prepare_advertising_context(
    api: SerialApiClient,
) -> tuple[str, list[str], list[str], int, int, int]:
    token = performance_token(api)
    campaign_rows = performance_campaign_rows(api, token)
    campaign_ids = sorted(
        {
            str(item.get("id") or item.get("campaignId") or "").strip()
            for item in campaign_rows
        }
        - {""}
    )
    if not campaign_ids:
        raise PipelineError("Performance API не вернул кампании")
    all_sku_campaign_ids = sorted(
        {
            str(item.get("id") or item.get("campaignId") or "").strip()
            for item in campaign_rows
            if "SKU" in str(item.get("advObjectType") or "").strip().upper()
            and "ALL" in str(item.get("advObjectType") or "").strip().upper()
        }
        - {""}
    )
    sku_campaigns = sum(
        1
        for item in campaign_rows
        if str(item.get("advObjectType") or "").strip().upper() == "SKU"
    )
    print(
        f"ПРОГРЕСС: кампаний {len(campaign_ids)} | SKU {sku_campaigns} | "
        f"all-SKU promo {len(all_sku_campaign_ids)} | "
        "загружаются product/SKU, all-SKU orders и campaign daily для сверки",
        flush=True,
    )
    return token, campaign_ids, all_sku_campaign_ids, 0, 0, len(campaign_rows)


def fetch_advertising_product_window(
    api: SerialApiClient,
    token: str,
    campaign_ids: list[str],
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    if (date_to - date_from).days + 1 > PERFORMANCE_MAX_WINDOW_DAYS:
        raise PipelineError(
            f"Окно рекламы {date_from}..{date_to} превышает "
            f"{PERFORMANCE_MAX_WINDOW_DAYS} дней"
        )
    if not campaign_ids:
        return []
    # Ozon Performance product/SKU statistics accepts only today or yesterday.
    # Historical windows must still load campaign/all-SKU facts instead of
    # failing the whole account pipeline with HTTP 400.
    supported_from = date.today() - timedelta(days=1)
    supported_to = date.today()
    request_from = max(date_from, supported_from)
    request_to = min(date_to, supported_to)
    if request_from > request_to:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: product/SKU детализация Ozon недоступна для "
            f"исторического окна {date_from}..{date_to}; "
            "будет сохранён campaign-level fallback без выдуманной SKU-детализации",
            flush=True,
        )
        return []
    if request_from != date_from or request_to != date_to:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: product/SKU окно Ozon ограничено "
            f"{request_from}..{request_to} вместо {date_from}..{date_to}; "
            "старые дни останутся на campaign-level fallback",
            flush=True,
        )
    response = api.request(
        "POST",
        f"{PERFORMANCE_BASE_URL}/api/client/statistics/products/sku",
        headers={"Authorization": f"Bearer {token}"},
        payload={
            "campaignIds": campaign_ids,
            "dateFrom": request_from.isoformat(),
            "dateTo": request_to.isoformat(),
        },
        bucket="performance-statistics-product",
        interval=PERFORMANCE_INTERVAL,
    )
    payload = response.json()
    if isinstance(payload, dict) and "rows" not in payload:
        raise PipelineError(
            "Performance API изменил формат product-статистики: поле rows отсутствует"
        )
    return parse_performance_product_rows(payload)


def advertising_fallback_dates(
    product_rows: list[dict[str, Any]], date_from: date, date_to: date
) -> list[date]:
    detailed_dates = {
        row.get("report_date")
        for row in product_rows
        if isinstance(row.get("report_date"), date)
    }
    return [
        date_from + timedelta(days=offset)
        for offset in range((date_to - date_from).days + 1)
        if date_from + timedelta(days=offset) not in detailed_dates
    ]


def fetch_advertising_campaign_window(
    api: SerialApiClient,
    token: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    if (date_to - date_from).days + 1 > PERFORMANCE_MAX_WINDOW_DAYS:
        raise PipelineError(
            f"Окно рекламы {date_from}..{date_to} превышает "
            f"{PERFORMANCE_MAX_WINDOW_DAYS} дней"
        )
    query = urlencode(
        {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }
    )
    response = api.request(
        "GET",
        f"{PERFORMANCE_BASE_URL}/api/client/statistics/daily/json?{query}",
        headers={"Authorization": f"Bearer {token}"},
        bucket="performance-statistics-campaign",
        interval=PERFORMANCE_INTERVAL,
    )
    payload = response.json()
    if isinstance(payload, dict) and "rows" not in payload:
        raise PipelineError(
            "Performance API изменил формат daily-статистики: поле rows отсутствует"
        )
    return parse_performance_campaign_rows(payload)


def fetch_advertising_window(
    api: SerialApiClient,
    token: str,
    campaign_ids: list[str],
    date_from: date,
    date_to: date,
    *,
    all_sku_campaign_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    product_rows = fetch_advertising_product_window(
        api, token, campaign_ids, date_from, date_to
    )
    all_sku_rows = fetch_all_sku_orders_window(
        api,
        token,
        all_sku_campaign_ids or [],
        date_from,
        date_to,
    )
    campaign_rows = fetch_advertising_campaign_window(api, token, date_from, date_to)
    print(
        f"ПРОГРЕСС: реклама API окно {date_from}..{date_to} | "
        f"product SKU-строк {len(product_rows):,} | "
        f"all-SKU order-строк {len(all_sku_rows):,} | "
        f"campaign-строк {len(campaign_rows):,}",
        flush=True,
    )
    return {
        "product": product_rows,
        "all_sku": all_sku_rows,
        "campaign": campaign_rows,
    }


def fetch_advertising_window_legacy(
    api: SerialApiClient,
    token: str,
    campaign_to_sku: dict[str, str],
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    if (date_to - date_from).days + 1 > PERFORMANCE_MAX_WINDOW_DAYS:
        raise PipelineError(
            f"Окно рекламы {date_from}..{date_to} превышает "
            f"{PERFORMANCE_MAX_WINDOW_DAYS} дней"
        )
    query = urlencode(
        {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }
    )
    response = api.request(
        "GET",
        f"{PERFORMANCE_BASE_URL}/api/client/statistics/daily/json?{query}",
        headers={"Authorization": f"Bearer {token}"},
        bucket="performance-statistics",
        interval=PERFORMANCE_INTERVAL,
    )
    payload = response.json()
    if isinstance(payload, dict) and "rows" not in payload:
        raise PipelineError(
            "Performance API изменил формат daily-статистики: поле rows отсутствует"
        )
    raw_rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    raw_campaign_ids = {
        str(first_value(item, ("id", "campaignId")) or "").strip()
        for item in (raw_rows or [])
        if isinstance(item, dict)
    } - {""}
    unresolved = sorted(raw_campaign_ids - set(campaign_to_sku))
    if unresolved:
        resolved, inaccessible, ambiguous = performance_campaign_skus(
            api, token, unresolved
        )
        campaign_to_sku.update(resolved)
        for campaign_id in unresolved:
            campaign_to_sku.setdefault(campaign_id, f"campaign:{campaign_id}")
        print(
            f"ПРОГРЕСС: legacy SKU-карта | новых {len(unresolved)} | "
            f"однозначных SKU {len(resolved)} | campaign-level fallback "
            f"{len(unresolved) - len(resolved)} | недоступно {inaccessible} | "
            f"несколько/нет SKU {ambiguous}",
            flush=True,
        )
    rows = parse_performance_daily_rows(payload, campaign_to_sku)
    if raw_rows and not rows:
        raise PipelineError(
            f"Performance API вернул строки за {date_from}..{date_to}, "
            "но ни одна не сопоставилась с SKU; окно не сохранено"
        )
    return rows

def fetch_advertising(
    api: SerialApiClient, date_from: date, date_to: date
) -> tuple[list[dict[str, Any]], int]:
    before_requests = api.request_count
    token, campaign_ids, all_sku_campaign_ids, inaccessible, ambiguous, _ = (
        prepare_advertising_context(api)
    )
    rows: list[dict[str, Any]] = []
    windows = performance_date_windows(date_from, date_to)
    for index, (window_from, window_to) in enumerate(windows, start=1):
        window_payload = fetch_advertising_window(
            api,
            token,
            campaign_ids,
            window_from,
            window_to,
            all_sku_campaign_ids=all_sku_campaign_ids,
        )
        product_rows = window_payload["product"] + window_payload["all_sku"]
        rows.extend(product_rows)
        print(
            f"ПРОГРЕСС: реклама окно {index}/{len(windows)} | "
            f"{window_from}..{window_to} | SKU-строк {len(product_rows):,} | "
            f"накоплено {len(rows):,}",
            flush=True,
        )
    print(
        f"ПРОГРЕСС: реклама готова к записи | SKU-строк {len(rows):,} | "
        f"кампаний {len(campaign_ids)} | недоступно {inaccessible} | неоднозначно {ambiguous} | "
        "campaign daily используется отдельно для сверки",
        flush=True,
    )
    return rows, api.request_count - before_requests


def advertising_window_source(date_from: date, date_to: date) -> str:
    return f"{API_SOURCE_PREFIX}{ADV_SOURCE_PRODUCT}/{date_from}/{date_to}"


def advertising_campaign_window_source(date_from: date, date_to: date) -> str:
    return f"{API_SOURCE_PREFIX}{ADV_SOURCE_CAMPAIGN}/{date_from}/{date_to}"


def advertising_all_sku_orders_window_source(date_from: date, date_to: date) -> str:
    return f"{API_SOURCE_PREFIX}{ADV_SOURCE_ALL_SKU_ORDERS}/{date_from}/{date_to}"


def existing_manual_adv_keys(conn, date_from: date, date_to: date) -> set[tuple[date, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ozon_adv_daily_raw'
                  AND column_name = 'campaign_id'
            )
            """
        )
        has_campaign_id = bool(cur.fetchone()[0])
        campaign_expr = "COALESCE(campaign_id, '')" if has_campaign_id else "''"
        cur.execute(
            f"""
            SELECT report_date, COALESCE(ozon_marketplace_article, ''), {campaign_expr}
            FROM public.ozon_adv_daily_raw
            WHERE report_date BETWEEN %s AND %s
              AND source_file NOT LIKE %s
            """,
            (date_from, date_to, f"{API_SOURCE_PREFIX}%"),
        )
        return {(row[0], str(row[1]), str(row[2])) for row in cur.fetchall()}


def ensure_advertising_api_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE public.ozon_adv_daily_raw
                ADD COLUMN IF NOT EXISTS campaign_id text,
                ADD COLUMN IF NOT EXISTS instrument text,
                ADD COLUMN IF NOT EXISTS placement text,
                ADD COLUMN IF NOT EXISTS promoted_sku text,
                ADD COLUMN IF NOT EXISTS direct_orders_qty bigint NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS indirect_orders_qty bigint NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS direct_orders_amount_rub numeric,
                ADD COLUMN IF NOT EXISTS indirect_orders_amount_rub numeric
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_adv_campaign_daily_raw (
                id bigserial PRIMARY KEY,
                report_date date NOT NULL,
                campaign_id text NOT NULL,
                campaign_title text,
                impressions bigint NOT NULL DEFAULT 0,
                clicks bigint NOT NULL DEFAULT 0,
                ctr_pct numeric,
                expense_rub numeric,
                cpc_rub numeric,
                cpm_rub numeric,
                orders_qty bigint NOT NULL DEFAULT 0,
                cr_pct numeric,
                orders_amount_rub numeric,
                cpa_rub numeric,
                drr_pct numeric,
                source_file text NOT NULL,
                source_sheet text NOT NULL,
                source_row_num integer NOT NULL,
                imported_at timestamp without time zone NOT NULL DEFAULT now(),
                UNIQUE (source_file, source_sheet, source_row_num)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ozon_adv_campaign_daily_date
            ON public.ozon_adv_campaign_daily_raw (report_date)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ozon_adv_campaign_daily_campaign
            ON public.ozon_adv_campaign_daily_raw (campaign_id)
            """
        )
    conn.commit()


def normalize_advertising_product_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[date, str, str, str, str, str], dict[str, Any]] = {}
    additive_fields = (
        "impressions",
        "clicks",
        "expense_rub",
        "orders_qty",
        "direct_orders_qty",
        "indirect_orders_qty",
        "orders_amount_rub",
        "direct_orders_amount_rub",
        "indirect_orders_amount_rub",
        "total_orders_qty",
        "total_orders_amount_rub",
    )
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if not sku or sku.startswith("campaign:"):
            continue
        campaign_id = str(row.get("campaign_id") or "").strip()
        source_file = str(row.get("source_file") or "").strip()
        source_sheet = str(row.get("source_sheet") or "").strip()
        promoted_sku = str(row.get("promoted_sku") or "").strip()
        key = (
            row["report_date"], sku, campaign_id, promoted_sku, source_file, source_sheet
        )
        if key not in grouped:
            grouped[key] = {**row, "sku": sku, "campaign_id": campaign_id or None}
            grouped[key].setdefault("direct_orders_qty", row.get("orders_qty", 0))
            grouped[key].setdefault("indirect_orders_qty", 0)
            grouped[key].setdefault("direct_orders_amount_rub", row.get("orders_amount_rub", Decimal("0")))
            grouped[key].setdefault("indirect_orders_amount_rub", Decimal("0"))
            grouped[key].setdefault("total_orders_qty", row.get("orders_qty", 0))
            grouped[key].setdefault("total_orders_amount_rub", row.get("orders_amount_rub", Decimal("0")))
            continue
        target = grouped[key]
        for field in additive_fields:
            target[field] = (target.get(field) or 0) + (row.get(field) or 0)
        if row.get("added_to_cart") is not None:
            target["added_to_cart"] = (target.get("added_to_cart") or 0) + row["added_to_cart"]
    prepared = list(grouped.values())
    for row in prepared:
        impressions = row["impressions"]
        clicks = row["clicks"]
        expense = row["expense_rub"]
        orders = row["orders_qty"]
        amount = row["orders_amount_rub"]
        total_orders = row.get("total_orders_qty") or orders
        total_amount = row.get("total_orders_amount_rub") or amount
        row["ctr_pct"] = Decimal(clicks) / Decimal(impressions) * 100 if impressions else None
        row["cr_pct"] = Decimal(orders) / Decimal(clicks) * 100 if clicks else None
        row["cpc_rub"] = expense / Decimal(clicks) if clicks else None
        row["cpm_rub"] = expense / Decimal(impressions) * 1000 if impressions else None
        row["cpa_rub"] = expense / Decimal(orders) if orders else None
        row["drr_pct"] = expense / amount * 100 if amount else None
        row["total_cpa_rub"] = expense / Decimal(total_orders) if total_orders else None
        row["total_drr_pct"] = expense / total_amount * 100 if total_amount else None
    return prepared


def store_advertising_campaigns(
    conn,
    rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
    *,
    dry_run: bool,
) -> int:
    if dry_run:
        return len(rows)
    source = advertising_campaign_window_source(date_from, date_to)
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.ozon_adv_campaign_daily_raw
            WHERE report_date BETWEEN %s AND %s AND source_file LIKE %s
            """,
            (date_from, date_to, f"{API_SOURCE_PREFIX}%"),
        )
        if rows:
            execute_values(
                cur,
                f"""
                INSERT INTO public.ozon_adv_campaign_daily_raw ({", ".join(ADV_CAMPAIGN_RAW_COLUMNS)})
                VALUES %s
                ON CONFLICT (source_file, source_sheet, source_row_num) DO UPDATE SET
                    campaign_title = EXCLUDED.campaign_title,
                    impressions = EXCLUDED.impressions,
                    clicks = EXCLUDED.clicks,
                    ctr_pct = EXCLUDED.ctr_pct,
                    expense_rub = EXCLUDED.expense_rub,
                    cpc_rub = EXCLUDED.cpc_rub,
                    cpm_rub = EXCLUDED.cpm_rub,
                    orders_qty = EXCLUDED.orders_qty,
                    cr_pct = EXCLUDED.cr_pct,
                    orders_amount_rub = EXCLUDED.orders_amount_rub,
                    cpa_rub = EXCLUDED.cpa_rub,
                    drr_pct = EXCLUDED.drr_pct,
                    imported_at = now()
                """,
                [
                    tuple(
                        {
                            **row,
                            "source_file": source,
                            "source_sheet": "api-campaign",
                            "source_row_num": index,
                        }[column]
                        for column in ADV_CAMPAIGN_RAW_COLUMNS
                    )
                    for index, row in enumerate(rows, start=1)
                ],
            )
    return len(rows)


def store_advertising(
    conn,
    rows: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
    date_from: date,
    date_to: date,
    *,
    dry_run: bool,
) -> int:
    if isinstance(rows, dict):
        detailed_product_rows = rows.get("product", [])
        product_rows = detailed_product_rows + rows.get("all_sku", [])
        campaign_rows = rows.get("campaign", [])
    else:
        detailed_product_rows = rows
        product_rows = rows
        campaign_rows = []
    if not dry_run:
        ensure_advertising_api_schema(conn)
    prepared_all = normalize_advertising_product_rows(product_rows)
    manual_keys = set() if dry_run else existing_manual_adv_keys(conn, date_from, date_to)
    prepared = [
        row for row in prepared_all
        if (row["report_date"], row["sku"], str(row.get("campaign_id") or "")) not in manual_keys
    ]
    if dry_run:
        return len(prepared)
    product_source = advertising_window_source(date_from, date_to)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM public.ozon_adv_daily_raw
                WHERE report_date BETWEEN %s AND %s AND source_file LIKE %s
                """,
                (date_from, date_to, f"{API_SOURCE_PREFIX}%"),
            )
            if prepared:
                execute_values(
                    cur,
                    """
                    INSERT INTO public.ozon_adv_daily_raw (
                        report_date, ozon_marketplace_article, seller_article,
                        campaign_id, instrument, placement, promoted_sku,
                        impressions, clicks, ctr_pct, expense_rub, fact_expense_rub,
                        cpc_rub, cpm_rub, added_to_cart, orders_qty,
                        direct_orders_qty, indirect_orders_qty, cr_pct,
                        orders_amount_rub, direct_orders_amount_rub,
                        indirect_orders_amount_rub, cpa_rub, drr_pct,
                        total_orders_qty, total_orders_amount_rub,
                        total_drr_pct, total_cpa_rub,
                        source_file, source_sheet, source_row_num
                    ) VALUES %s
                    ON CONFLICT (source_file, source_sheet, source_row_num) DO UPDATE SET
                        seller_article = EXCLUDED.seller_article,
                        campaign_id = EXCLUDED.campaign_id,
                        instrument = EXCLUDED.instrument,
                        placement = EXCLUDED.placement,
                        promoted_sku = EXCLUDED.promoted_sku,
                        impressions = EXCLUDED.impressions,
                        clicks = EXCLUDED.clicks,
                        ctr_pct = EXCLUDED.ctr_pct,
                        expense_rub = EXCLUDED.expense_rub,
                        fact_expense_rub = EXCLUDED.fact_expense_rub,
                        cpc_rub = EXCLUDED.cpc_rub,
                        cpm_rub = EXCLUDED.cpm_rub,
                        added_to_cart = EXCLUDED.added_to_cart,
                        orders_qty = EXCLUDED.orders_qty,
                        direct_orders_qty = EXCLUDED.direct_orders_qty,
                        indirect_orders_qty = EXCLUDED.indirect_orders_qty,
                        cr_pct = EXCLUDED.cr_pct,
                        orders_amount_rub = EXCLUDED.orders_amount_rub,
                        direct_orders_amount_rub = EXCLUDED.direct_orders_amount_rub,
                        indirect_orders_amount_rub = EXCLUDED.indirect_orders_amount_rub,
                        cpa_rub = EXCLUDED.cpa_rub,
                        drr_pct = EXCLUDED.drr_pct,
                        total_orders_qty = EXCLUDED.total_orders_qty,
                        total_orders_amount_rub = EXCLUDED.total_orders_amount_rub,
                        total_drr_pct = EXCLUDED.total_drr_pct,
                        total_cpa_rub = EXCLUDED.total_cpa_rub,
                        imported_at = now()
                    """,
                    [
                        (
                            row["report_date"],
                            row["sku"],
                            row.get("seller_article"),
                            row.get("campaign_id"),
                            row.get("instrument"),
                            row.get("placement"),
                            row.get("promoted_sku"),
                            row["impressions"],
                            row["clicks"],
                            row["ctr_pct"],
                            row["expense_rub"],
                            row["expense_rub"],
                            row["cpc_rub"],
                            row["cpm_rub"],
                            row.get("added_to_cart"),
                            row["orders_qty"],
                            row.get("direct_orders_qty", row["orders_qty"]),
                            row.get("indirect_orders_qty", 0),
                            row["cr_pct"],
                            row["orders_amount_rub"],
                            row.get("direct_orders_amount_rub", row["orders_amount_rub"]),
                            row.get("indirect_orders_amount_rub", Decimal("0")),
                            row["cpa_rub"],
                            row["drr_pct"],
                            row.get("total_orders_qty", row["orders_qty"]),
                            row.get("total_orders_amount_rub", row["orders_amount_rub"]),
                            row.get("total_drr_pct", row["drr_pct"]),
                            row.get("total_cpa_rub", row["cpa_rub"]),
                            row.get("source_file") or product_source,
                            row.get("source_sheet") or "api-product",
                            index,
                        )
                        for index, row in enumerate(prepared, start=1)
                    ],
                )
        campaign_loaded = store_advertising_campaigns(
            conn, campaign_rows, date_from, date_to, dry_run=dry_run
        )
        fallback_loaded = 0
        # Manual rows win on key collisions. A campaign-level fallback is only
        # valid when the product endpoint itself returned no SKU detail; using
        # it merely because every SKU collided with manual data would double-count.
        fallback_dates = advertising_fallback_dates(
            detailed_product_rows, date_from, date_to
        )
        if fallback_dates and campaign_loaded:
            fallback_source = advertising_campaign_window_source(date_from, date_to)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.ozon_adv_daily_raw (
                        report_date, ozon_marketplace_article, seller_article,
                        campaign_id, instrument, placement, promoted_sku,
                        impressions, clicks, ctr_pct, expense_rub, fact_expense_rub,
                        cpc_rub, cpm_rub, added_to_cart, orders_qty,
                        direct_orders_qty, indirect_orders_qty, cr_pct,
                        orders_amount_rub, direct_orders_amount_rub,
                        indirect_orders_amount_rub, cpa_rub, drr_pct,
                        total_orders_qty, total_orders_amount_rub,
                        total_drr_pct, total_cpa_rub,
                        source_file, source_sheet, source_row_num
                    )
                    SELECT
                        report_date,
                        'campaign:' || campaign_id,
                        coalesce(nullif(campaign_title, ''), 'Кампания ' || campaign_id),
                        campaign_id,
                        'campaign',
                        NULL,
                        NULL,
                        impressions,
                        clicks,
                        ctr_pct,
                        expense_rub,
                        expense_rub,
                        cpc_rub,
                        cpm_rub,
                        0,
                        orders_qty,
                        orders_qty,
                        0,
                        cr_pct,
                        orders_amount_rub,
                        orders_amount_rub,
                        0,
                        cpa_rub,
                        drr_pct,
                        orders_qty,
                        orders_amount_rub,
                        drr_pct,
                        cpa_rub,
                        source_file,
                        'api-campaign-fallback',
                        id::integer
                    FROM public.ozon_adv_campaign_daily_raw
                    WHERE report_date = ANY(%s)
                      AND source_file = %s
                    ON CONFLICT (source_file, source_sheet, source_row_num) DO UPDATE SET
                        impressions = EXCLUDED.impressions,
                        clicks = EXCLUDED.clicks,
                        ctr_pct = EXCLUDED.ctr_pct,
                        expense_rub = EXCLUDED.expense_rub,
                        fact_expense_rub = EXCLUDED.fact_expense_rub,
                        cpc_rub = EXCLUDED.cpc_rub,
                        cpm_rub = EXCLUDED.cpm_rub,
                        orders_qty = EXCLUDED.orders_qty,
                        direct_orders_qty = EXCLUDED.direct_orders_qty,
                        cr_pct = EXCLUDED.cr_pct,
                        orders_amount_rub = EXCLUDED.orders_amount_rub,
                        direct_orders_amount_rub = EXCLUDED.direct_orders_amount_rub,
                        drr_pct = EXCLUDED.drr_pct,
                        total_orders_qty = EXCLUDED.total_orders_qty,
                        total_orders_amount_rub = EXCLUDED.total_orders_amount_rub,
                        total_drr_pct = EXCLUDED.total_drr_pct,
                        imported_at = now()
                    """,
                    (fallback_dates, fallback_source),
                )
                fallback_loaded = cur.rowcount
            print(
                f"ПРОГРЕСС: реклама | SKU-детализация недоступна, "
                f"сохранён fallback по кампаниям {fallback_loaded:,} строк",
                flush=True,
            )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.ozon_adv_daily_import_files (
                    source_file, report_date_from, report_date_to,
                    rows_imported, imported_at, status, error
                ) VALUES (%s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    rows_imported = EXCLUDED.rows_imported,
                    imported_at = now(), status = 'ok', error = NULL
                """,
                (
                    product_source,
                    date_from,
                    date_to,
                    sum(
                        1
                        for row in prepared
                        if not row.get("source_file")
                        or row.get("source_file") == product_source
                    ),
                ),
            )
            all_sku_source = advertising_all_sku_orders_window_source(
                date_from, date_to
            )
            cur.execute(
                """
                INSERT INTO public.ozon_adv_daily_import_files (
                    source_file, report_date_from, report_date_to,
                    rows_imported, imported_at, status, error
                ) VALUES (%s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    rows_imported = EXCLUDED.rows_imported,
                    imported_at = now(), status = 'ok', error = NULL
                """,
                (
                    all_sku_source,
                    date_from,
                    date_to,
                    sum(
                        1
                        for row in prepared
                        if row.get("source_file") == all_sku_source
                    ),
                ),
            )
            campaign_source = advertising_campaign_window_source(date_from, date_to)
            cur.execute(
                """
                INSERT INTO public.ozon_adv_daily_import_files (
                    source_file, report_date_from, report_date_to,
                    rows_imported, imported_at, status, error
                ) VALUES (%s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    rows_imported = EXCLUDED.rows_imported,
                    imported_at = now(), status = 'ok', error = NULL
                """,
                (campaign_source, date_from, date_to, campaign_loaded),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(prepared) + fallback_loaded


def completed_advertising_sources(
    conn, windows: list[tuple[date, date]]
) -> set[str]:
    sources = [advertising_window_source(start, end) for start, end in windows]
    if not sources:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_file
            FROM public.ozon_adv_daily_import_files
            WHERE status = 'ok' AND source_file = ANY(%s)
            """,
            (sources,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def sync_advertising_incremental(
    conn,
    api: SerialApiClient,
    date_from: date,
    date_to: date,
    *,
    run_id: int,
    dry_run: bool,
) -> tuple[int, int]:
    windows = performance_date_windows(date_from, date_to)
    completed = set() if dry_run else completed_advertising_sources(conn, windows)
    refresh_cutoff = date.today() - timedelta(days=PERFORMANCE_REFRESH_DAYS)
    pending = [
        (start, end)
        for start, end in windows
        if advertising_window_source(start, end) not in completed
        or end >= refresh_cutoff
    ]
    print(
        f"ПЛАН: реклама Ozon | период {date_from}..{date_to} | "
        f"окон {len(windows)} по максимум {PERFORMANCE_MAX_WINDOW_DAYS} дней | "
        f"к загрузке {len(pending)} | готовых старых окон {len(windows) - len(pending)} | "
        f"последние {PERFORMANCE_REFRESH_DAYS} дней обновляются повторно | "
        "контекст кампаний один раз; product/SKU, all-SKU orders и campaign daily загружаются отдельно",
        flush=True,
    )
    if not pending:
        print(
            "ПРОГРЕСС: реклама | все старые окна уже сохранены в БД | "
            "API-запросы не требуются",
            flush=True,
        )
        return 0, 0

    before_requests = api.request_count
    (
        token,
        campaign_ids,
        all_sku_campaign_ids,
        inaccessible,
        ambiguous,
        campaign_count,
    ) = prepare_advertising_context(api)
    loaded = 0
    processed_days = 0
    completed_pending = 0
    started = time.monotonic()
    pending_set = set(pending)

    for window_index, (window_from, window_to) in enumerate(windows, start=1):
        window_days = (window_to - window_from).days + 1
        if (window_from, window_to) not in pending_set:
            processed_days += window_days
            print(
                f"ПРОГРЕСС: реклама окно {window_index}/{len(windows)} | "
                f"{window_from}..{window_to} | уже сохранено, пропуск | "
                f"накоплено строк {loaded:,}",
                flush=True,
            )
            continue

        window_rows = fetch_advertising_window(
            api,
            token,
            campaign_ids,
            window_from,
            window_to,
            all_sku_campaign_ids=all_sku_campaign_ids,
        )
        stored = store_advertising(
            conn,
            window_rows,
            window_from,
            window_to,
            dry_run=dry_run,
        )
        loaded += stored
        processed_days += window_days
        completed_pending += 1
        requests = api.request_count - before_requests
        if not dry_run:
            checkpoint_run(
                conn,
                run_id,
                loaded,
                requests,
                processed_days,
            )
        elapsed = time.monotonic() - started
        remaining = len(pending) - completed_pending
        eta = (elapsed / completed_pending * remaining) if completed_pending else 0
        print(
            f"ПРОГРЕСС: реклама окно {completed_pending}/{len(pending)} "
            f"({completed_pending / len(pending) * 100:.1f}%) | "
            f"общий план {window_index}/{len(windows)} | "
            f"{window_from}..{window_to} | сохранено {stored:,} | "
            f"накоплено строк {loaded:,} | запросов {requests} | "
            f"прошло {elapsed:.1f} сек. | ETA {eta:.1f} сек.",
            flush=True,
        )

    requests = api.request_count - before_requests
    print(
        f"ИТОГ: реклама Ozon | окна {completed_pending}/{len(pending)} | "
        f"SKU-строк {loaded:,} | запросов {requests} | "
        f"кампаний {campaign_count}, product endpoint campaignIds {len(campaign_ids)}, "
        f"all-SKU кампаний {len(all_sku_campaign_ids)}, "
        f"недоступно {inaccessible}, неоднозначно {ambiguous}",
        flush=True,
    )
    return loaded, requests

def ensure_run_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.km_ozon_api_runs (
                id bigserial PRIMARY KEY,
                step text NOT NULL,
                date_from date,
                date_to date,
                started_at timestamp without time zone NOT NULL DEFAULT now(),
                finished_at timestamp without time zone,
                status text NOT NULL,
                rows_loaded integer NOT NULL DEFAULT 0,
                requests_made integer NOT NULL DEFAULT 0,
                checkpoint_offset integer NOT NULL DEFAULT 0,
                contract_key text,
                error text
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE public.km_ozon_api_runs
            ADD COLUMN IF NOT EXISTS checkpoint_offset integer NOT NULL DEFAULT 0
            """
        )
        cur.execute(
            """
            ALTER TABLE public.km_ozon_api_runs
            ADD COLUMN IF NOT EXISTS contract_key text
            """
        )
    conn.commit()


def start_run(conn, step: str, date_from: date | None, date_to: date | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.km_ozon_api_runs
            SET status = 'stopped', finished_at = COALESCE(finished_at, now()),
                error = COALESCE(error, 'Предыдущий процесс был прерван до завершения')
            WHERE step = %s AND status = 'running'
            """,
            (step,),
        )
        cur.execute(
            """
            INSERT INTO public.km_ozon_api_runs (step, date_from, date_to, status)
            VALUES (%s, %s, %s, 'running') RETURNING id
            """,
            (step, date_from, date_to),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def set_run_contract(conn, run_id: int, contract_key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.km_ozon_api_runs SET contract_key = %s WHERE id = %s",
            (contract_key, run_id),
        )
    conn.commit()


def finish_run(
    conn, run_id: int, status: str, rows: int, requests: int, error: str | None = None
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.km_ozon_api_runs
            SET finished_at = now(), status = %s,
                rows_loaded = GREATEST(rows_loaded, %s),
                requests_made = GREATEST(requests_made, %s), error = %s
            WHERE id = %s
            """,
            (status, rows, requests, error[:2000] if error else None, run_id),
        )
    conn.commit()


def rebuild_views() -> None:
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-u",
        str(PROJECT_ROOT / "scripts" / "rebuild_km_dashboard_views.py"),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        raise PipelineError(f"Пересборка витрин завершилась с кодом {completed.returncode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step",
        choices=("funnel", "stock", "advertising", "views", "all"),
        default="all",
    )
    parser.add_argument("--date-from", type=date.fromisoformat)
    parser.add_argument("--date-to", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    conn = connect_km()
    api = SerialApiClient()
    total_rows = 0
    total_requests = 0
    errors: list[str] = []
    try:
        ensure_funnel_api_schema(conn)
        ensure_run_table(conn)
        steps = (
            ["funnel", "stock", "advertising", "views"]
            if args.step == "all"
            else [args.step]
        )
        print(
            f"ПЛАН: клиент {CLIENT_LABEL} | БД {TARGET_DB} | шагов {len(steps)} | "
            f"последовательные API-запросы без параллелизма | "
            f"analytics не чаще 1 запроса/{SELLER_ANALYTICS_INTERVAL:.1f} сек. "
            f"(лимит Ozon 1/мин + запас; после 429 разовый cooldown, "
            f"плановая пауза до {ADAPTIVE_RATE_LIMIT_STEADY_MAX_INTERVAL:.1f} сек.) | "
            f"остальные пауза не менее {min(SELLER_DEFAULT_INTERVAL, PERFORMANCE_INTERVAL):.1f} сек. | "
            f"429: Retry-After + до {MAX_RATE_LIMIT_RETRIES} повторов | "
            f"реклама: окна до {PERFORMANCE_MAX_WINDOW_DAYS} дней, "
            f"повторное обновление последних {PERFORMANCE_REFRESH_DAYS} дней | "
            "остатки: только фактическая дата запроса, без псевдо-backfill",
            flush=True,
        )
        for index, step in enumerate(steps, start=1):
            step_from = args.date_from
            step_to = args.date_to
            if step in {"funnel", "advertising"} and (step_from is None or step_to is None):
                default_from, default_to = default_range(conn, step)
                step_from = step_from or default_from
                step_to = step_to or default_to
            if step == "stock":
                requested_from, requested_to = step_from, step_to
                snapshot_day = resolve_stock_snapshot_date(step_from, step_to)
                step_from = snapshot_day
                step_to = snapshot_day
                if (requested_from or requested_to) and (
                    requested_from != snapshot_day or requested_to != snapshot_day
                ):
                    print(
                        "ПРОГРЕСС: остатки Ozon — выбранный период "
                        f"{requested_from or '-'}..{requested_to or '-'} не является backfill; "
                        f"сохраняется фактический снимок {snapshot_day}",
                        flush=True,
                    )
            if step in {"funnel", "advertising"} and step_from > step_to:
                print(f"[{index}/{len(steps)}] {step}: новых дат нет, пропуск", flush=True)
                continue
            if step in {"funnel", "advertising"} and step_from and step_to:
                validate_date_range(step_from, step_to)
            print(
                f"ПРОГРЕСС: {index}/{len(steps)} ({index / len(steps) * 100:.1f}%) | "
                f"шаг {step} | период {step_from or '-'}..{step_to or '-'} | "
                f"накоплено строк {total_rows:,}",
                flush=True,
            )
            run_id = start_run(conn, step, step_from, step_to)
            before_requests = api.request_count
            try:
                if step == "funnel":
                    capability = detect_funnel_capability(
                        conn, api, step_from, step_to
                    )
                    set_run_contract(conn, run_id, capability.contract_key)
                    loaded, _ = sync_funnel_incremental(
                        conn,
                        api,
                        step_from,
                        step_to,
                        run_id=run_id,
                        capability=capability,
                        dry_run=args.dry_run,
                        overwrite=args.overwrite,
                    )
                elif step == "stock":
                    rows, _ = fetch_stock(api)
                    loaded = store_stock(
                        conn,
                        rows,
                        dry_run=args.dry_run,
                        snapshot_date=step_to,
                    )
                elif step == "advertising":
                    loaded, _ = sync_advertising_incremental(
                        conn,
                        api,
                        step_from,
                        step_to,
                        run_id=run_id,
                        dry_run=args.dry_run,
                    )
                else:
                    if not args.dry_run:
                        rebuild_views()
                    loaded = 0
                requests = api.request_count - before_requests
                finish_run(conn, run_id, "dry_run" if args.dry_run else "ok", loaded, requests)
                total_rows += loaded
                total_requests += requests
                print(
                    f"[{index}/{len(steps)}] {step}: загружено {loaded:,}, "
                    f"запросов {requests}, ошибок 0",
                    flush=True,
                )
            except Exception as exc:
                conn.rollback()
                requests = api.request_count - before_requests
                finish_run(conn, run_id, "error", 0, requests, str(exc))
                errors.append(f"{step}: {exc}")
                raise
        elapsed = time.monotonic() - started
        print(
            f"ИТОГ: {CLIENT_LABEL} | строк {total_rows:,} | API-запросов {total_requests} | "
            f"ошибок {len(errors)} | прошло {elapsed:.1f} сек. | "
            f"режим {'dry-run' if args.dry_run else 'запись завершена'}",
            flush=True,
        )
        return 0
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(
            f"ИТОГ: {CLIENT_LABEL} | остановлено с ошибкой | строк {total_rows:,} | "
            f"API-запросов {api.request_count} | прошло {elapsed:.1f} сек. | {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())




