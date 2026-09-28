#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Read-only verification of the latest KM Trade Ozon API export."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


ALLOWED_DB = "km_trade_products"
DATE_FROM = "2026-07-20"
DATE_TO = "2026-07-26"


def main() -> int:
    print(
        "ПЛАН: read-only сверка Ozon API-выгрузки | "
        f"БД {ALLOWED_DB} | период {DATE_FROM}..{DATE_TO}",
        flush=True,
    )
    config = {**app.read_db_config(), "database": ALLOWED_DB}
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        current_db = cur.fetchone()[0]
        if current_db != ALLOWED_DB:
            raise RuntimeError(
                f"Защитная остановка: подключено к {current_db}, ожидалось {ALLOWED_DB}"
            )
        print(f"DATABASE: {current_db}", flush=True)

        cur.execute(
            """
            SELECT min(report_date), max(report_date), count(*),
                   count(DISTINCT report_date), count(DISTINCT sku),
                   coalesce(sum(ordered_units), 0),
                   coalesce(sum(ordered_amount_rub), 0),
                   count(*) FILTER (
                       WHERE impressions_total IS NULL
                         AND impressions_search_catalog IS NULL
                         AND card_visits IS NULL
                         AND cart_adds IS NULL
                   )
            FROM public.ozon_funnel_daily
            WHERE report_date BETWEEN %s AND %s
              AND source_file LIKE 'ozon_api://%%'
            """,
            (DATE_FROM, DATE_TO),
        )
        (
            funnel_from,
            funnel_to,
            funnel_rows,
            funnel_days,
            funnel_skus,
            funnel_orders,
            funnel_revenue,
            funnel_null_traffic,
        ) = cur.fetchone()
        print(
            "FUNNEL_API: "
            f"{funnel_from}..{funnel_to} | rows={funnel_rows} | "
            f"days={funnel_days} | sku={funnel_skus} | "
            f"orders={funnel_orders} | revenue={funnel_revenue} | "
            f"null_traffic_cart_rows={funnel_null_traffic}",
            flush=True,
        )

        cur.execute(
            """
            SELECT min(report_date), max(report_date), count(*),
                   count(DISTINCT report_date),
                   count(DISTINCT ozon_marketplace_article),
                   coalesce(sum(impressions), 0), coalesce(sum(clicks), 0),
                   coalesce(sum(expense_rub), 0),
                   coalesce(sum(orders_qty), 0),
                   coalesce(sum(orders_amount_rub), 0),
                   count(*) FILTER (WHERE added_to_cart IS NULL)
            FROM public.ozon_adv_daily_raw
            WHERE report_date BETWEEN %s AND %s
              AND source_file LIKE
                  'ozon_api://performance/statistics/daily/json/%%'
            """,
            (DATE_FROM, DATE_TO),
        )
        (
            adv_from,
            adv_to,
            adv_rows,
            adv_days,
            adv_skus,
            adv_impressions,
            adv_clicks,
            adv_expense,
            adv_orders,
            adv_revenue,
            adv_null_cart,
        ) = cur.fetchone()
        print(
            "ADV_API: "
            f"{adv_from}..{adv_to} | rows={adv_rows} | days={adv_days} | "
            f"sku={adv_skus} | impressions={adv_impressions} | "
            f"clicks={adv_clicks} | expense={adv_expense} | "
            f"orders={adv_orders} | revenue={adv_revenue} | "
            f"null_cart_rows={adv_null_cart}",
            flush=True,
        )

        cur.execute(
            """
            SELECT count(*)
            FROM public.ozon_adv_daily_raw
            WHERE source_file LIKE
                  'ozon_api://performance/statistics/campaign/product/%%'
            """
        )
        print(f"ADV_STALE_SOURCE_ROWS: {cur.fetchone()[0]}", flush=True)

        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM public.ozon_stock_products
                 WHERE source_file LIKE 'ozon_api://%%'),
                (SELECT count(*) FROM public.ozon_stock_product_warehouses
                 WHERE source_file LIKE 'ozon_api://%%'),
                (SELECT coalesce(sum(available_to_sell), 0)
                 FROM public.ozon_stock_products
                 WHERE source_file LIKE 'ozon_api://%%')
            """
        )
        stock_products, stock_warehouses, stock_available = cur.fetchone()
        print(
            "STOCK_API: "
            f"products={stock_products} | warehouses={stock_warehouses} | "
            f"available_to_sell={stock_available}",
            flush=True,
        )

        cur.execute(
            """
            SELECT step, status, rows_loaded, requests_made
            FROM public.km_ozon_api_runs
            WHERE started_at >= current_date
            ORDER BY id DESC
            LIMIT 8
            """
        )
        print("LATEST_RUNS:", flush=True)
        for step, status, rows, requests in cur.fetchall():
            print(
                f"  {step} | {status} | rows={rows} | requests={requests}",
                flush=True,
            )

    print(
        "ИТОГ: read_only=true | database=km_trade_products | "
        "other_clients_touched=false | secrets_read=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
