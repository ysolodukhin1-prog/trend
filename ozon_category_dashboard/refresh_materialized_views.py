import ast
import re
import sys

import psycopg2


text = sys.stdin.read()
config = ast.literal_eval(re.search(r"DB_CONFIG\s*=\s*(\{.*?\})", text, re.S).group(1))

views = [
    "mv_category_stock_sku_attribute_stats",
    "mv_ozon_category_stock_sku_attribute_stats",
    "mv_sku_card_scoring_wb",
    "mv_sku_card_scoring_ozon",
    "mv_product_abc_wb",
    "mv_product_abc_ozon",
    "mv_ozon_adv_daily_by_article_category",
    "mv_ozon_media_adv_daily",
    "mv_wb_funnel_daily_by_article_category",
    "mv_wb_funnel_filter_options",
    "mv_wb_funnel_product_options",
    "mv_wb_abc_product_orders_base",
    "mv_wb_abc_product_stock_base",
]

with psycopg2.connect(**config) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        for view in views:
            print(f"Refreshing {view}...")
            cur.execute(f"REFRESH MATERIALIZED VIEW public.{view}")
            cur.execute(f"ANALYZE public.{view}")
        print("Done.")
