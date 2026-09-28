#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Idempotent KM Trade migration for inventory-history metadata and lookup index."""

from __future__ import annotations

import argparse
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить индекс и metadata backfill. Без флага выполняется read-only аудит.",
    )
    return parser.parse_args()


def metadata_quality(cur) -> dict[str, int]:
    cur.execute(
        """
        WITH product_meta AS (
          SELECT DISTINCT ON (trim(sku))
                 trim(sku) AS sku,
                 nullif(trim(artikul), '') AS seller_article,
                 nullif(trim(nazvanie_tovara), '') AS product_name,
                 nullif(trim(category_name), '') AS category_name
          FROM public.ozon_cat_products
          WHERE nullif(trim(sku), '') IS NOT NULL
          ORDER BY trim(sku), updated_at DESC NULLS LAST,
                   imported_at DESC NULLS LAST, product_id DESC
        )
        SELECT
          count(*) FILTER (WHERE h.marketplace = 'ozon') AS history_rows,
          count(DISTINCT h.sku) FILTER (WHERE h.marketplace = 'ozon') AS history_skus,
          count(*) FILTER (
            WHERE h.marketplace = 'ozon' AND p.sku IS NOT NULL
          ) AS matched_rows,
          count(DISTINCT h.sku) FILTER (
            WHERE h.marketplace = 'ozon' AND p.sku IS NOT NULL
          ) AS matched_skus,
          count(*) FILTER (
            WHERE h.marketplace = 'ozon'
              AND nullif(trim(h.seller_article), '') IS NULL
          ) AS missing_article_rows,
          count(*) FILTER (
            WHERE h.marketplace = 'ozon'
              AND nullif(trim(h.product_name), '') IS NULL
          ) AS missing_name_rows,
          count(*) FILTER (
            WHERE h.marketplace = 'ozon'
              AND nullif(trim(h.category_name), '') IS NULL
          ) AS missing_category_rows,
          count(*) FILTER (
            WHERE h.marketplace = 'ozon'
              AND nullif(trim(h.category_name), '') IS NULL
              AND p.category_name IS NOT NULL
          ) AS fillable_category_rows
        FROM public.inventory_history_daily h
        LEFT JOIN product_meta p ON p.sku = h.sku
        """
    )
    return dict(cur.fetchone())


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    if TARGET_DB != ALLOWED_DB:
        raise RuntimeError(
            f"Защитная остановка: разрешена только БД {ALLOWED_DB}, получено {TARGET_DB}"
        )
    mode = "apply" if args.apply else "dry-run"
    print(
        f"ПЛАН: KM Trade inventory metadata migration | mode={mode} | шагов 5 | "
        "только km_trade_products | идемпотентный UPDATE только пустых полей",
        flush=True,
    )
    config = {**app.read_db_config(), "database": TARGET_DB}
    with psycopg2.connect(**config, cursor_factory=RealDictCursor) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS database_name")
            database_name = cur.fetchone()["database_name"]
            if database_name != ALLOWED_DB:
                raise RuntimeError(
                    f"Защитная остановка: подключение к {database_name}, ожидалась {ALLOWED_DB}"
                )
            print(
                "ПРОГРЕСС: 1/5 (20.0%) | база подтверждена | "
                f"database={database_name}",
                flush=True,
            )

            cur.execute(
                """
                SELECT to_regclass('public.inventory_history_daily') AS history_table,
                       to_regclass('public.ozon_cat_products') AS product_table
                """
            )
            relations = dict(cur.fetchone())
            if not relations["history_table"] or not relations["product_table"]:
                raise RuntimeError(
                    "Нужны public.inventory_history_daily и public.ozon_cat_products"
                )
            print(
                "ПРОГРЕСС: 2/5 (40.0%) | таблицы подтверждены | "
                f"history={relations['history_table']} | catalog={relations['product_table']}",
                flush=True,
            )

            before = metadata_quality(cur)
            print(
                "ПРОГРЕСС: 3/5 (60.0%) | до миграции | "
                f"rows={before['history_rows']} | skus={before['history_skus']} | "
                f"matched_skus={before['matched_skus']} | "
                f"missing_category={before['missing_category_rows']} | "
                f"fillable_category={before['fillable_category_rows']}",
                flush=True,
            )

            updated_rows = 0
            if args.apply:
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_inventory_history_market_date_sku
                    ON public.inventory_history_daily (
                      marketplace, snapshot_date DESC, sku
                    )
                    """
                )
                cur.execute(
                    """
                    WITH product_meta AS (
                      SELECT DISTINCT ON (trim(sku))
                             trim(sku) AS sku,
                             nullif(trim(artikul), '') AS seller_article,
                             nullif(trim(nazvanie_tovara), '') AS product_name,
                             nullif(trim(category_name), '') AS category_name
                      FROM public.ozon_cat_products
                      WHERE nullif(trim(sku), '') IS NOT NULL
                      ORDER BY trim(sku), updated_at DESC NULLS LAST,
                               imported_at DESC NULLS LAST, product_id DESC
                    )
                    UPDATE public.inventory_history_daily h
                    SET seller_article = coalesce(
                          nullif(trim(h.seller_article), ''), p.seller_article
                        ),
                        product_name = coalesce(
                          nullif(trim(h.product_name), ''), p.product_name
                        ),
                        category_name = coalesce(
                          nullif(trim(h.category_name), ''), p.category_name
                        )
                    FROM product_meta p
                    WHERE h.marketplace = 'ozon'
                      AND h.sku = p.sku
                      AND (
                        (
                          nullif(trim(h.seller_article), '') IS NULL
                          AND p.seller_article IS NOT NULL
                        ) OR (
                          nullif(trim(h.product_name), '') IS NULL
                          AND p.product_name IS NOT NULL
                        ) OR (
                          nullif(trim(h.category_name), '') IS NULL
                          AND p.category_name IS NOT NULL
                        )
                      )
                    """
                )
                updated_rows = cur.rowcount
                cur.execute("ANALYZE public.inventory_history_daily")
                conn.commit()
            else:
                conn.rollback()
            print(
                "ПРОГРЕСС: 4/5 (80.0%) | применение | "
                f"mode={mode} | updated_rows={updated_rows}",
                flush=True,
            )

        with conn.cursor() as cur:
            after = metadata_quality(cur)
            cur.execute(
                """
                SELECT count(*) AS index_count
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'inventory_history_daily'
                  AND indexname = 'idx_inventory_history_market_date_sku'
                """
            )
            index_count = cur.fetchone()["index_count"]
            print(
                "ПРОГРЕСС: 5/5 (100.0%) | проверка | "
                f"index_present={bool(index_count)} | "
                f"missing_article={after['missing_article_rows']} | "
                f"missing_name={after['missing_name_rows']} | "
                f"missing_category={after['missing_category_rows']}",
                flush=True,
            )

    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: status=ok | mode={mode} | database={ALLOWED_DB} | "
        f"updated_rows={updated_rows} | elapsed={elapsed:.1f}s | "
        "other_clients_touched=false",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
