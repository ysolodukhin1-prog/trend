"""WB calendar operations; order cohorts remain independently addressable."""
from psycopg2 import sql

SALES_CTE = """
sales_latest AS (
 SELECT DISTINCT ON (payload->>'saleID') payload
 FROM public.wb_api_entities
 WHERE source_key='statistics.sales'
 ORDER BY payload->>'saleID', payload->>'lastChangeDate' DESC, captured_at DESC
), sales AS (
 SELECT (payload->>'date')::timestamp::date AS report_date,
        (payload->>'nmId')::bigint::text AS sku,
        max(payload->>'supplierArticle') AS seller_article,
        max(payload->>'subject') AS category_name,
        max(payload->>'brand') AS brand,
        sum(CASE WHEN left(payload->>'saleID',1)='R' THEN -1 ELSE 1 END)::numeric AS bought_units,
        sum(CASE WHEN left(payload->>'saleID',1)='R'
                 THEN -abs((payload->>'priceWithDisc')::numeric)
                 ELSE (payload->>'priceWithDisc')::numeric END) AS bought_amount_rub,
        count(*) FILTER (WHERE left(payload->>'saleID',1)='R')::numeric AS returned_units,
        coalesce(sum(abs((payload->>'priceWithDisc')::numeric)) FILTER (WHERE left(payload->>'saleID',1)='R'),0) AS returned_amount_rub
 FROM sales_latest GROUP BY 1,2
)
"""

def create_calendar_funnel(cur, cohort_select):
    """Full join retains sales of orders outside the selected/order-history period."""
    cur.execute('CREATE TEMP VIEW wb_cohort_shape AS '+cohort_select)
    cur.execute('SELECT * FROM wb_cohort_shape LIMIT 0')
    columns=[d.name for d in cur.description]
    cur.execute('DROP VIEW wb_cohort_shape')
    dimensions={
        'report_date':'coalesce(c.report_date,s.report_date)',
        'sku':'coalesce(c.sku,s.sku)',
        'seller_article':"coalesce(c.seller_article,d.seller_article,s.seller_article,s.sku)",
        'product_artikul':"coalesce(c.product_artikul,d.product_artikul,s.seller_article,s.sku)",
        'product_name':"coalesce(c.product_name,d.product_name,s.seller_article,s.sku)",
        'category_name':"coalesce(c.category_name,d.category_name,s.category_name,'Без категории')",
        'subcategory_name':"coalesce(c.subcategory_name,d.subcategory_name,s.category_name,'Без категории')",
        'brand':'coalesce(c.brand,d.brand,s.brand)',
    }
    zero_fields={'ordered_amount_rub','ordered_units','impressions_total','impressions_search_catalog','card_visits','cart_adds','favorites_adds','cancelled_units','cancelled_amount_rub'}
    fields=[]
    for col in columns:
        if col in dimensions:expr=sql.SQL(dimensions[col])
        elif col in {'bought_units','bought_amount_rub'}:expr=sql.SQL('coalesce(s.{},0)').format(sql.Identifier(col))
        elif col in zero_fields or col.startswith('wb_club_'):expr=sql.SQL('coalesce(c.{},0)').format(sql.Identifier(col))
        else:expr=sql.SQL('c.{}').format(sql.Identifier(col))
        fields.append(sql.SQL('{} AS {}').format(expr,sql.Identifier(col)))
    fields.extend(sql.SQL(x) for x in [
        'coalesce(c.bought_units,0) AS cohort_bought_units',
        'coalesce(c.bought_amount_rub,0) AS cohort_bought_amount_rub',
        'coalesce(s.returned_units,0) AS returned_units',
        'coalesce(s.returned_amount_rub,0) AS returned_amount_rub'])
    cur.execute(sql.SQL('''CREATE MATERIALIZED VIEW public.mv_wb_funnel_daily_by_article_category AS
        WITH cohort AS ({cohort}), {sales}, dimensions AS (
          SELECT DISTINCT ON (sku) * FROM cohort ORDER BY sku,report_date DESC
        ) SELECT {fields} FROM cohort c FULL OUTER JOIN sales s
          ON s.report_date=c.report_date AND s.sku=c.sku
        LEFT JOIN dimensions d ON d.sku=coalesce(c.sku,s.sku)
    ''').format(cohort=sql.SQL(cohort_select),sales=sql.SQL(SALES_CTE),fields=sql.SQL(',').join(fields)))
