#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Read-only audit of KM Trade Ozon API credentials metadata and data ranges."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


TARGET_DB = os.environ.get("KM_DB_NAME", "km_trade_products")
ALLOWED_DB = "km_trade_products"
TABLES = (
    "ozon_funnel_daily",
    "ozon_adv_daily_raw",
    "ozon_stock_products",
    "ozon_stock_product_clusters",
    "ozon_stock_product_warehouses",
)


def main() -> int:
    if TARGET_DB != ALLOWED_DB:
        raise RuntimeError(
            f"Защитная остановка: разрешена только БД {ALLOWED_DB}, получено {TARGET_DB}"
        )

    print(
        "ПЛАН: read-only аудит только km_trade_products | credential-метаданные, "
        "схемы Ozon-таблиц и диапазоны дат | секретные значения не читаются",
        flush=True,
    )
    config = {**app.read_db_config(), "database": TARGET_DB}
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        current_db = cur.fetchone()[0]
        if current_db != ALLOWED_DB:
            raise RuntimeError(
                f"Защитная остановка: подключение открыто к {current_db}, ожидалась {ALLOWED_DB}"
            )
        print(f"DATABASE: {current_db}", flush=True)

        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (
                    lower(column_name) LIKE '%token%'
                 OR lower(column_name) LIKE '%api%key%'
                 OR lower(column_name) LIKE '%client%id%'
                 OR lower(column_name) LIKE '%secret%'
                 OR lower(table_name) LIKE '%credential%'
                 OR lower(table_name) LIKE '%token%'
              )
            ORDER BY table_name, column_name
            """
        )
        credential_columns = cur.fetchall()
        print(
            f"CREDENTIAL_CANDIDATES: {len(credential_columns)} "
            "(только имена, значения не читаются)",
            flush=True,
        )
        for table_name, column_name, data_type in credential_columns:
            print(f"  {table_name}.{column_name} | {data_type}", flush=True)

        for table_name in TABLES:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            columns = cur.fetchall()
            print(
                f"SCHEMA: {table_name} | "
                + ", ".join(f"{name}:{data_type}" for name, data_type in columns),
                flush=True,
            )

        for table_name in ("ozon_funnel_daily", "ozon_adv_daily_raw"):
            cur.execute(
                sql.SQL(
                    """
                    SELECT min(report_date), max(report_date), count(*),
                           count(DISTINCT report_date)
                    FROM public.{}
                    """
                ).format(sql.Identifier(table_name))
            )
            date_from, date_to, rows, days = cur.fetchone()
            print(
                f"RANGE: {table_name} | {date_from}..{date_to} | "
                f"rows={rows} | days={days}",
                flush=True,
            )

    env_path = PROJECT_ROOT / "ozon_category_dashboard" / ".env.local"
    env_names = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            env_names[name.strip()] = bool(value.strip())
    for name in (
        "OZON_SELLER_CLIENT_ID_KM_TRADE",
        "OZON_SELLER_API_KEY_KM_TRADE",
        "OZON_PERFORMANCE_CLIENT_ID_KM_TRADE",
        "OZON_PERFORMANCE_CLIENT_SECRET_KM_TRADE",
    ):
        print(f"SECRET_LOCATION: {name} | configured={env_names.get(name, False)}")

    print(
        "ИТОГ: read_only=true | database=km_trade_products | "
        "other_clients_touched=false | secret_values_read=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
