#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Save current Ozon Seller API stock snapshots for every configured client.

The Ozon Seller stock endpoint is a current observation, not a historical
backfill source.  This script therefore records only the actual run date unless
an explicit test-only override is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402
import inventory_history  # noqa: E402


SELLER_BASE_URL = os.environ.get(
    "OZON_SELLER_API_BASE_URL", "https://api-seller.ozon.ru"
).rstrip("/")
SELLER_DEFAULT_INTERVAL = float(os.environ.get("OZON_SELLER_DEFAULT_INTERVAL_SECONDS", "1.1"))
MAX_RETRIES = int(os.environ.get("OZON_API_MAX_RETRIES", "6"))
HTTP_TIMEOUT = int(os.environ.get("OZON_API_HTTP_TIMEOUT_SECONDS", "90"))
PAGE_SIZE = int(os.environ.get("OZON_STOCK_SNAPSHOT_PAGE_SIZE", "1000"))
API_SOURCE_PREFIX = "ozon_api://"


class SnapshotError(RuntimeError):
    pass


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotError("Ozon API вернул некорректный JSON") from exc


class SerialApiClient:
    def __init__(self) -> None:
        self.last_request_at: dict[str, float] = {}
        self.request_count = 0

    def _wait(self, bucket: str, interval: float) -> None:
        previous = self.last_request_at.get(bucket)
        if previous is None:
            return
        remaining = interval - (time.monotonic() - previous)
        while remaining > 0:
            chunk = min(remaining, 10)
            print(
                f"ПРОГРЕСС: лимит API | {bucket} | пауза {remaining:.1f} сек. | "
                f"запросов {self.request_count}",
                flush=True,
            )
            time.sleep(chunk)
            remaining = interval - (time.monotonic() - previous)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: Any,
        bucket: str,
        interval: float,
    ) -> ApiResponse:
        method = method.upper()
        if method != "POST":
            raise SnapshotError(f"Метод {method} запрещён read-only политикой")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **headers,
        }
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait(bucket, interval)
            self.request_count += 1
            try:
                response = urlopen(
                    Request(url, data=data, headers=request_headers, method=method),
                    timeout=HTTP_TIMEOUT,
                )
                body = response.read()
                self.last_request_at[bucket] = time.monotonic()
                return ApiResponse(
                    status=response.status,
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                )
            except HTTPError as exc:
                self.last_request_at[bucket] = time.monotonic()
                error_body = exc.read().decode("utf-8", errors="replace")[:1200]
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= MAX_RETRIES:
                    raise SnapshotError(
                        f"Ozon API HTTP {exc.code} для {method} {url}: {error_body}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                try:
                    wait_seconds = max(float(retry_after or 0), min(2**attempt, 60))
                except ValueError:
                    wait_seconds = min(2**attempt, 60)
                print(
                    f"ПРОГРЕСС: повтор API {attempt}/{MAX_RETRIES} | HTTP {exc.code} | "
                    f"ожидание {wait_seconds:.1f} сек.",
                    flush=True,
                )
                time.sleep(wait_seconds)
            except (URLError, TimeoutError) as exc:
                self.last_request_at[bucket] = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise SnapshotError(f"Сетевая ошибка Ozon API для {method} {url}: {exc}") from exc
                wait_seconds = min(2**attempt, 60)
                print(
                    f"ПРОГРЕСС: повтор API {attempt}/{MAX_RETRIES} | сеть | "
                    f"ожидание {wait_seconds} сек.",
                    flush=True,
                )
                time.sleep(wait_seconds)
        raise SnapshotError("Исчерпаны повторы Ozon API")


def decimal_value(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise SnapshotError(f"Некорректное число в ответе Ozon API: {value!r}") from exc


def int_value(value: Any) -> int:
    return int(decimal_value(value))


def seller_headers(client_key: str) -> dict[str, str] | None:
    client_id, api_key = app.ozon_seo_credentials_value(client_key)
    if not client_id or not api_key:
        return None
    return {"Client-Id": client_id, "Api-Key": api_key}


def configured_ozon_clients(selected: set[str] | None = None) -> list[dict[str, Any]]:
    try:
        app.hydrate_registered_clients()
    except Exception as exc:
        print(
            f"ПРЕДУПРЕЖДЕНИЕ: реестр клиентов не прочитан ({type(exc).__name__}); "
            "использую статическую конфигурацию",
            flush=True,
        )
    rows: list[dict[str, Any]] = []
    for key, config in sorted(app.ADMIN_CLIENTS.items()):
        normalized = app.normalize_client_key(key)
        if selected and normalized not in selected:
            continue
        if "ozon" not in (config.get("marketplaces") or []):
            continue
        if str(config.get("status") or "active").lower() != "active":
            continue
        rows.append(
            {
                "key": normalized,
                "label": config.get("label") or normalized,
                "db_name": config.get("db_name"),
                "credentials_saved": seller_headers(normalized) is not None,
            }
        )
    return rows


def parse_stock_page(payload: Any) -> list[dict[str, Any]]:
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    rows = result.get("rows") or result.get("items") or [] if isinstance(result, dict) else []
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


def fetch_stock(
    api: SerialApiClient,
    *,
    client_key: str,
    headers: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    offset = 0
    request_number = 0
    while True:
        request_number += 1
        print(
            f"ПРОГРЕСС: {client_key} | запрос {request_number}/? | остатки Ozon | "
            f"offset {offset} | накоплено {len(rows):,}",
            flush=True,
        )
        response = api.request(
            "POST",
            f"{SELLER_BASE_URL}/v2/analytics/stock_on_warehouses",
            headers=headers,
            payload={"limit": PAGE_SIZE, "offset": offset},
            bucket=f"seller-stock:{client_key}",
            interval=SELLER_DEFAULT_INTERVAL,
        )
        page = parse_stock_page(response.json())
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    if not rows:
        raise SnapshotError(
            f"{client_key}: Ozon вернул пустой снимок остатков; текущие таблицы не изменены"
        )
    return rows, request_number


def connect_client(client_key: str, expected_db: str):
    config = app.read_db_config(client_key)
    conn = psycopg2.connect(**config)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    if actual != expected_db:
        conn.close()
        raise SnapshotError(
            f"Защита клиента {client_key}: ожидалась БД {expected_db}, подключение открыло {actual}"
        )
    return conn


def ensure_run_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_stock_snapshot_api_runs (
                id bigserial PRIMARY KEY,
                client_key text NOT NULL,
                snapshot_date date NOT NULL,
                started_at timestamp without time zone NOT NULL DEFAULT now(),
                finished_at timestamp without time zone,
                status text NOT NULL,
                rows_loaded integer NOT NULL DEFAULT 0,
                requests_made integer NOT NULL DEFAULT 0,
                current_tables_updated boolean NOT NULL DEFAULT false,
                error text
            )
            """
        )
    conn.commit()


def start_run(conn: Any, client_key: str, snapshot_date: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.ozon_stock_snapshot_api_runs
              (client_key, snapshot_date, status)
            VALUES (%s, %s, 'running')
            RETURNING id
            """,
            (client_key, snapshot_date),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(
    conn: Any,
    run_id: int,
    *,
    status: str,
    rows_loaded: int,
    requests_made: int,
    current_tables_updated: bool,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.ozon_stock_snapshot_api_runs
            SET finished_at = now(), status = %s, rows_loaded = %s,
                requests_made = %s, current_tables_updated = %s, error = %s
            WHERE id = %s
            """,
            (
                status,
                rows_loaded,
                requests_made,
                current_tables_updated,
                error[:2000] if error else None,
                run_id,
            ),
        )
    conn.commit()


def table_columns(cur: Any, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {row[0] for row in cur.fetchall()}


def existing_current_stock_tables(cur: Any) -> set[str]:
    cur.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename = ANY(%s)
        """,
        (
            [
                "ozon_stock_products",
                "ozon_stock_product_warehouses",
                "ozon_stock_product_clusters",
                "ozon_stock_clusters",
            ],
        ),
    )
    return {row[0] for row in cur.fetchall()}


def aggregate_stock(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = grouped.setdefault(
            row["sku"],
            {
                "sku": row["sku"],
                "article": row.get("article"),
                "product_name": row.get("product_name"),
                "available_to_sell": 0,
                "preparing_to_sell": 0,
            },
        )
        target["article"] = target.get("article") or row.get("article")
        target["product_name"] = target.get("product_name") or row.get("product_name")
        target["available_to_sell"] += row.get("available_to_sell") or 0
        target["preparing_to_sell"] += row.get("preparing_to_sell") or 0
    return list(grouped.values())


def enrich_stock_metadata(
    conn: Any, rows: list[dict[str, Any]]
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


def update_current_stock_tables(
    conn: Any,
    rows: list[dict[str, Any]],
    source_ref: str,
) -> bool:
    products = aggregate_stock(rows)
    with conn.cursor() as cur:
        existing = existing_current_stock_tables(cur)
        required_product_columns = {
            "source_file",
            "source_sheet",
            "source_row_num",
            "article",
            "product_name",
            "sku",
            "available_to_sell",
            "preparing_to_sell",
        }
        required_warehouse_columns = required_product_columns | {
            "cluster_name",
            "warehouse_name",
        }
        if not {
            "ozon_stock_products",
            "ozon_stock_product_warehouses",
        }.issubset(existing):
            return False
        if not required_product_columns.issubset(table_columns(cur, "ozon_stock_products")):
            return False
        if not required_warehouse_columns.issubset(
            table_columns(cur, "ozon_stock_product_warehouses")
        ):
            return False
        truncate_targets = [
            name
            for name in (
                "ozon_stock_products",
                "ozon_stock_product_warehouses",
                "ozon_stock_product_clusters",
                "ozon_stock_clusters",
            )
            if name in existing
        ]
        cur.execute(
            sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(sql.Identifier("public", name) for name in truncate_targets)
            )
        )
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
                    source_ref,
                    "api-products",
                    index,
                    row.get("article"),
                    row.get("product_name"),
                    row.get("sku"),
                    row.get("available_to_sell"),
                    row.get("preparing_to_sell"),
                )
                for index, row in enumerate(products, start=1)
            ],
            page_size=2000,
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
                    source_ref,
                    "api-warehouses",
                    index,
                    row.get("article"),
                    row.get("product_name"),
                    row.get("sku"),
                    row.get("cluster_name"),
                    row.get("warehouse_name"),
                    row.get("available_to_sell"),
                    row.get("preparing_to_sell"),
                )
                for index, row in enumerate(rows, start=1)
            ],
            page_size=2000,
        )
        if "ozon_stock_product_clusters" not in existing:
            return True
        cluster_columns = table_columns(cur, "ozon_stock_product_clusters")
        required_cluster_columns = required_product_columns | {"cluster_name"}
        if not required_cluster_columns.issubset(cluster_columns):
            return True
        cluster_rows: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if not row.get("cluster_name"):
                continue
            key = (str(row.get("sku")), str(row.get("cluster_name")))
            target = cluster_rows.setdefault(
                key,
                {
                    **row,
                    "available_to_sell": 0,
                    "preparing_to_sell": 0,
                },
            )
            target["available_to_sell"] += row.get("available_to_sell") or 0
            target["preparing_to_sell"] += row.get("preparing_to_sell") or 0
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
                        source_ref,
                        "api-clusters",
                        index,
                        row.get("article"),
                        row.get("product_name"),
                        row.get("sku"),
                        row.get("cluster_name"),
                        row.get("available_to_sell"),
                        row.get("preparing_to_sell"),
                    )
                    for index, row in enumerate(cluster_rows.values(), start=1)
                ],
                page_size=2000,
            )
        return True


def store_snapshot(
    conn: Any,
    rows: list[dict[str, Any]],
    *,
    client_key: str,
    snapshot_date: date,
    dry_run: bool,
) -> tuple[int, bool]:
    if dry_run:
        return len(rows), False
    source_ref = (
        f"{API_SOURCE_PREFIX}seller/v2/analytics/stock_on_warehouses/"
        f"{client_key}/{datetime.now():%Y-%m-%dT%H:%M:%S}"
    )
    rows, metadata_stats = enrich_stock_metadata(conn, rows)
    inventory_history.ensure_schema(conn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM public.inventory_history_daily
                WHERE marketplace = 'ozon' AND snapshot_date = %s
                """,
                (snapshot_date,),
            )
            replaced_history_rows = cur.rowcount
        current_tables_updated = update_current_stock_tables(conn, rows, source_ref)
        history_rows = inventory_history.store_ozon_api_snapshot(
            conn, rows, snapshot_date, source_ref
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(
        f"ПРОГРЕСС: {client_key} | история остатков Ozon | "
        f"snapshot_date={snapshot_date} | строк истории {history_rows:,} | "
        f"удалено старых строк дня {replaced_history_rows:,} | "
        f"current_tables_updated={current_tables_updated} | "
        f"SKU источника {metadata_stats['source_skus']:,} | "
        f"SKU со справочником {metadata_stats['matched_skus']:,}",
        flush=True,
    )
    return history_rows, current_tables_updated


def parse_client_selection(raw_values: list[str] | None) -> set[str] | None:
    if not raw_values:
        return None
    selected: set[str] = set()
    for raw in raw_values:
        for part in str(raw or "").split(","):
            clean = str(part or "").strip().lower()
            if not clean or clean == "all":
                continue
            value = re.sub(r"[^a-z0-9_]+", "_", clean).strip("_")
            if value:
                selected.add(value)
    return selected or None


def run_client(
    api: SerialApiClient,
    client: dict[str, Any],
    *,
    snapshot_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    key = client["key"]
    if not client["credentials_saved"]:
        print(
            f"[skip] {key}: нет сохранённых Ozon Seller credentials",
            flush=True,
        )
        return {"client": key, "status": "missing_credentials", "rows": 0, "requests": 0}
    headers = seller_headers(key)
    if headers is None:
        return {"client": key, "status": "missing_credentials", "rows": 0, "requests": 0}
    conn = connect_client(key, str(client["db_name"]))
    run_id: int | None = None
    before_requests = api.request_count
    try:
        ensure_run_table(conn)
        run_id = start_run(conn, key, snapshot_date)
        rows, _ = fetch_stock(api, client_key=key, headers=headers)
        loaded, current_tables_updated = store_snapshot(
            conn,
            rows,
            client_key=key,
            snapshot_date=snapshot_date,
            dry_run=dry_run,
        )
        requests = api.request_count - before_requests
        finish_run(
            conn,
            run_id,
            status="dry_run" if dry_run else "ok",
            rows_loaded=loaded,
            requests_made=requests,
            current_tables_updated=current_tables_updated,
        )
        print(
            f"[ok] {key}: строк {loaded:,} | запросов {requests} | "
            f"current_tables_updated={current_tables_updated}",
            flush=True,
        )
        return {
            "client": key,
            "status": "dry_run" if dry_run else "ok",
            "rows": loaded,
            "requests": requests,
        }
    except Exception as exc:
        conn.rollback()
        requests = api.request_count - before_requests
        if run_id is not None:
            try:
                finish_run(
                    conn,
                    run_id,
                    status="error",
                    rows_loaded=0,
                    requests_made=requests,
                    current_tables_updated=False,
                    error=str(exc),
                )
            except Exception:
                conn.rollback()
        print(f"[error] {key}: {exc}", flush=True)
        return {"client": key, "status": "error", "rows": 0, "requests": requests, "error": str(exc)}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client",
        action="append",
        help="Клиент, список через запятую или all. По умолчанию все активные Ozon-клиенты.",
    )
    parser.add_argument("--snapshot-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--allow-non-today-date", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.snapshot_date != date.today() and not args.allow_non_today_date:
        raise SnapshotError(
            "Ozon stock API отдаёт только текущий снимок; "
            "для нестандартной даты нужен явный --allow-non-today-date"
        )
    started = time.monotonic()
    selected = parse_client_selection(args.client)
    clients = configured_ozon_clients(selected)
    with_credentials = sum(1 for client in clients if client["credentials_saved"])
    print(
        f"ПЛАН: Ozon stock snapshots | клиентов {len(clients)} | "
        f"с credentials {with_credentials} | snapshot_date={args.snapshot_date} | "
        f"пауза API {SELLER_DEFAULT_INTERVAL:.1f} сек. | "
        f"page_size={PAGE_SIZE} | режим {'dry-run' if args.dry_run else 'write'}",
        flush=True,
    )
    api = SerialApiClient()
    results: list[dict[str, Any]] = []
    for index, client in enumerate(clients, start=1):
        print(
            f"ПРОГРЕСС: клиент {index}/{len(clients)} "
            f"({index / max(len(clients), 1) * 100:.1f}%) | "
            f"{client['key']} | БД {client['db_name']} | "
            f"credentials={client['credentials_saved']}",
            flush=True,
        )
        results.append(
            run_client(
                api,
                client,
                snapshot_date=args.snapshot_date,
                dry_run=args.dry_run,
            )
        )
    elapsed = time.monotonic() - started
    ok = sum(1 for result in results if result["status"] in {"ok", "dry_run"})
    missing = sum(1 for result in results if result["status"] == "missing_credentials")
    errors = sum(1 for result in results if result["status"] == "error")
    rows = sum(int(result.get("rows") or 0) for result in results)
    requests = sum(int(result.get("requests") or 0) for result in results)
    print(
        f"ИТОГ: clients_ok={ok} | missing_credentials={missing} | errors={errors} | "
        f"rows={rows:,} | api_requests={requests} | elapsed={elapsed:.1f}s | "
        f"stopped=no | partial={'yes' if errors or missing else 'no'}",
        flush=True,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

