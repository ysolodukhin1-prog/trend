import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


ROLLUP_SQL = {
    "mv_ozon_funnel_daily_product_rollup": """
        CREATE MATERIALIZED VIEW public.mv_ozon_funnel_daily_product_rollup AS
        SELECT
            report_date,
            category_name,
            product_artikul,
            product_name,
            count(DISTINCT coalesce(sku, barcode, seller_article, product_artikul)) AS sku_count,
            coalesce(sum(impressions_total), 0) AS impressions_total,
            coalesce(sum(impressions_search_catalog), 0) AS impressions_search_catalog,
            coalesce(sum(impressions_card), 0) AS impressions_card,
            coalesce(sum(card_visits), 0) AS card_visits,
            coalesce(sum(cart_adds), 0) AS cart_adds,
            coalesce(sum(cart_adds_search_catalog), 0) AS cart_adds_search_catalog,
            coalesce(sum(cart_adds_card), 0) AS cart_adds_card,
            coalesce(sum(sessions_total), 0) AS sessions_total,
            coalesce(sum(sessions_search_catalog), 0) AS sessions_search_catalog,
            coalesce(sum(sessions_card), 0) AS sessions_card,
            coalesce(sum(returned_units), 0) AS returned_units,
            coalesce(sum(cancelled_units), 0) AS cancelled_units,
            coalesce(sum(delivered_units), 0) AS delivered_units,
            coalesce(sum(ordered_units), 0) AS ordered_units,
            coalesce(sum(ordered_amount_rub), 0) AS ordered_amount_rub,
            0::numeric AS bought_units,
            0::numeric AS bought_amount_rub
        FROM public.mv_ozon_funnel_daily_by_article_category
        GROUP BY report_date, category_name, product_artikul, product_name
    """,
    "mv_wb_funnel_daily_product_rollup": """
        CREATE MATERIALIZED VIEW public.mv_wb_funnel_daily_product_rollup AS
        SELECT
            report_date,
            category_name,
            product_artikul,
            product_name,
            count(DISTINCT coalesce(sku, barcode, seller_article, product_artikul)) AS sku_count,
            coalesce(sum(impressions_total), 0) AS impressions_total,
            coalesce(sum(impressions_search_catalog), 0) AS impressions_search_catalog,
            coalesce(sum(card_visits), 0) AS card_visits,
            coalesce(sum(cart_adds), 0) AS cart_adds,
            coalesce(sum(ordered_units), 0) AS ordered_units,
            coalesce(sum(ordered_amount_rub), 0) AS ordered_amount_rub,
            coalesce(sum(bought_units), 0) AS bought_units,
            coalesce(sum(bought_amount_rub), 0) AS bought_amount_rub,
            coalesce(sum(cohort_bought_units), 0) AS cohort_bought_units,
            coalesce(sum(cohort_bought_amount_rub), 0) AS cohort_bought_amount_rub,
            coalesce(sum(returned_units), 0) AS returned_units,
            coalesce(sum(returned_amount_rub), 0) AS returned_amount_rub,
            coalesce(sum(favorites_adds), 0) AS favorites_adds,
            coalesce(sum(cancelled_units), 0) AS cancelled_units,
            coalesce(sum(cancelled_amount_rub), 0) AS cancelled_amount_rub,
            coalesce(sum(wb_club_ordered_units), 0) AS wb_club_ordered_units,
            coalesce(sum(wb_club_bought_units), 0) AS wb_club_bought_units,
            coalesce(sum(wb_club_cancelled_units), 0) AS wb_club_cancelled_units,
            coalesce(sum(wb_club_ordered_amount_rub), 0) AS wb_club_ordered_amount_rub,
            coalesce(sum(wb_club_bought_amount_rub), 0) AS wb_club_bought_amount_rub,
            coalesce(sum(wb_club_cancelled_amount_rub), 0) AS wb_club_cancelled_amount_rub
        FROM public.mv_wb_funnel_daily_by_article_category
        GROUP BY report_date, category_name, product_artikul, product_name
    """,
    "mv_ozon_funnel_daily_summary_rollup": """
        CREATE MATERIALIZED VIEW public.mv_ozon_funnel_daily_summary_rollup AS
        SELECT
            report_date,
            count(DISTINCT category_name) AS categories,
            coalesce(sum(sku_count), 0) AS sku_count,
            coalesce(sum(impressions_total), 0) AS impressions_total,
            coalesce(sum(impressions_search_catalog), 0) AS impressions_search_catalog,
            coalesce(sum(impressions_card), 0) AS impressions_card,
            coalesce(sum(card_visits), 0) AS card_visits,
            coalesce(sum(cart_adds), 0) AS cart_adds,
            coalesce(sum(cart_adds_search_catalog), 0) AS cart_adds_search_catalog,
            coalesce(sum(cart_adds_card), 0) AS cart_adds_card,
            coalesce(sum(sessions_total), 0) AS sessions_total,
            coalesce(sum(sessions_search_catalog), 0) AS sessions_search_catalog,
            coalesce(sum(sessions_card), 0) AS sessions_card,
            coalesce(sum(returned_units), 0) AS returned_units,
            coalesce(sum(cancelled_units), 0) AS cancelled_units,
            coalesce(sum(delivered_units), 0) AS delivered_units,
            coalesce(sum(ordered_units), 0) AS ordered_units,
            coalesce(sum(ordered_amount_rub), 0) AS ordered_amount_rub,
            0::numeric AS bought_units,
            0::numeric AS bought_amount_rub
        FROM public.mv_ozon_funnel_daily_product_rollup
        GROUP BY report_date
    """,
    "mv_wb_funnel_daily_summary_rollup": """
        CREATE MATERIALIZED VIEW public.mv_wb_funnel_daily_summary_rollup AS
        SELECT
            report_date,
            count(DISTINCT category_name) AS categories,
            coalesce(sum(sku_count), 0) AS sku_count,
            coalesce(sum(impressions_total), 0) AS impressions_total,
            coalesce(sum(impressions_search_catalog), 0) AS impressions_search_catalog,
            coalesce(sum(card_visits), 0) AS card_visits,
            coalesce(sum(cart_adds), 0) AS cart_adds,
            coalesce(sum(ordered_units), 0) AS ordered_units,
            coalesce(sum(ordered_amount_rub), 0) AS ordered_amount_rub,
            coalesce(sum(bought_units), 0) AS bought_units,
            coalesce(sum(bought_amount_rub), 0) AS bought_amount_rub,
            coalesce(sum(cohort_bought_units), 0) AS cohort_bought_units,
            coalesce(sum(cohort_bought_amount_rub), 0) AS cohort_bought_amount_rub,
            coalesce(sum(returned_units), 0) AS returned_units,
            coalesce(sum(returned_amount_rub), 0) AS returned_amount_rub,
            coalesce(sum(favorites_adds), 0) AS favorites_adds,
            coalesce(sum(cancelled_units), 0) AS cancelled_units,
            coalesce(sum(cancelled_amount_rub), 0) AS cancelled_amount_rub,
            coalesce(sum(wb_club_ordered_units), 0) AS wb_club_ordered_units,
            coalesce(sum(wb_club_bought_units), 0) AS wb_club_bought_units,
            coalesce(sum(wb_club_cancelled_units), 0) AS wb_club_cancelled_units,
            coalesce(sum(wb_club_ordered_amount_rub), 0) AS wb_club_ordered_amount_rub,
            coalesce(sum(wb_club_bought_amount_rub), 0) AS wb_club_bought_amount_rub,
            coalesce(sum(wb_club_cancelled_amount_rub), 0) AS wb_club_cancelled_amount_rub
        FROM public.mv_wb_funnel_daily_product_rollup
        GROUP BY report_date
    """,
}


INDEX_SQL = {
    "mv_ozon_funnel_daily_product_rollup": [
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_rollup_date ON public.mv_ozon_funnel_daily_product_rollup(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_rollup_category ON public.mv_ozon_funnel_daily_product_rollup(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_rollup_product ON public.mv_ozon_funnel_daily_product_rollup(product_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_rollup_date_category ON public.mv_ozon_funnel_daily_product_rollup(report_date, category_name)",
    ],
    "mv_wb_funnel_daily_product_rollup": [
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_rollup_date ON public.mv_wb_funnel_daily_product_rollup(report_date)",
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_rollup_category ON public.mv_wb_funnel_daily_product_rollup(category_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_rollup_product ON public.mv_wb_funnel_daily_product_rollup(product_name)",
        "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_rollup_date_category ON public.mv_wb_funnel_daily_product_rollup(report_date, category_name)",
    ],
    "mv_ozon_funnel_daily_summary_rollup": [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_ozon_funnel_daily_summary_rollup_date ON public.mv_ozon_funnel_daily_summary_rollup(report_date)",
    ],
    "mv_wb_funnel_daily_summary_rollup": [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_wb_funnel_daily_summary_rollup_date ON public.mv_wb_funnel_daily_summary_rollup(report_date)",
    ],
}


def rebuild_rollup(cur, view_name):
    if view_name == "mv_ozon_funnel_daily_product_rollup":
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_daily_summary_rollup")
    elif view_name == "mv_wb_funnel_daily_product_rollup":
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_wb_funnel_daily_summary_rollup")
    cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")
    cur.execute(ROLLUP_SQL[view_name])
    for query in INDEX_SQL[view_name]:
        cur.execute(query)
    cur.execute(f"ANALYZE public.{view_name}")
    cur.execute(f"SELECT count(*) AS rows_count FROM public.{view_name}")
    row = cur.fetchone()
    return int(row["rows_count"] if isinstance(row, dict) else row[0])


def main():
    parser = argparse.ArgumentParser(description="Build compact dashboard aggregate materialized views.")
    parser.add_argument("--only", choices=sorted(ROLLUP_SQL), default=None)
    args = parser.parse_args()

    targets = [args.only] if args.only else list(ROLLUP_SQL)
    started = time.monotonic()
    with app.get_conn() as conn, conn.cursor() as cur:
        for view_name in targets:
            view_started = time.monotonic()
            rows_count = rebuild_rollup(cur, view_name)
            conn.commit()
            print(f"{view_name}: rows={rows_count}, seconds={time.monotonic() - view_started:.1f}")
    print(f"Dashboard aggregates rebuilt in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
