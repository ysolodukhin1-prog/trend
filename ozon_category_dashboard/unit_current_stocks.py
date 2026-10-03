"""Latest available-for-sale inventory; unknown quantities never become zero."""
def attach(cur, client, rows, marketplace=''):
    wb={}
    if marketplace in ('', 'wb'):
      cur.execute("""SELECT DISTINCT ON(wb_nmid) wb_nmid,stock_qty,price_min_rub,price_max_rub,snapshot_date
      FROM wb_stock_api_current ORDER BY wb_nmid,snapshot_date DESC,imported_at DESC""")
      wb={str(r['wb_nmid']):dict(r) for r in cur.fetchall()}
    for row in rows:
        if row['marketplace']!='wb' or str(row['cabinet'])!=client: continue
        source=wb.get(str(row['sku']))
        if not source: continue
        row['current_stock']={'quantity':source['stock_qty'],'date':str(source['snapshot_date']),
          'sku':str(row['sku']),'grain':'marketplace_sku',
          'note':'Остаток всего SKU WB по последнему загруженному отчёту (stockCount), не отдельного размера/штрихкода. Товары в пути к покупателю и обратно не включены. Не онлайн-остаток.'}
        row['current_price']={'min':source['price_min_rub'],'max':source['price_max_rub'],'date':str(source['snapshot_date']),
          'sku':str(row['sku']),
          'note':'Текущая цена/диапазон SKU WB из metrics.currentPrice последнего загруженного отчёта остатков. Не средняя цена продаж периода и не персональная цена покупателя.'}
    # A completed report, not its download campaign alone, establishes freshness.
    stocks={}
    if marketplace in ('', 'yandex'):
      cur.execute('''WITH latest AS (
      SELECT DISTINCT ON (campaign_id) campaign_id,job_key,date_to
      FROM yandex_analytics_jobs WHERE client_key=%s AND source_key='stocks' AND state='completed'
      ORDER BY campaign_id,date_to DESC,finished_at DESC,job_key DESC)
      SELECT f.campaign_id,f.offer_id,l.date_to,
        CASE WHEN count(*)=count(f.available_for_order) THEN sum(f.available_for_order) END quantity
      FROM latest l JOIN yandex_fact_stocks f USING(job_key)
      WHERE f.client_key=%s GROUP BY f.campaign_id,f.offer_id,l.date_to''', (client,client))
      stocks={(str(r['campaign_id']),r['offer_id']):r for r in cur.fetchall()}
    for row in rows:
        if row['marketplace']!='yandex': continue
        stock=stocks.get((str(row['cabinet']),row['sku']))
        row['current_stock'] = None if not stock else {
            'quantity':stock['quantity'],'date':str(stock['date_to']),
            'note':'Доступно к заказу по последнему загруженному отчёту ЯМ. Дата снимка указана под остатком; не онлайн-остаток.'}
