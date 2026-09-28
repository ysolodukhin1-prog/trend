#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-refresh evidence summary for KM Trade Ozon and WB sources."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon_category_dashboard"))
os.environ["DASHBOARD_DB_NAME"] = "km_trade_products"
import app  # noqa: E402


def main() -> None:
    config = app.read_db_config("km_trade")
    config["database"] = "km_trade_products"
    config["application_name"] = "km_refresh_result_audit"
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        queries = {
            "OZON_OBSERVED_DATES": """
                SELECT snapshot_date, count(*)
                FROM public.inventory_history_daily
                WHERE marketplace = 'ozon'
                GROUP BY snapshot_date ORDER BY snapshot_date
            """,
            "WB_SOURCE_SNAPSHOTS": """
                SELECT source_key, count(*), max(captured_at), max(row_count)
                FROM public.wb_api_source_snapshots
                GROUP BY source_key ORDER BY source_key
            """,
            "WB_ENTITIES": """
                SELECT source_key, count(*), min(record_date), max(record_date)
                FROM public.wb_api_entities
                GROUP BY source_key ORDER BY source_key
            """,
            "WB_STOCK_HISTORY": """
                SELECT min(snapshot_date), max(snapshot_date), count(DISTINCT snapshot_date),
                       count(*), count(DISTINCT nm_id),
                       count(*) FILTER (WHERE quantity > 0), coalesce(sum(quantity), 0)
                FROM public.wb_inventory_history_daily_csv
            """,
            "WB_CORE": """
                SELECT
                    (SELECT min(report_date) FROM public.wb_funnel_daily),
                    (SELECT max(report_date) FROM public.wb_funnel_daily),
                    (SELECT count(*) FROM public.wb_funnel_daily),
                    (SELECT max(snapshot_date) FROM public.wb_stock_api_current),
                    (SELECT count(*) FROM public.wb_stock_api_current),
                    (SELECT coalesce(sum(stock_qty), 0) FROM public.wb_stock_api_current)
            """,
        }
        for label, query in queries.items():
            cur.execute(query)
            rows = cur.fetchall()
            print(label)
            for row in rows:
                print("  ", tuple(row))


if __name__ == "__main__":
    main()
