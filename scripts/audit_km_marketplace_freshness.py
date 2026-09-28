#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Secret-safe freshness audit for KM Trade marketplace source tables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))
os.environ["DASHBOARD_DB_NAME"] = "km_trade_products"

import app  # noqa: E402


def main() -> None:
    config = app.read_db_config("km_trade")
    config["database"] = "km_trade_products"
    config["application_name"] = "km_marketplace_freshness_audit"
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'wb_%'
            ORDER BY table_name
            """
        )
        print("WB_TABLES", [row[0] for row in cur.fetchall()])
        checks = {
            "wb_funnel_daily": (
                "SELECT min(report_date), max(report_date), count(*), "
                "count(DISTINCT wb_nmid) FROM public.wb_funnel_daily"
            ),
            "wb_stock_api_current": (
                "SELECT min(snapshot_date), max(snapshot_date), count(*), "
                "coalesce(sum(stock_qty), 0) FROM public.wb_stock_api_current"
            ),
            "inventory_history_daily": (
                "SELECT min(snapshot_date), max(snapshot_date), count(*), "
                "count(DISTINCT snapshot_date) FROM public.inventory_history_daily "
                "WHERE marketplace = 'ozon'"
            ),
        }
        for name, query in checks.items():
            cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
            if cur.fetchone()[0] is None:
                print(name, "MISSING")
                continue
            cur.execute(query)
            print(name, cur.fetchone())


if __name__ == "__main__":
    main()
