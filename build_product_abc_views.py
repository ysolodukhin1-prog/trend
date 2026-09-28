import sys

sys.path.insert(0, r"C:\Users\Solod\Documents\New project\ozon_category_dashboard")

import app
from psycopg2 import sql


VIEWS = [
    ("mv_product_abc_wb", "mv_sku_card_scoring_wb"),
    ("mv_product_abc_ozon", "mv_sku_card_scoring_ozon"),
]


VIEW_SQL = """
WITH base AS (
    SELECT
        category_name,
        artikul_wb,
        naimenovanie,
        coalesce(total_stock_qty, 0)::numeric AS total_stock_qty,
        coalesce(zakazano_sht, 0)::numeric AS zakazano_sht,
        coalesce(zakazano_rub, 0)::numeric AS zakazano_rub,
        reyting_kartochki,
        reyting_po_otzyvam
    FROM public.{source}
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
        CASE WHEN t.total_orders_qty > 0
            THEN round(b.zakazano_sht / t.total_orders_qty * 100, 4)
            ELSE 0 END AS orders_qty_share_pct,
        CASE WHEN t.total_orders_qty > 0
            THEN round(sum(b.zakazano_sht) OVER (ORDER BY b.zakazano_sht DESC NULLS LAST, b.artikul_wb) / t.total_orders_qty * 100, 4)
            ELSE 0 END AS orders_qty_cumulative_pct,
        CASE WHEN t.total_orders_rub > 0
            THEN round(b.zakazano_rub / t.total_orders_rub * 100, 4)
            ELSE 0 END AS sales_share_pct,
        CASE WHEN t.total_orders_rub > 0
            THEN round(sum(b.zakazano_rub) OVER (ORDER BY b.zakazano_rub DESC NULLS LAST, b.artikul_wb) / t.total_orders_rub * 100, 4)
            ELSE 0 END AS sales_cumulative_pct,
        CASE WHEN t.total_stock > 0
            THEN round(b.total_stock_qty / t.total_stock * 100, 4)
            ELSE 0 END AS stock_share_pct,
        CASE WHEN t.total_stock > 0
            THEN round(sum(b.total_stock_qty) OVER (ORDER BY b.total_stock_qty DESC NULLS LAST, b.artikul_wb) / t.total_stock * 100, 4)
            ELSE 0 END AS stock_cumulative_pct,
        t.total_orders_qty,
        t.total_orders_rub,
        t.total_stock
    FROM base b
    CROSS JOIN totals t
),
classified AS (
    SELECT
        *,
        CASE
            WHEN total_orders_qty = 0 THEN 'Без ABC'
            WHEN orders_qty_cumulative_pct <= 80 THEN 'A'
            WHEN orders_qty_cumulative_pct <= 95 THEN 'B'
            ELSE 'C'
        END AS abc_orders,
        CASE
            WHEN total_orders_rub = 0 THEN 'Без ABC'
            WHEN sales_cumulative_pct <= 80 THEN 'A'
            WHEN sales_cumulative_pct <= 95 THEN 'B'
            ELSE 'C'
        END AS abc_sales,
        CASE
            WHEN total_stock = 0 THEN 'Без ABC'
            WHEN stock_cumulative_pct <= 80 THEN 'A'
            WHEN stock_cumulative_pct <= 95 THEN 'B'
            ELSE 'C'
        END AS abc_stock
    FROM ranked
)
SELECT
    category_name,
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
    CASE
        WHEN abc_orders = 'Без ABC' AND abc_sales = 'Без ABC' AND abc_stock = 'Без ABC' THEN 'Без ABC'
        ELSE concat(abc_orders, abc_sales, abc_stock)
    END AS abc_combined,
    reyting_kartochki,
    reyting_po_otzyvam
FROM classified
"""


def main():
    with app.get_conn() as conn, conn.cursor() as cur:
        for target, source in VIEWS:
            print(f"Rebuilding public.{target} from public.{source}")
            cur.execute(sql.SQL("DROP MATERIALIZED VIEW IF EXISTS public.{target}").format(target=sql.Identifier(target)))
            cur.execute(
                sql.SQL("CREATE MATERIALIZED VIEW public.{target} AS " + VIEW_SQL).format(
                    target=sql.Identifier(target),
                    source=sql.Identifier(source),
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
                    sql.SQL("CREATE INDEX {idx} ON public.{target} ({column})").format(
                        idx=sql.Identifier(f"idx_{target}_{column}"),
                        target=sql.Identifier(target),
                        column=sql.Identifier(column),
                    )
                )
            cur.execute(sql.SQL("ANALYZE public.{target}").format(target=sql.Identifier(target)))
            cur.execute(sql.SQL("SELECT count(*) AS rows_count FROM public.{target}").format(target=sql.Identifier(target)))
            print(f"Rows: {cur.fetchone()['rows_count']}")
        conn.commit()


if __name__ == "__main__":
    main()
