#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))

import app  # noqa: E402


MAPPING_COLUMNS = """
    mapping_hash text PRIMARY KEY,
    barcode text NOT NULL,
    wb_article text,
    wb_nmid text,
    ozon_sku text,
    gj_model text,
    assortment_bia text,
    tg text,
    tg_plus text,
    cg text,
    season text,
    source_file text,
    source_wb_row integer,
    source_ozon_row integer,
    imported_at timestamp without time zone DEFAULT now()
"""


def ensure_empty_seo_tag_table(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.marketplace_sku_tags (
            marketplace text NOT NULL,
            sku text NOT NULL,
            tag text NOT NULL,
            product_name text,
            source_file text,
            tagged_at timestamp without time zone NOT NULL DEFAULT now(),
            PRIMARY KEY (marketplace, sku, tag)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_sku_tags_tag "
        "ON public.marketplace_sku_tags (marketplace, tag)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_marketplace_sku_tags_sku "
        "ON public.marketplace_sku_tags (marketplace, sku)"
    )


def ensure_mapping_views(cur) -> None:
    cur.execute(f"CREATE TABLE IF NOT EXISTS public.sku_mapping_gj ({MAPPING_COLUMNS})")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sku_mapping_gj_barcode ON public.sku_mapping_gj (barcode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sku_mapping_gj_wb_article ON public.sku_mapping_gj (wb_article)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sku_mapping_gj_wb_nmid ON public.sku_mapping_gj (wb_nmid)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sku_mapping_gj_ozon_sku ON public.sku_mapping_gj (ozon_sku)")
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_sku_mapping_gj_wb_article AS
        SELECT
            wb_article,
            max(wb_nmid) AS wb_nmid,
            min(barcode) AS barcode,
            min(ozon_sku) AS ozon_sku,
            max(gj_model) AS gj_model,
            max(assortment_bia) AS assortment_bia,
            max(tg) AS tg,
            max(tg_plus) AS tg_plus,
            max(cg) AS cg,
            max(season) AS season
        FROM public.sku_mapping_gj
        WHERE wb_article IS NOT NULL
        GROUP BY wb_article
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_sku_mapping_gj_wb_nmid AS
        SELECT
            wb_nmid,
            min(wb_article) AS wb_article,
            min(barcode) AS barcode,
            min(ozon_sku) AS ozon_sku,
            max(gj_model) AS gj_model,
            max(assortment_bia) AS assortment_bia,
            max(tg) AS tg,
            max(tg_plus) AS tg_plus,
            max(cg) AS cg,
            max(season) AS season
        FROM public.sku_mapping_gj
        WHERE wb_nmid IS NOT NULL
        GROUP BY wb_nmid
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_sku_mapping_gj_ozon_sku AS
        SELECT
            ozon_sku,
            min(barcode) AS barcode,
            min(wb_article) AS wb_article,
            max(wb_nmid) AS wb_nmid,
            max(gj_model) AS gj_model,
            max(assortment_bia) AS assortment_bia,
            max(tg) AS tg,
            max(tg_plus) AS tg_plus,
            max(cg) AS cg,
            max(season) AS season
        FROM public.sku_mapping_gj
        WHERE ozon_sku IS NOT NULL
        GROUP BY ozon_sku
        """
    )
    cur.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_sku_mapping_gj_barcode AS
        SELECT
            barcode,
            min(wb_article) AS wb_article,
            max(wb_nmid) AS wb_nmid,
            min(ozon_sku) AS ozon_sku,
            max(gj_model) AS gj_model,
            max(assortment_bia) AS assortment_bia,
            max(tg) AS tg,
            max(tg_plus) AS tg_plus,
            max(cg) AS cg,
            max(season) AS season
        FROM public.sku_mapping_gj
        GROUP BY barcode
        """
    )
    for view_name in (
        "mv_sku_mapping_gj_wb_article",
        "mv_sku_mapping_gj_wb_nmid",
        "mv_sku_mapping_gj_ozon_sku",
        "mv_sku_mapping_gj_barcode",
    ):
        cur.execute(f"REFRESH MATERIALIZED VIEW public.{view_name}")
        cur.execute(f"ANALYZE public.{view_name}")


def ensure_stock_materialized_view(cur) -> None:
    cur.execute("SELECT to_regclass('public.vw_ozon_category_stock_sku_attribute_stats') AS view_name")
    if not cur.fetchone()["view_name"]:
        return
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_category_stock_sku_attribute_stats")
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_ozon_category_stock_sku_attribute_stats AS
        SELECT * FROM public.vw_ozon_category_stock_sku_attribute_stats
        """
    )
    for column in ("category_name", "total_stock_qty", "zakazano_rub", "abc_combined"):
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_mv_ozon_category_stock_{column} "
            f"ON public.mv_ozon_category_stock_sku_attribute_stats ({column})"
        )
    cur.execute("ANALYZE public.mv_ozon_category_stock_sku_attribute_stats")


def main() -> None:
    with app.get_conn() as conn, conn.cursor() as cur:
        ensure_empty_seo_tag_table(cur)
        ensure_mapping_views(cur)
        ensure_stock_materialized_view(cur)
        conn.commit()
    print("KM compatibility objects are ready")


if __name__ == "__main__":
    main()
