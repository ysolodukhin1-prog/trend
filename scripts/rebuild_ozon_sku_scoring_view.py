from __future__ import annotations

import sys
from pathlib import Path

from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

import app  # noqa: E402
from build_product_abc_views import VIEW_SQL  # noqa: E402


SKU_SCORING_SQL = """
WITH op_by_sku AS (
    SELECT
        sku,
        max(artikul) FILTER (WHERE nullif(trim(artikul), '') IS NOT NULL) AS artikul,
        max(kontent_reyting) FILTER (WHERE nullif(trim(kontent_reyting), '') IS NOT NULL) AS kontent_reyting,
        max(reyting) FILTER (WHERE nullif(trim(reyting), '') IS NOT NULL) AS reyting
    FROM public.ozon_products
    WHERE nullif(trim(sku), '') IS NOT NULL
    GROUP BY sku
),
product_by_article AS (
    SELECT DISTINCT ON (artikul)
        artikul,
        product_id,
        category_id,
        category_name,
        nazvanie_tovara,
        annotatsiya,
        heshtegi,
        rich_kontent_json,
        sku,
        ves_v_upakovke_g,
        vysota_upakovki_mm,
        dlina_upakovki_mm,
        kolichestvo_zavodskih_upakovok,
        kolichestvo_tovara_v_uei,
        minimalnoe_kolichestvo_optom,
        nds_pct,
        obedinit_v_pohozhie_tovary,
        rassrochka,
        ssylka_na_glavnoe_foto,
        ssylki_na_dopolnitelnye_foto,
        strana_izgotovitel,
        tip,
        uskorennyy_sbor_otzyvov,
        tsena_do_skidki_rub,
        tsena_rub,
        shirina_upakovki_mm,
        shtrihkod_seriynyy_nomer_ean
    FROM public.ozon_cat_products
    WHERE nullif(trim(artikul), '') IS NOT NULL
    ORDER BY artikul, updated_at DESC NULLS LAST, imported_at DESC NULLS LAST, product_id DESC
),
product_by_sku AS (
    SELECT DISTINCT ON (sku)
        sku,
        artikul,
        product_id,
        category_id,
        category_name,
        nazvanie_tovara,
        annotatsiya,
        heshtegi,
        rich_kontent_json,
        ves_v_upakovke_g,
        vysota_upakovki_mm,
        dlina_upakovki_mm,
        kolichestvo_zavodskih_upakovok,
        kolichestvo_tovara_v_uei,
        minimalnoe_kolichestvo_optom,
        nds_pct,
        obedinit_v_pohozhie_tovary,
        rassrochka,
        ssylka_na_glavnoe_foto,
        ssylki_na_dopolnitelnye_foto,
        strana_izgotovitel,
        tip,
        uskorennyy_sbor_otzyvov,
        tsena_do_skidki_rub,
        tsena_rub,
        shirina_upakovki_mm,
        shtrihkod_seriynyy_nomer_ean
    FROM public.ozon_cat_products
    WHERE nullif(trim(sku), '') IS NOT NULL
    ORDER BY sku, updated_at DESC NULLS LAST, imported_at DESC NULLS LAST, product_id DESC
),
sku_universe AS (
    SELECT ozon_sku::text AS sku
    FROM public.vw_ozon_sku_sales_90d
    WHERE nullif(trim(ozon_sku::text), '') IS NOT NULL
    UNION
    SELECT artikul_wb::text AS sku
    FROM public.mv_ozon_abc_product_orders_base
    WHERE nullif(trim(artikul_wb::text), '') IS NOT NULL
    UNION
    SELECT sku::text AS sku
    FROM public.ozon_cat_products
    WHERE nullif(trim(sku::text), '') IS NOT NULL
),
matched AS (
    SELECT
        k.sku AS universe_sku,
        s.*,
        op.kontent_reyting,
        op.reyting,
        COALESCE(pa.product_id, po.product_id, ps.product_id) AS product_id,
        COALESCE(pa.category_id, po.category_id, ps.category_id) AS category_id,
        COALESCE(pa.category_name, po.category_name, ps.category_name) AS card_category_name,
        COALESCE(pa.nazvanie_tovara, po.nazvanie_tovara, ps.nazvanie_tovara) AS card_name,
        COALESCE(pa.annotatsiya, po.annotatsiya, ps.annotatsiya) AS annotatsiya,
        COALESCE(pa.heshtegi, po.heshtegi, ps.heshtegi) AS heshtegi,
        COALESCE(pa.rich_kontent_json, po.rich_kontent_json, ps.rich_kontent_json) AS rich_kontent_json,
        COALESCE(pa.sku, po.sku, ps.sku) AS card_sku,
        COALESCE(pa.ves_v_upakovke_g, po.ves_v_upakovke_g, ps.ves_v_upakovke_g) AS ves_v_upakovke_g,
        COALESCE(pa.vysota_upakovki_mm, po.vysota_upakovki_mm, ps.vysota_upakovki_mm) AS vysota_upakovki_mm,
        COALESCE(pa.dlina_upakovki_mm, po.dlina_upakovki_mm, ps.dlina_upakovki_mm) AS dlina_upakovki_mm,
        COALESCE(pa.kolichestvo_zavodskih_upakovok, po.kolichestvo_zavodskih_upakovok, ps.kolichestvo_zavodskih_upakovok) AS kolichestvo_zavodskih_upakovok,
        COALESCE(pa.kolichestvo_tovara_v_uei, po.kolichestvo_tovara_v_uei, ps.kolichestvo_tovara_v_uei) AS kolichestvo_tovara_v_uei,
        COALESCE(pa.minimalnoe_kolichestvo_optom, po.minimalnoe_kolichestvo_optom, ps.minimalnoe_kolichestvo_optom) AS minimalnoe_kolichestvo_optom,
        COALESCE(pa.nds_pct, po.nds_pct, ps.nds_pct) AS nds_pct,
        COALESCE(pa.obedinit_v_pohozhie_tovary, po.obedinit_v_pohozhie_tovary, ps.obedinit_v_pohozhie_tovary) AS obedinit_v_pohozhie_tovary,
        COALESCE(pa.rassrochka, po.rassrochka, ps.rassrochka) AS rassrochka,
        COALESCE(pa.ssylka_na_glavnoe_foto, po.ssylka_na_glavnoe_foto, ps.ssylka_na_glavnoe_foto) AS ssylka_na_glavnoe_foto,
        COALESCE(pa.ssylki_na_dopolnitelnye_foto, po.ssylki_na_dopolnitelnye_foto, ps.ssylki_na_dopolnitelnye_foto) AS ssylki_na_dopolnitelnye_foto,
        COALESCE(pa.strana_izgotovitel, po.strana_izgotovitel, ps.strana_izgotovitel) AS strana_izgotovitel,
        COALESCE(pa.tip, po.tip, ps.tip) AS card_tip,
        COALESCE(pa.uskorennyy_sbor_otzyvov, po.uskorennyy_sbor_otzyvov, ps.uskorennyy_sbor_otzyvov) AS uskorennyy_sbor_otzyvov,
        COALESCE(pa.tsena_do_skidki_rub, po.tsena_do_skidki_rub, ps.tsena_do_skidki_rub) AS tsena_do_skidki_rub,
        COALESCE(pa.tsena_rub, po.tsena_rub, ps.tsena_rub) AS tsena_rub,
        COALESCE(pa.shirina_upakovki_mm, po.shirina_upakovki_mm, ps.shirina_upakovki_mm) AS shirina_upakovki_mm,
        COALESCE(pa.shtrihkod_seriynyy_nomer_ean, po.shtrihkod_seriynyy_nomer_ean, ps.shtrihkod_seriynyy_nomer_ean) AS shtrihkod_seriynyy_nomer_ean
    FROM sku_universe k
    LEFT JOIN public.vw_ozon_sku_sales_90d s ON s.ozon_sku = k.sku
    LEFT JOIN op_by_sku op ON op.sku = s.ozon_sku
    LEFT JOIN product_by_article pa ON pa.artikul = s.artikul_prodavtsa
    LEFT JOIN product_by_article po ON po.artikul = op.artikul
    LEFT JOIN product_by_sku ps ON ps.sku = k.sku
),
category_totals AS (
    SELECT
        category_id,
        count(DISTINCT attribute_id)::bigint AS category_attrs_total
    FROM public.ozon_cat_category_attributes
    GROUP BY category_id
),
category_filled AS (
    SELECT
        p.product_id,
        count(DISTINCT pa.attribute_id)::bigint AS category_attrs_filled
    FROM public.ozon_cat_products p
    JOIN public.ozon_cat_product_attributes pa
        ON pa.product_id = p.product_id
       AND nullif(trim(pa.value_text), '') IS NOT NULL
    JOIN public.ozon_cat_category_attributes ca
        ON ca.category_id = p.category_id
       AND ca.attribute_id = pa.attribute_id
    GROUP BY p.product_id
),
common_totals AS (
    SELECT count(*)::bigint AS common_attrs_total
    FROM public.ozon_cat_common_attributes
)
SELECT
    COALESCE(m.ozon_category, m.card_category_name, 'Без категории') AS category_name,
    COALESCE(m.ozon_sku, m.universe_sku) AS artikul_wb,
    COALESCE(m.naimenovanie, m.card_name) AS naimenovanie,
    length(COALESCE(m.naimenovanie, m.card_name)) AS naimenovanie_len,
    length(NULLIF(m.annotatsiya, '')) AS opisanie_len,
    ct.common_attrs_total,
    (
        (CASE WHEN nullif(trim(COALESCE(m.heshtegi, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.rich_kontent_json, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.card_sku, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.annotatsiya, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.artikul_prodavtsa, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.ves_v_upakovke_g, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.vysota_upakovki_mm, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.dlina_upakovki_mm, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.kolichestvo_zavodskih_upakovok, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.kolichestvo_tovara_v_uei, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.minimalnoe_kolichestvo_optom, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.nds_pct, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.card_name, m.naimenovanie, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.obedinit_v_pohozhie_tovary, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.rassrochka, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.ssylka_na_glavnoe_foto, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.ssylki_na_dopolnitelnye_foto, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.strana_izgotovitel, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.card_tip, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.uskorennyy_sbor_otzyvov, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.tsena_do_skidki_rub, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.tsena_rub, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.shirina_upakovki_mm, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN nullif(trim(COALESCE(m.shtrihkod_seriynyy_nomer_ean, '')), '') IS NOT NULL THEN 1 ELSE 0 END)
    )::bigint AS common_attrs_filled,
    COALESCE(cat.category_attrs_total, 0::bigint) AS category_attrs_total,
    COALESCE(cf.category_attrs_filled, 0::bigint) AS category_attrs_filled,
    (
        (CASE WHEN nullif(trim(COALESCE(m.ssylka_na_glavnoe_foto, '')), '') IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE
            WHEN nullif(trim(COALESCE(m.ssylki_na_dopolnitelnye_foto, '')), '') IS NULL THEN 0
            ELSE cardinality(array_remove(regexp_split_to_array(trim(m.ssylki_na_dopolnitelnye_foto), E'[\\s;,]+'), ''))
        END)
    )::integer AS foto_count,
    COALESCE(m.ostatok_na_konets, 0::numeric) AS total_stock_qty,
    COALESCE(m.zakazano_sht, 0::numeric) AS zakazano_sht,
    COALESCE(m.zakazano_rub, 0::numeric) AS zakazano_rub,
    CASE
        WHEN replace(COALESCE(m.kontent_reyting, ''), ',', '.') ~ '^\\s*[0-9]+(\\.[0-9]+)?\\s*$'
        THEN replace(trim(m.kontent_reyting), ',', '.')::numeric
        ELSE NULL::numeric
    END AS reyting_kartochki,
    CASE
        WHEN replace(COALESCE(m.reyting, ''), ',', '.') ~ '^\\s*[0-9]+(\\.[0-9]+)?\\s*$'
        THEN replace(trim(m.reyting), ',', '.')::numeric
        ELSE NULL::numeric
    END AS reyting_po_otzyvam
FROM matched m
CROSS JOIN common_totals ct
LEFT JOIN category_totals cat ON cat.category_id = m.category_id
LEFT JOIN category_filled cf ON cf.product_id = m.product_id
"""


def rebuild_product_abc_ozon(cur) -> None:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_product_abc_ozon")
    cur.execute(
        sql.SQL("CREATE MATERIALIZED VIEW public.mv_product_abc_ozon AS " + VIEW_SQL).format(
            source=sql.Identifier("mv_sku_card_scoring_ozon"),
        )
    )
    for column in [
        "category_name",
        "artikul_wb",
        "zakazano_sht",
        "zakazano_rub",
        "total_stock_qty",
        "abc_orders",
        "abc_sales",
        "abc_stock",
        "abc_combined",
    ]:
        cur.execute(
            sql.SQL("CREATE INDEX {idx} ON public.mv_product_abc_ozon ({column})").format(
                idx=sql.Identifier(f"idx_mv_product_abc_ozon_{column}"),
                column=sql.Identifier(column),
            )
        )
    cur.execute("ANALYZE public.mv_product_abc_ozon")


def rebuild_sku_scoring_ozon(cur) -> None:
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_product_abc_ozon")
    cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_sku_card_scoring_ozon")
    cur.execute("CREATE MATERIALIZED VIEW public.mv_sku_card_scoring_ozon AS " + SKU_SCORING_SQL)
    for column in ["category_name", "artikul_wb", "total_stock_qty", "zakazano_rub"]:
        cur.execute(
            sql.SQL("CREATE INDEX {idx} ON public.mv_sku_card_scoring_ozon ({column})").format(
                idx=sql.Identifier(f"idx_mv_sku_card_ozon_{column}"),
                column=sql.Identifier(column),
            )
        )
    cur.execute("ANALYZE public.mv_sku_card_scoring_ozon")
    rebuild_product_abc_ozon(cur)


def print_checks(cur) -> None:
    checks = [
        (
            "sku_scoring",
            """
            SELECT
                count(*) AS rows_count,
                count(*) FILTER (WHERE common_attrs_filled > 0) AS common_filled_rows,
                count(*) FILTER (WHERE category_attrs_filled > 0) AS category_filled_rows,
                count(*) FILTER (WHERE foto_count > 0) AS photo_rows,
                max(common_attrs_total) AS common_attrs_total,
                round(avg(common_attrs_filled), 2) AS avg_common_filled,
                round(avg(category_attrs_filled), 2) AS avg_category_filled,
                round(avg(foto_count), 2) AS avg_photo_count
            FROM public.mv_sku_card_scoring_ozon
            """,
        ),
        (
            "top_stock",
            """
            SELECT
                category_name,
                artikul_wb,
                common_attrs_total,
                common_attrs_filled,
                category_attrs_total,
                category_attrs_filled,
                foto_count,
                total_stock_qty
            FROM public.mv_sku_card_scoring_ozon
            ORDER BY total_stock_qty DESC NULLS LAST
            LIMIT 5
            """,
        ),
    ]
    for title, query in checks:
        print(f"\n[{title}]")
        cur.execute(query)
        for row in cur.fetchall():
            print(dict(row))


def main() -> None:
    with app.get_conn() as conn, conn.cursor() as cur:
        print("Rebuilding public.mv_sku_card_scoring_ozon...")
        rebuild_sku_scoring_ozon(cur)
        print_checks(cur)
        conn.commit()
    print("Done.")


if __name__ == "__main__":
    main()
