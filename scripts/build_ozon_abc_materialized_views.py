#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build fast base materialized views for Ozon ABC reports."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


ABC_MATERIALIZED_VIEWS = [
    "mv_ozon_abc_product_orders_base",
    "mv_ozon_abc_product_stock_base",
]

DEPENDENT_ABC_MATERIALIZED_VIEWS = [
    "mv_product_abc_ozon",
    "mv_sku_card_scoring_ozon",
]


def drop_ozon_abc_materialized_views(cur: Any) -> None:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_product_abc_ozon")
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_sku_card_scoring_ozon")
    for view_name in ABC_MATERIALIZED_VIEWS:
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")


def create_or_refresh_ozon_abc_materialized_views(cur: Any, rebuild_dependents: bool = True) -> None:
    drop_ozon_abc_materialized_views(cur)
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_ozon_abc_product_orders_base AS
        WITH cat_products AS (
            SELECT
                sku,
                max(nazvanie_tovara) FILTER (WHERE nazvanie_tovara IS NOT NULL AND nazvanie_tovara <> '') AS product_name,
                max(category_name) FILTER (WHERE category_name IS NOT NULL AND category_name <> '') AS category_name
            FROM public.ozon_cat_products
            WHERE sku IS NOT NULL
            GROUP BY sku
        ),
        products AS (
            SELECT
                sku,
                max(nazvanie_tovara) FILTER (WHERE nazvanie_tovara IS NOT NULL AND nazvanie_tovara <> '') AS product_name,
                max(coalesce(nullif(tip, ''), nullif(kategoriya, ''))) AS category_name,
                max(kontent_reyting) FILTER (WHERE kontent_reyting IS NOT NULL AND kontent_reyting <> '') AS reyting_kartochki,
                max(reyting) FILTER (WHERE reyting IS NOT NULL AND reyting <> '') AS reyting_po_otzyvam
            FROM public.ozon_products
            WHERE sku IS NOT NULL
            GROUP BY sku
        )
        SELECT
            v.report_date,
            v.sku::text AS artikul_wb,
            max(coalesce(v.product_name, p.product_name, cp.product_name, 'Без названия')) AS naimenovanie,
            max(coalesce(v.category_name, cp.category_name, p.category_name, 'Без категории')) AS category_name,
            max(coalesce(nullif(v.category_level_3, ''), nullif(v.category_level_2, ''), nullif(v.category_name, ''), cp.category_name, p.category_name, 'Без категории')) AS subcategory_name,
            coalesce(sum(v.ordered_units), 0)::numeric AS zakazano_sht,
            coalesce(sum(v.ordered_amount_rub), 0)::numeric AS zakazano_rub,
            max(p.reyting_kartochki) AS reyting_kartochki,
            max(p.reyting_po_otzyvam) AS reyting_po_otzyvam,
            max(m.wb_article) AS gj_wb_article,
            max(m.ozon_sku) AS gj_ozon_sku,
            max(m.gj_model) AS gj_model,
            max(m.assortment_bia) AS assortment_bia,
            max(m.tg) AS tg,
            max(m.tg_plus) AS tg_plus,
            max(m.cg) AS cg,
            max(m.season) AS season
        FROM public.mv_ozon_funnel_daily_by_article_category v
        LEFT JOIN cat_products cp ON cp.sku = v.sku
        LEFT JOIN products p ON p.sku = v.sku
        LEFT JOIN public.mv_sku_mapping_gj_barcode m ON m.barcode = v.barcode
        WHERE v.sku IS NOT NULL
        GROUP BY v.report_date, v.sku::text
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_ozon_abc_product_stock_base AS
        WITH cat_products AS (
            SELECT
                sku,
                max(nazvanie_tovara) FILTER (WHERE nazvanie_tovara IS NOT NULL AND nazvanie_tovara <> '') AS product_name,
                max(category_name) FILTER (WHERE category_name IS NOT NULL AND category_name <> '') AS category_name
            FROM public.ozon_cat_products
            WHERE sku IS NOT NULL
            GROUP BY sku
        ),
        products AS (
            SELECT
                sku,
                max(nazvanie_tovara) FILTER (WHERE nazvanie_tovara IS NOT NULL AND nazvanie_tovara <> '') AS product_name,
                max(coalesce(nullif(tip, ''), nullif(kategoriya, ''))) AS category_name,
                max(kontent_reyting) FILTER (WHERE kontent_reyting IS NOT NULL AND kontent_reyting <> '') AS reyting_kartochki,
                max(reyting) FILTER (WHERE reyting IS NOT NULL AND reyting <> '') AS reyting_po_otzyvam
            FROM public.ozon_products
            WHERE sku IS NOT NULL
            GROUP BY sku
        ),
        funnel_products AS (
            SELECT
                sku,
                max(product_name) FILTER (WHERE product_name IS NOT NULL AND product_name <> '') AS product_name,
                max(category_name) FILTER (WHERE category_name IS NOT NULL AND category_name <> '') AS category_name,
                max(coalesce(nullif(category_level_3, ''), nullif(category_level_2, ''), nullif(category_name, ''))) AS subcategory_name
            FROM public.mv_ozon_funnel_daily_by_article_category
            WHERE sku IS NOT NULL
            GROUP BY sku
        )
        SELECT
            st.sku::text AS artikul_wb,
            max(coalesce(p.product_name, cp.product_name, fp.product_name, st.product_name, st.article, st.sku::text)) AS naimenovanie,
            max(coalesce(cp.category_name, p.category_name, fp.category_name, 'Без категории')) AS category_name,
            max(coalesce(fp.subcategory_name, cp.category_name, p.category_name, fp.category_name, 'Без категории')) AS subcategory_name,
            coalesce(sum(st.available_to_sell), 0)::numeric AS total_stock_qty,
            max(p.reyting_kartochki) AS reyting_kartochki,
            max(p.reyting_po_otzyvam) AS reyting_po_otzyvam,
            max(m.wb_article) AS gj_wb_article,
            max(m.ozon_sku) AS gj_ozon_sku,
            max(m.gj_model) AS gj_model,
            max(m.assortment_bia) AS assortment_bia,
            max(m.tg) AS tg,
            max(m.tg_plus) AS tg_plus,
            max(m.cg) AS cg,
            max(m.season) AS season
        FROM public.vw_ozon_current_stock_by_sku st
        LEFT JOIN cat_products cp ON cp.sku = st.sku
        LEFT JOIN products p ON p.sku = st.sku
        LEFT JOIN funnel_products fp ON fp.sku = st.sku
        LEFT JOIN public.mv_sku_mapping_gj_ozon_sku m ON m.ozon_sku = st.sku
        WHERE st.sku IS NOT NULL
        GROUP BY st.sku::text
        """
    )
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_date ON public.mv_ozon_abc_product_orders_base(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_category ON public.mv_ozon_abc_product_orders_base(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_subcategory ON public.mv_ozon_abc_product_orders_base(subcategory_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_sku ON public.mv_ozon_abc_product_orders_base(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_date_category ON public.mv_ozon_abc_product_orders_base(report_date, category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_gj_model ON public.mv_ozon_abc_product_orders_base(gj_model)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_assortment ON public.mv_ozon_abc_product_orders_base(assortment_bia)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_tg ON public.mv_ozon_abc_product_orders_base(tg)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_tg_plus ON public.mv_ozon_abc_product_orders_base(tg_plus)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_cg ON public.mv_ozon_abc_product_orders_base(cg)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_orders_season ON public.mv_ozon_abc_product_orders_base(season)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_category ON public.mv_ozon_abc_product_stock_base(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_subcategory ON public.mv_ozon_abc_product_stock_base(subcategory_name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_sku ON public.mv_ozon_abc_product_stock_base(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_gj_model ON public.mv_ozon_abc_product_stock_base(gj_model)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_assortment ON public.mv_ozon_abc_product_stock_base(assortment_bia)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_tg ON public.mv_ozon_abc_product_stock_base(tg)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_tg_plus ON public.mv_ozon_abc_product_stock_base(tg_plus)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_cg ON public.mv_ozon_abc_product_stock_base(cg)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_abc_stock_season ON public.mv_ozon_abc_product_stock_base(season)",
    ]
    for index_sql in indexes:
        cur.execute(index_sql)
    for view_name in ABC_MATERIALIZED_VIEWS:
        cur.execute(f"ANALYZE public.{view_name}")
    if rebuild_dependents:
        from rebuild_ozon_sku_scoring_view import rebuild_sku_scoring_ozon

        rebuild_sku_scoring_ozon(cur)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Ozon ABC materialized views")
    parser.add_argument(
        "--skip-dependents",
        action="store_true",
        help="Rebuild only Ozon ABC base views; leave SKU scoring/product ABC to a separate job.",
    )
    args = parser.parse_args()
    started = time.monotonic()
    with app.get_conn() as conn:
        with conn.cursor() as cur:
            create_or_refresh_ozon_abc_materialized_views(cur, rebuild_dependents=not args.skip_dependents)
        conn.commit()
    print(f"Ozon ABC materialized views rebuilt in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
