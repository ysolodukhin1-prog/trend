#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only data-quality audit for KM Trade daily inventory history."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
TARGET_DB = os.environ.get("KM_DB_NAME", "km_trade_products")
ALLOWED_DB = "km_trade_products"

os.environ["PGDATABASE"] = TARGET_DB
os.environ["DASHBOARD_CLIENT"] = "km_trade"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


def main() -> int:
    started = time.monotonic()
    if TARGET_DB != ALLOWED_DB:
        raise RuntimeError(
            f"Защитная остановка: разрешена только БД {ALLOWED_DB}, получено {TARGET_DB}"
        )

    print(
        "ПЛАН: read-only аудит истории остатков KM Trade | 6 проверок | "
        "только km_trade_products | без записи и без чтения секретных значений",
        flush=True,
    )
    config = {**app.read_db_config(), "database": TARGET_DB}
    with psycopg2.connect(**config, cursor_factory=RealDictCursor) as conn:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS database_name")
            database_name = cur.fetchone()["database_name"]
            if database_name != ALLOWED_DB:
                raise RuntimeError(
                    f"Защитная остановка: подключение к {database_name}, ожидалась {ALLOWED_DB}"
                )
            print(
                "ПРОГРЕСС: 1/6 (16.7%) | база подтверждена | "
                f"database={database_name}",
                flush=True,
            )

            cur.execute(
                "SELECT to_regclass('public.inventory_history_daily') AS relation_name"
            )
            relation_name = cur.fetchone()["relation_name"]
            print(
                "ПРОГРЕСС: 2/6 (33.3%) | схема истории | "
                f"relation={relation_name or 'missing'}",
                flush=True,
            )
            if not relation_name:
                print(
                    "ИТОГ: status=missing | table=public.inventory_history_daily | "
                    "other_clients_touched=false",
                    flush=True,
                )
                return 2

            cur.execute(
                """
                SELECT marketplace, source_type,
                       min(snapshot_date) AS date_from,
                       max(snapshot_date) AS date_to,
                       count(DISTINCT snapshot_date) AS snapshot_days,
                       count(*) AS rows,
                       count(DISTINCT sku) AS skus,
                       count(DISTINCT (sku, warehouse_name, cluster_name)) AS locations
                FROM public.inventory_history_daily
                GROUP BY marketplace, source_type
                ORDER BY marketplace, source_type
                """
            )
            source_rows = cur.fetchall()
            print(
                "ПРОГРЕСС: 3/6 (50.0%) | покрытие по источникам | "
                f"groups={len(source_rows)}",
                flush=True,
            )
            for row in source_rows:
                print(
                    "SOURCE: "
                    f"marketplace={row['marketplace']} | source={row['source_type']} | "
                    f"dates={row['date_from']}..{row['date_to']} | "
                    f"days={row['snapshot_days']} | rows={row['rows']} | "
                    f"skus={row['skus']} | locations={row['locations']}",
                    flush=True,
                )

            cur.execute(
                """
                WITH bounds AS (
                  SELECT min(snapshot_date) AS date_from, max(snapshot_date) AS date_to
                  FROM public.inventory_history_daily
                  WHERE marketplace = 'ozon'
                ), expected AS (
                  SELECT generate_series(date_from, date_to, interval '1 day')::date AS snapshot_date
                  FROM bounds
                  WHERE date_from IS NOT NULL
                ), actual AS (
                  SELECT DISTINCT snapshot_date
                  FROM public.inventory_history_daily
                  WHERE marketplace = 'ozon'
                )
                SELECT e.snapshot_date
                FROM expected e
                LEFT JOIN actual a USING (snapshot_date)
                WHERE a.snapshot_date IS NULL
                ORDER BY e.snapshot_date
                """
            )
            missing_days = [row["snapshot_date"] for row in cur.fetchall()]
            print(
                "ПРОГРЕСС: 4/6 (66.7%) | календарная непрерывность Ozon | "
                f"missing_days={len(missing_days)} | "
                f"dates={','.join(map(str, missing_days)) or '-'}",
                flush=True,
            )

            cur.execute(
                """
                WITH latest AS (
                  SELECT max(snapshot_date) AS snapshot_date
                  FROM public.inventory_history_daily
                  WHERE marketplace = 'ozon'
                ), history AS (
                  SELECT count(DISTINCT sku) AS skus,
                         coalesce(sum(stock_available_qty), 0) AS available_qty,
                         coalesce(sum(stock_preparing_qty), 0) AS preparing_qty,
                         coalesce(sum(stock_reserved_qty), 0) AS reserved_qty,
                         count(*) AS rows
                  FROM public.inventory_history_daily h
                  JOIN latest l USING (snapshot_date)
                  WHERE h.marketplace = 'ozon'
                ), current_stock AS (
                  SELECT count(DISTINCT sku) AS skus,
                         coalesce(sum(available_to_sell), 0) AS available_qty,
                         coalesce(sum(preparing_to_sell), 0) AS preparing_qty,
                         count(*) AS rows,
                         max(imported_at) AS imported_at
                  FROM public.ozon_stock_product_warehouses
                )
                SELECT l.snapshot_date,
                       h.skus AS history_skus,
                       h.available_qty AS history_available_qty,
                       h.preparing_qty AS history_preparing_qty,
                       h.reserved_qty AS history_reserved_qty,
                       h.rows AS history_rows,
                       c.skus AS current_skus,
                       c.available_qty AS current_available_qty,
                       c.preparing_qty AS current_preparing_qty,
                       c.rows AS current_rows,
                       c.imported_at AS current_imported_at
                FROM latest l CROSS JOIN history h CROSS JOIN current_stock c
                """
            )
            parity = cur.fetchone()
            parity_ok = (
                parity["history_skus"] == parity["current_skus"]
                and parity["history_available_qty"] == parity["current_available_qty"]
                and parity["history_preparing_qty"] == parity["current_preparing_qty"]
            )
            print(
                "ПРОГРЕСС: 5/6 (83.3%) | latest-vs-current parity | "
                f"date={parity['snapshot_date']} | ok={str(parity_ok).lower()} | "
                f"history_skus={parity['history_skus']} current_skus={parity['current_skus']} | "
                f"history_available={parity['history_available_qty']} "
                f"current_available={parity['current_available_qty']} | "
                f"history_preparing={parity['history_preparing_qty']} "
                f"current_preparing={parity['current_preparing_qty']} | "
                f"current_imported_at={parity['current_imported_at']}",
                flush=True,
            )

            cur.execute(
                """
                SELECT snapshot_date,
                       count(DISTINCT sku) AS skus,
                       count(*) AS rows,
                       coalesce(sum(stock_available_qty), 0) AS available_qty,
                       coalesce(sum(stock_preparing_qty), 0) AS preparing_qty,
                       coalesce(sum(stock_reserved_qty), 0) AS reserved_qty,
                       max(imported_at) AS imported_at
                FROM public.inventory_history_daily
                WHERE marketplace = 'ozon'
                GROUP BY snapshot_date
                ORDER BY snapshot_date
                """
            )
            daily_rows = cur.fetchall()
            print(
                "ПРОГРЕСС: 6/6 (100.0%) | дневной grain | "
                f"days={len(daily_rows)}",
                flush=True,
            )
            for row in daily_rows:
                print(
                    "DAY: "
                    f"{row['snapshot_date']} | skus={row['skus']} | rows={row['rows']} | "
                    f"available={row['available_qty']} | preparing={row['preparing_qty']} | "
                    f"reserved={row['reserved_qty']} | imported_at={row['imported_at']}",
                    flush=True,
                )

    elapsed = time.monotonic() - started
    print(
        "ИТОГ: read_only=true | database=km_trade_products | "
        f"ozon_days={len(daily_rows)} | missing_days={len(missing_days)} | "
        f"latest_parity={str(parity_ok).lower()} | elapsed={elapsed:.1f}s | "
        "other_clients_touched=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
