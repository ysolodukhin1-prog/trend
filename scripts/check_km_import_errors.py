#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Print secret-safe KM Trade import status and failed source files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


TARGET_DB = os.environ.get("KM_DB_NAME", "km_trade_products")


def main() -> None:
    config = {**app.read_db_config(), "database": TARGET_DB}
    with psycopg2.connect(**config, cursor_factory=RealDictCursor) as conn, conn.cursor() as cur:
        for table in ("ozon_funnel_import_files", "ozon_adv_daily_import_files"):
            cur.execute(f"SELECT status, count(*) AS files, sum(rows_imported) AS rows FROM public.{table} GROUP BY status ORDER BY status")
            for row in cur.fetchall():
                print(f"STATUS: {table} | {row['status']} | files={row['files']} | rows={row['rows'] or 0}")
            cur.execute(
                f"SELECT source_file, status, error FROM public.{table} "
                "WHERE status IS DISTINCT FROM 'ok' ORDER BY source_file"
            )
            for row in cur.fetchall():
                print(f"ISSUE: {table} | {row['status']} | {row['source_file']} | {row['error'] or ''}")


if __name__ == "__main__":
    main()
