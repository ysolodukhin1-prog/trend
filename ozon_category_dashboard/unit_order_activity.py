"""Read order units independently of settlement units; keep source grain explicit."""
def load(cur,client,start,end):
    rows=[]
    cur.execute('''SELECT sku::text sku, max(brand) brand,max(category_level_3) category,
       CASE WHEN count(*)=count(ordered_units) AND count(*)=count(DISTINCT (report_date,sku)) THEN sum(ordered_units) END orders,
       count(DISTINCT report_date) observed_days
       FROM ozon_funnel_daily WHERE report_date BETWEEN %s AND %s GROUP BY sku''',(start,end))
    rows.extend(dict(r,marketplace='ozon',cabinet=client,buyout=None,buyout_denominator=None,grain='sku',basis='Воронка Ozon: заказанные единицы по дате заказа') for r in cur.fetchall())
    cur.execute('''SELECT wb_nmid::text sku,max(brand) brand,max(category_name) category,
       CASE WHEN count(*)=count(ordered_units) AND count(*)=count(DISTINCT report_date) THEN sum(ordered_units) END orders,
       CASE WHEN count(*)=count(bought_units) AND count(*)=count(cancelled_units) THEN sum(bought_units) END buyout,
       CASE WHEN count(*)=count(bought_units) AND count(*)=count(cancelled_units) THEN sum(bought_units+cancelled_units) END buyout_denominator,
       count(DISTINCT report_date) observed_days
       FROM wb_funnel_daily WHERE report_date BETWEEN %s AND %s GROUP BY wb_nmid''',(start,end))
    rows.extend(dict(r,marketplace='wb',cabinet=client,grain='nmID_all_sizes',basis='Воронка WB: все размеры карточки; выкуп среди завершённых заказов') for r in cur.fetchall())
    cur.execute('''SELECT campaign_id cabinet,offer_id sku,
       CASE WHEN count(*)=count(units) THEN sum(units) END orders,
       CASE WHEN count(*)=count(units) THEN sum(CASE WHEN status='DELIVERED' THEN units ELSE 0 END) END buyout,
       CASE WHEN count(*)=count(units) THEN sum(units) END buyout_denominator,
       count(DISTINCT order_date) observed_days
       FROM yandex_fact_order_items WHERE client_key=%s AND order_date BETWEEN %s AND %s AND NOT is_test
       GROUP BY campaign_id,offer_id''',(client,start,end))
    rows.extend(dict(r,marketplace='yandex',grain='sku',basis='ЯМ: доля доставленных единиц среди заказанных; текущий статус, после выкупа возвраты отдельно',brand=None,category=None) for r in cur.fetchall())
    return rows
