from pathlib import Path
import sys
import time

from psycopg2 import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


VIEWS = [
    "mv_sku_card_scoring_wb",
    "mv_category_stock_sku_attribute_stats",
    "mv_wb_funnel_daily_by_article_category",
    "mv_wb_funnel_filter_options",
    "mv_wb_funnel_product_options",
    "mv_wb_abc_product_orders_base",
    "mv_wb_abc_product_stock_base",
    "mv_product_abc_wb",
]


def refresh_view(cur, view_name):
    started = time.monotonic()
    print(f"Refreshing {view_name}...")
    cur.execute(sql.SQL("REFRESH MATERIALIZED VIEW public.{view}").format(view=sql.Identifier(view_name)))
    cur.execute(sql.SQL("ANALYZE public.{view}").format(view=sql.Identifier(view_name)))
    cur.execute(sql.SQL("SELECT count(*) AS rows_count FROM public.{view}").format(view=sql.Identifier(view_name)))
    rows_count = cur.fetchone()["rows_count"]
    elapsed = time.monotonic() - started
    print(f"Done {view_name}: rows={rows_count:,}, seconds={elapsed:.1f}")


def main():
    started = time.monotonic()
    with app.get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for view_name in VIEWS:
                refresh_view(cur, view_name)
    elapsed = time.monotonic() - started
    print(f"WB dashboard views refreshed. views={len(VIEWS)}, seconds={elapsed:.1f}, errors=0")


if __name__ == "__main__":
    main()
