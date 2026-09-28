#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish the current normalized KM Trade WB stock into shared inventory history."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ozon_category_dashboard"))
os.environ["DASHBOARD_DB_NAME"] = "km_trade_products"
import app  # noqa: E402
import inventory_history  # noqa: E402


def main() -> None:
    started = time.monotonic()
    config = app.read_db_config("km_trade")
    config["database"] = "km_trade_products"
    config["application_name"] = "km_wb_current_stock_publisher"
    print("ПЛАН: KM Trade WB current stock | 1 SQL read + 1 upsert batch | history date from source snapshot", flush=True)
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT snapshot_date, wb_nmid, seller_article, product_name,
                   category_name, stock_qty, stock_amount_rub,
                   to_customer_qty, from_customer_qty
            FROM public.wb_stock_api_current
            ORDER BY wb_nmid
            """
        )
        columns = [column.name for column in cur.description]
        source_rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        if not source_rows:
            raise RuntimeError("wb_stock_api_current пуст — снимок не опубликован")
        snapshot_dates = {row["snapshot_date"] for row in source_rows}
        if len(snapshot_dates) != 1:
            raise RuntimeError(f"Ожидалась одна дата снимка, получено: {sorted(snapshot_dates)}")
        snapshot_date = next(iter(snapshot_dates))
        items = [
            {
                "nmID": row["wb_nmid"],
                "vendorCode": row["seller_article"],
                "name": row["product_name"],
                "subjectName": row["category_name"],
                "metrics": {
                    "stockCount": row["stock_qty"],
                    "stockSum": row["stock_amount_rub"],
                    "toClientCount": row["to_customer_qty"],
                    "fromClientCount": row["from_customer_qty"],
                },
            }
            for row in source_rows
        ]
        loaded = inventory_history.store_wb_product_snapshot(
            conn,
            snapshot_date,
            items,
            "database://public.wb_stock_api_current",
        )
        conn.commit()
    print(
        f"ИТОГ: snapshot_date={snapshot_date} | source_rows={len(source_rows)} | "
        f"imported={loaded} | errors=0 | elapsed={time.monotonic() - started:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()

