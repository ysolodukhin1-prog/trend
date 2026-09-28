#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("WB_PRODUCTS_DB_NAME", "wb_products"))

import app  # noqa: E402


PRODUCT_ABC_SQL = """
WITH base AS (
    SELECT
        category_name,
        coalesce(nullif(subcategory_name, ''), category_name) AS subcategory_name,
        artikul_wb,
        naimenovanie,
        coalesce(total_stock_qty, 0)::numeric AS total_stock_qty,
        coalesce(zakazano_sht, 0)::numeric AS zakazano_sht,
        coalesce(zakazano_rub, 0)::numeric AS zakazano_rub,
        reyting_kartochki,
        reyting_po_otzyvam
    FROM public.mv_sku_card_scoring_wb
),
totals AS (
    SELECT
        coalesce(sum(zakazano_sht), 0)::numeric AS total_orders_qty,
        coalesce(sum(zakazano_rub), 0)::numeric AS total_orders_rub,
        coalesce(sum(total_stock_qty), 0)::numeric AS total_stock
    FROM base
),
ranked AS (
    SELECT
        b.*,
        CASE WHEN t.total_orders_qty > 0 THEN round(b.zakazano_sht / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_qty_share_pct,
        CASE WHEN t.total_orders_qty > 0 THEN round(sum(b.zakazano_sht) OVER (ORDER BY b.zakazano_sht DESC NULLS LAST, b.artikul_wb) / t.total_orders_qty * 100, 4) ELSE 0 END AS orders_qty_cumulative_pct,
        CASE WHEN t.total_orders_rub > 0 THEN round(b.zakazano_rub / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_share_pct,
        CASE WHEN t.total_orders_rub > 0 THEN round(sum(b.zakazano_rub) OVER (ORDER BY b.zakazano_rub DESC NULLS LAST, b.artikul_wb) / t.total_orders_rub * 100, 4) ELSE 0 END AS sales_cumulative_pct,
        CASE WHEN t.total_stock > 0 THEN round(b.total_stock_qty / t.total_stock * 100, 4) ELSE 0 END AS stock_share_pct,
        CASE WHEN t.total_stock > 0 THEN round(sum(b.total_stock_qty) OVER (ORDER BY b.total_stock_qty DESC NULLS LAST, b.artikul_wb) / t.total_stock * 100, 4) ELSE 0 END AS stock_cumulative_pct,
        t.total_orders_qty,
        t.total_orders_rub,
        t.total_stock
    FROM base b
    CROSS JOIN totals t
),
classified AS (
    SELECT
        *,
        CASE WHEN total_orders_qty = 0 THEN 'Без ABC' WHEN orders_qty_cumulative_pct <= 80 THEN 'A' WHEN orders_qty_cumulative_pct <= 95 THEN 'B' ELSE 'C' END AS abc_orders,
        CASE WHEN total_orders_rub = 0 THEN 'Без ABC' WHEN sales_cumulative_pct <= 80 THEN 'A' WHEN sales_cumulative_pct <= 95 THEN 'B' ELSE 'C' END AS abc_sales,
        CASE WHEN total_stock = 0 THEN 'Без ABC' WHEN stock_cumulative_pct <= 80 THEN 'A' WHEN stock_cumulative_pct <= 95 THEN 'B' ELSE 'C' END AS abc_stock
    FROM ranked
)
SELECT
    category_name,
    subcategory_name,
    artikul_wb,
    naimenovanie,
    total_stock_qty,
    zakazano_sht,
    zakazano_rub,
    orders_qty_share_pct,
    orders_qty_cumulative_pct,
    abc_orders,
    sales_share_pct,
    sales_cumulative_pct,
    abc_sales,
    stock_share_pct,
    stock_cumulative_pct,
    abc_stock,
    CASE WHEN abc_orders = 'Без ABC' AND abc_sales = 'Без ABC' AND abc_stock = 'Без ABC' THEN 'Без ABC' ELSE concat(abc_orders, abc_sales, abc_stock) END AS abc_combined,
    reyting_kartochki,
    reyting_po_otzyvam
FROM classified
"""


def relation_exists(cur, relation_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS relation_id", (f"public.{relation_name}",))
    row = cur.fetchone()
    return bool(row and row["relation_id"])


def relation_columns(cur, relation_name: str) -> set[str]:
    cur.execute(
        """
        SELECT a.attname AS column_name
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        (relation_name,),
    )
    return {row["column_name"] for row in cur.fetchall()}


def first_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in columns:
            return column
    return None


def nullable_text_expr(alias: str, columns: set[str], candidates: tuple[str, ...], fallback: str = "NULL::text") -> str:
    column = first_column(columns, candidates)
    if not column:
        return fallback
    return f"nullif({alias}.{column}::text, '')"


def sum_expr(alias: str, columns: set[str], column: str) -> str:
    if column not in columns:
        return "0::numeric"
    return f"coalesce({alias}.{column}, 0)::numeric"


def build_funnel_orders_cte(cur) -> str:
    if not relation_exists(cur, "wb_funnel_daily"):
        return """
            funnel_orders AS (
                SELECT NULL::text AS article_key, 0::numeric AS zakazano_sht,
                       0::numeric AS zakazano_rub, NULL::numeric AS reyting_kartochki,
                       NULL::numeric AS reyting_po_otzyvam
                WHERE false
            )
        """

    columns = relation_columns(cur, "wb_funnel_daily")
    ordered_units = sum_expr("f", columns, "ordered_units")
    ordered_amount = sum_expr("f", columns, "ordered_amount_rub")
    card_rating = "f.card_rating::numeric" if "card_rating" in columns else "NULL::numeric"
    review_rating = "f.review_rating::numeric" if "review_rating" in columns else "NULL::numeric"
    selects = []
    if "wb_nmid" in columns:
        selects.append(
            f"""
            SELECT nullif(f.wb_nmid::text, '') AS article_key,
                   {ordered_units} AS zakazano_sht,
                   {ordered_amount} AS zakazano_rub,
                   {card_rating} AS reyting_kartochki,
                   {review_rating} AS reyting_po_otzyvam
            FROM public.wb_funnel_daily f
            WHERE f.wb_nmid IS NOT NULL
            """
        )
    if "seller_article" in columns:
        selects.append(
            f"""
            SELECT nullif(f.seller_article::text, '') AS article_key,
                   {ordered_units} AS zakazano_sht,
                   {ordered_amount} AS zakazano_rub,
                   {card_rating} AS reyting_kartochki,
                   {review_rating} AS reyting_po_otzyvam
            FROM public.wb_funnel_daily f
            WHERE nullif(f.seller_article::text, '') IS NOT NULL
            """
        )
    if not selects:
        return """
            funnel_orders AS (
                SELECT NULL::text AS article_key, 0::numeric AS zakazano_sht,
                       0::numeric AS zakazano_rub, NULL::numeric AS reyting_kartochki,
                       NULL::numeric AS reyting_po_otzyvam
                WHERE false
            )
        """

    return f"""
            funnel_orders AS (
                SELECT
                    article_key,
                    sum(zakazano_sht)::numeric AS zakazano_sht,
                    sum(zakazano_rub)::numeric AS zakazano_rub,
                    max(reyting_kartochki)::numeric AS reyting_kartochki,
                    max(reyting_po_otzyvam)::numeric AS reyting_po_otzyvam
                FROM (
                    {" UNION ALL ".join(selects)}
                ) source
                WHERE article_key IS NOT NULL
                GROUP BY article_key
            )
        """


def build_stock_cte(cur) -> str:
    stock_selects = []

    if relation_exists(cur, "stock_history_import"):
        columns = relation_columns(cur, "stock_history_import")
        qty_column = first_column(columns, ("stock_qty", "quantity", "qty", "ostatok"))
        if qty_column:
            date_column = first_column(columns, ("stock_date", "report_date", "date"))
            where_latest = ""
            if date_column:
                where_latest = f"WHERE s.{date_column} = (SELECT max({date_column}) FROM public.stock_history_import)"
            for key_column in ("artikul_wb", "artikul_prodavtsa", "wb_nmid", "seller_article"):
                if key_column in columns:
                    stock_selects.append(
                        f"""
                        SELECT nullif(s.{key_column}::text, '') AS stock_key,
                               sum(coalesce(s.{qty_column}, 0))::numeric AS total_stock_qty
                        FROM public.stock_history_import s
                        {where_latest}
                        GROUP BY nullif(s.{key_column}::text, '')
                        """
                    )

    if not stock_selects and relation_exists(cur, "wb_funnel_daily"):
        columns = relation_columns(cur, "wb_funnel_daily")
        stock_parts = [sum_expr("f", columns, column) for column in ("stock_wb_qty", "stock_own_qty") if column in columns]
        if stock_parts and "report_date" in columns:
            stock_value = " + ".join(stock_parts)
            if "wb_nmid" in columns:
                stock_selects.append(
                    f"""
                    SELECT stock_key, sum(stock_qty)::numeric AS total_stock_qty
                    FROM (
                        SELECT nullif(f.wb_nmid::text, '') AS stock_key,
                               ({stock_value}) AS stock_qty,
                               rank() OVER (PARTITION BY f.wb_nmid ORDER BY f.report_date DESC) AS date_rank
                        FROM public.wb_funnel_daily f
                        WHERE f.wb_nmid IS NOT NULL
                    ) latest
                    WHERE date_rank = 1
                    GROUP BY stock_key
                    """
                )
            if "seller_article" in columns:
                stock_selects.append(
                    f"""
                    SELECT stock_key, sum(stock_qty)::numeric AS total_stock_qty
                    FROM (
                        SELECT nullif(f.seller_article::text, '') AS stock_key,
                               ({stock_value}) AS stock_qty,
                               rank() OVER (PARTITION BY f.seller_article ORDER BY f.report_date DESC) AS date_rank
                        FROM public.wb_funnel_daily f
                        WHERE nullif(f.seller_article::text, '') IS NOT NULL
                    ) latest
                    WHERE date_rank = 1
                    GROUP BY stock_key
                    """
                )

    if not stock_selects:
        return """
            stock AS (
                SELECT NULL::text AS stock_key, 0::numeric AS total_stock_qty
                WHERE false
            )
        """

    return f"""
            stock AS (
                SELECT stock_key, sum(total_stock_qty)::numeric AS total_stock_qty
                FROM (
                    {" UNION ALL ".join(stock_selects)}
                ) source
                WHERE stock_key IS NOT NULL
                GROUP BY stock_key
            )
        """


def mapping_column_expr(alias: str, columns: set[str], column: str) -> str:
    if column in columns:
        return f"{alias}.{column}::text"
    return "NULL::text"


def build_mapping_cte(cur) -> str:
    selects = []
    mapping_columns = ("wb_article", "ozon_sku", "gj_model", "assortment_bia", "tg", "tg_plus", "cg", "season")
    for view_name, key_column in (
        ("mv_sku_mapping_gj_wb_nmid", "wb_nmid"),
        ("mv_sku_mapping_gj_wb_article", "wb_article"),
    ):
        if not relation_exists(cur, view_name):
            continue
        columns = relation_columns(cur, view_name)
        if key_column not in columns:
            continue
        select_columns = ",\n                       ".join(
            f"{mapping_column_expr('m', columns, column)} AS {column}" for column in mapping_columns
        )
        selects.append(
            f"""
            SELECT nullif(m.{key_column}::text, '') AS map_key,
                   {select_columns}
            FROM public.{view_name} m
            WHERE nullif(m.{key_column}::text, '') IS NOT NULL
            """
        )

    if not selects:
        return """
            mapping AS (
                SELECT NULL::text AS map_key, NULL::text AS wb_article,
                       NULL::text AS ozon_sku, NULL::text AS gj_model,
                       NULL::text AS assortment_bia, NULL::text AS tg,
                       NULL::text AS tg_plus, NULL::text AS cg, NULL::text AS season
                WHERE false
            )
        """

    return f"""
            mapping AS (
                {" UNION ALL ".join(selects)}
            )
        """


def create_sku_card_view(cur) -> None:
    product_columns = relation_columns(cur, "products")
    product_key_parts = []
    for candidate in ("artikul_wb", "artikul_prodavtsa", "wb_nmid", "nmid"):
        if candidate in product_columns:
            product_key_parts.append(f"nullif(p.{candidate}::text, '')")
    product_key_expr = f"coalesce({', '.join(product_key_parts)}, p.product_id::text)" if product_key_parts else "p.product_id::text"
    category_parts = [
        expr
        for expr in (
            "nullif(p.seller_category_name::text, '')" if "seller_category_name" in product_columns else "",
            "nullif(p.kategoriya_prodavtsa::text, '')" if "kategoriya_prodavtsa" in product_columns else "",
        )
        if expr
    ]
    category_expr = f"coalesce({', '.join(category_parts)}, 'Без категории')" if category_parts else "'Без категории'::text"
    name_expr = nullable_text_expr("p", product_columns, ("naimenovanie", "nazvanie", "product_name", "tovar"))
    description_expr = nullable_text_expr("p", product_columns, ("opisanie", "description"))
    photo_expr = nullable_text_expr("p", product_columns, ("foto", "photos", "photo", "media"))
    funnel_orders_cte = build_funnel_orders_cte(cur)
    stock_cte = build_stock_cte(cur)

    cur.execute(
        f"""
        CREATE MATERIALIZED VIEW public.mv_sku_card_scoring_wb AS
        WITH common_total AS (
            SELECT count(*)::bigint AS value FROM public.common_attributes
        ),
        category_totals AS (
            SELECT category_id, count(DISTINCT attribute_id)::bigint AS value
            FROM public.category_attributes
            GROUP BY category_id
        ),
        product_attr_counts AS (
            SELECT
                p.product_id,
                count(DISTINCT pa.attribute_id) FILTER (WHERE ca.attribute_id IS NOT NULL)::bigint AS common_filled,
                count(DISTINCT pa.attribute_id) FILTER (WHERE cat.attribute_id IS NOT NULL)::bigint AS category_filled
            FROM public.products p
            LEFT JOIN public.product_attributes pa ON pa.product_id = p.product_id AND nullif(trim(pa.value_text), '') IS NOT NULL
            LEFT JOIN public.common_attributes ca ON ca.attribute_id = pa.attribute_id
            LEFT JOIN public.category_attributes cat ON cat.category_id = p.category_id AND cat.attribute_id = pa.attribute_id
            GROUP BY p.product_id
        ),
        {funnel_orders_cte},
        {stock_cte},
        products_base AS (
            SELECT
                p.product_id,
                p.category_id,
                {product_key_expr} AS product_key,
                {category_expr} AS category_name,
                {name_expr} AS naimenovanie,
                {description_expr} AS opisanie,
                {photo_expr} AS foto
            FROM public.products p
        ),
        products_grouped AS (
            SELECT
                p.product_key,
                max(p.category_id) AS category_id,
                max(nullif(p.category_name, '')) AS category_name,
                max(nullif(p.naimenovanie, '')) AS naimenovanie,
                max(nullif(p.opisanie, '')) AS opisanie,
                max(nullif(p.foto, '')) AS foto,
                max(coalesce(pac.common_filled, 0))::bigint AS common_attrs_filled,
                max(coalesce(pac.category_filled, 0))::bigint AS category_attrs_filled
            FROM products_base p
            LEFT JOIN product_attr_counts pac ON pac.product_id = p.product_id
            WHERE p.product_key IS NOT NULL
            GROUP BY p.product_key
        )
        SELECT
            coalesce(nullif(p.category_name, ''), 'Без категории') AS category_name,
            coalesce(nullif(p.category_name, ''), 'Без категории') AS subcategory_name,
            p.product_key AS artikul_wb,
            coalesce(nullif(p.naimenovanie, ''), p.product_key) AS naimenovanie,
            length(coalesce(nullif(p.naimenovanie, ''), ''))::integer AS naimenovanie_len,
            length(coalesce(nullif(p.opisanie, ''), ''))::integer AS opisanie_len,
            ct.value AS common_attrs_total,
            coalesce(p.common_attrs_filled, 0)::bigint AS common_attrs_filled,
            coalesce(cat_total.value, 0)::bigint AS category_attrs_total,
            coalesce(p.category_attrs_filled, 0)::bigint AS category_attrs_filled,
            CASE
                WHEN nullif(trim(coalesce(p.foto, '')), '') IS NULL THEN 0
                ELSE greatest(1, array_length(regexp_split_to_array(p.foto, '\\s*[;,]\\s*'), 1))
            END::integer AS foto_count,
            coalesce(st.total_stock_qty, 0)::numeric AS total_stock_qty,
            coalesce(fo.zakazano_sht, 0)::numeric AS zakazano_sht,
            coalesce(fo.zakazano_rub, 0)::numeric AS zakazano_rub,
            fo.reyting_kartochki,
            fo.reyting_po_otzyvam
        FROM products_grouped p
        CROSS JOIN common_total ct
        LEFT JOIN category_totals cat_total ON cat_total.category_id = p.category_id
        LEFT JOIN funnel_orders fo ON fo.article_key = p.product_key
        LEFT JOIN stock st ON st.stock_key = p.product_key
        WHERE p.product_key IS NOT NULL
        """
    )


def create_category_stats_view(cur) -> None:
    cur.execute(
        """
        CREATE MATERIALIZED VIEW public.mv_category_stock_sku_attribute_stats AS
        SELECT
            category_name,
            count(DISTINCT artikul_wb)::bigint AS sku_count,
            sum(total_stock_qty)::numeric AS total_stock_qty,
            avg(common_attrs_total)::numeric AS common_attrs_total,
            avg(common_attrs_filled)::numeric AS common_attrs_filled,
            avg(category_attrs_total)::numeric AS category_attrs_total,
            avg(category_attrs_filled)::numeric AS category_attrs_filled,
            sum(zakazano_sht)::numeric AS zakazano_sht,
            sum(zakazano_rub)::numeric AS zakazano_rub
        FROM public.mv_sku_card_scoring_wb
        GROUP BY category_name
        """
    )


def create_stock_base_view(cur) -> None:
    mapping_cte = build_mapping_cte(cur)
    cur.execute(
        f"""
        CREATE MATERIALIZED VIEW public.mv_wb_abc_product_stock_base AS
        WITH {mapping_cte},
        mapping_dedup AS (
            SELECT
                map_key,
                max(wb_article) AS wb_article,
                max(ozon_sku) AS ozon_sku,
                max(gj_model) AS gj_model,
                max(assortment_bia) AS assortment_bia,
                max(tg) AS tg,
                max(tg_plus) AS tg_plus,
                max(cg) AS cg,
                max(season) AS season
            FROM mapping
            GROUP BY map_key
        )
        SELECT
            v.artikul_wb::text AS artikul_wb,
            max(coalesce(v.naimenovanie, v.artikul_wb::text)) AS naimenovanie,
            max(coalesce(v.category_name, 'Без категории')) AS category_name,
            max(coalesce(v.subcategory_name, v.category_name, 'Без категории')) AS subcategory_name,
            coalesce(sum(v.total_stock_qty), 0)::numeric AS total_stock_qty,
            max(v.reyting_kartochki) AS reyting_kartochki,
            max(v.reyting_po_otzyvam) AS reyting_po_otzyvam,
            max(m.wb_article) AS gj_wb_article,
            max(m.ozon_sku) AS gj_ozon_sku,
            max(m.gj_model) AS gj_model,
            max(m.assortment_bia) AS assortment_bia,
            max(m.tg) AS tg,
            max(m.tg_plus) AS tg_plus,
            max(m.cg) AS cg,
            max(m.season) AS season
        FROM public.mv_sku_card_scoring_wb v
        LEFT JOIN mapping_dedup m ON m.map_key = v.artikul_wb::text
        WHERE v.artikul_wb IS NOT NULL
        GROUP BY v.artikul_wb::text
        """
    )


def create_indexes_and_analyze(cur) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_mv_sku_card_scoring_wb_category ON public.mv_sku_card_scoring_wb(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_sku_card_scoring_wb_subcategory ON public.mv_sku_card_scoring_wb(subcategory_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_sku_card_scoring_wb_artikul ON public.mv_sku_card_scoring_wb(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_mv_sku_card_scoring_wb_stock ON public.mv_sku_card_scoring_wb(total_stock_qty)",
        "CREATE INDEX IF NOT EXISTS idx_mv_category_stock_wb_category ON public.mv_category_stock_sku_attribute_stats(category_name)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_sku ON public.mv_wb_abc_product_stock_base(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_category ON public.mv_wb_abc_product_stock_base(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_subcategory ON public.mv_wb_abc_product_stock_base(subcategory_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_product_abc_wb_category ON public.mv_product_abc_wb(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_product_abc_wb_subcategory ON public.mv_product_abc_wb(subcategory_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_product_abc_wb_artikul ON public.mv_product_abc_wb(artikul_wb)",
        "CREATE INDEX IF NOT EXISTS idx_mv_product_abc_wb_combined ON public.mv_product_abc_wb(abc_combined)",
    ]
    for index_sql in indexes:
        cur.execute(index_sql)

    for view_name in (
        "mv_sku_card_scoring_wb",
        "mv_category_stock_sku_attribute_stats",
        "mv_wb_abc_product_stock_base",
        "mv_product_abc_wb",
    ):
        cur.execute(sql.SQL("ANALYZE public.{}").format(sql.Identifier(view_name)))
        cur.execute(sql.SQL("SELECT count(*) AS rows_count FROM public.{}").format(sql.Identifier(view_name)))
        print(f"{view_name}: rows={cur.fetchone()['rows_count']:,}", flush=True)


def main() -> None:
    started = time.monotonic()
    database = os.environ.get("DASHBOARD_DB_NAME", "wb_products")
    views = (
        "mv_sku_card_scoring_wb",
        "mv_category_stock_sku_attribute_stats",
        "mv_wb_abc_product_stock_base",
        "mv_product_abc_wb",
    )
    print("=" * 80)
    print("ПЛАН: пересборка WB assortment views")
    print("=" * 80)
    print(f"База данных: {database}")
    print(f"Витрин: {len(views)}")
    print("Источник категорий: public.products / seller_category_name")
    print("Клиентский маппинг категорий не используется.")

    with app.get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "products"):
            raise RuntimeError("public.products не найден; сначала выполните импорт WB ассортимента.")

        for view_name in reversed(views):
            print(f"ПРОГРЕСС: drop {view_name}", flush=True)
            cur.execute(sql.SQL("DROP MATERIALIZED VIEW IF EXISTS public.{}").format(sql.Identifier(view_name)))

        steps = [
            ("mv_sku_card_scoring_wb", create_sku_card_view),
            ("mv_category_stock_sku_attribute_stats", create_category_stats_view),
            ("mv_wb_abc_product_stock_base", create_stock_base_view),
            ("mv_product_abc_wb", lambda c: c.execute("CREATE MATERIALIZED VIEW public.mv_product_abc_wb AS " + PRODUCT_ABC_SQL)),
        ]
        for idx, (view_name, creator) in enumerate(steps, 1):
            pct = (idx - 1) / len(steps) * 100
            print(f"ПРОГРЕСС: {idx - 1}/{len(steps)} ({pct:.1f}%) | создание {view_name} | ETA -", flush=True)
            creator(cur)

        print("ПРОГРЕСС: индексы и ANALYZE", flush=True)
        create_indexes_and_analyze(cur)
        conn.commit()

    elapsed = time.monotonic() - started
    print(f"ПРОГРЕСС: {len(views)}/{len(views)} (100.0%) | витрины WB ассортимента | готово | elapsed {elapsed:.1f}s", flush=True)
    print(f"WB assortment views rebuilt. views={len(views)}, seconds={elapsed:.1f}, errors=0", flush=True)


if __name__ == "__main__":
    main()
