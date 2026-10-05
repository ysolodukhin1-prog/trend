"""Authorized, variant-level marketplace totals for the prices matrix."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import time
import threading
from zoneinfo import ZoneInfo

LABELS = {'wb': 'WB', 'ozon': 'Ozon', 'yandex_market': 'Яндекс Маркет', 'lamoda': 'Lamoda'}

_SOURCE_CACHE = {}
_SOURCE_LOCK = threading.Lock()

def source_maps(c, start, end, client):
    key=(client,start,end)
    with _SOURCE_LOCK:
        cached=_SOURCE_CACHE.get(key)
        if cached and cached[0]>time.monotonic():
            return cached[1]
        result=read_source_maps(c,start,end,client)
        now=time.monotonic()
        for old in list(_SOURCE_CACHE):
            if _SOURCE_CACHE[old][0]<=now:del _SOURCE_CACHE[old]
        _SOURCE_CACHE[key]=(now+60,result)
        return result

def read_source_maps(c, start, end, client):
    orders, stocks = {}, {}
    c.execute("""SELECT payload->>'parent' AS parent,count(DISTINCT master_id) AS masters
      FROM assortment_master.links WHERE channel='wb' GROUP BY 1""")
    wb_masters = {str(x['parent']): x['masters'] for x in c.fetchall()}
    for channel, table, key in [('wb','wb_funnel_daily','wb_nmid'),('ozon','ozon_funnel_daily','sku')]:
        c.execute(f"""SELECT {key}::text AS key,sum(ordered_units) AS units,sum(ordered_amount_rub) AS rub,
          max(report_date) AS source_date,count(*) AS n,count(DISTINCT report_date) AS days,
          count(ordered_units) AS known_units,count(ordered_amount_rub) AS known_rub
          FROM {table} WHERE report_date BETWEEN %s AND %s GROUP BY 1""",(start,end))
        for x in c.fetchall():
            if x['n'] != x['days']:
                continue  # Ambiguous duplicate source rows must not inflate totals.
            orders[channel,x['key']] = {'units':x['units'],'rub':x['rub'],'date':str(x['source_date']),
                'partial':x['known_units']!=x['n'] or x['known_rub']!=x['n']}
    c.execute("""SELECT offer_id AS key,sum(units) AS units,
      sum(buyer_payment+coalesce(subsidy,0)) FILTER(WHERE currency IN ('RUB','RUR')) AS rub,
      max(order_date) AS source_date,count(*) AS n,count(units) AS known_units,
      count(buyer_payment) FILTER(WHERE currency IN ('RUB','RUR')) AS known_rub
      FROM yandex_fact_order_items WHERE client_key=%s AND NOT is_test
      AND order_date BETWEEN %s AND %s GROUP BY offer_id""",(client,start,end))
    for x in c.fetchall():
        orders['yandex_market',x['key']]={'units':x['units'],'rub':x['rub'],'date':str(x['source_date']),
            'partial':x['known_units']!=x['n'] or x['known_rub']!=x['n']}
    c.execute("""SELECT DISTINCT ON(wb_nmid) wb_nmid::text AS key,stock_qty AS units,snapshot_date AS source_date
      FROM wb_stock_api_current ORDER BY wb_nmid,snapshot_date DESC""")
    for x in c.fetchall():stocks['wb',x['key']]={'units':x['units'],'date':str(x['source_date']),'partial':x['units'] is None}
    c.execute("""SELECT sku::text AS key,sum(available_to_sell) AS units,max(imported_at::date) AS source_date,
      count(*) AS n,count(available_to_sell) AS known FROM vw_ozon_current_stock_by_sku GROUP BY sku""")
    for x in c.fetchall():stocks['ozon',x['key']]={'units':x['units'],'date':str(x['source_date']),'partial':x['n']!=x['known']}
    c.execute("""SELECT offer_id AS key,sum(available_for_order) AS units,max(snapshot_date) AS source_date,
      sum(source_rows) AS n,sum(rows_with_available_stock) AS known
      FROM yandex_inventory_current WHERE client_key=%s GROUP BY offer_id""",(client,))
    for x in c.fetchall():stocks['yandex_market',x['key']]={'units':x['units'],'date':str(x['source_date']),'partial':x['n']!=x['known']}
    # Catalog quantities are separate FBO/FBS inventories; prices may omit quantity.
    c.execute("""WITH latest AS (
      SELECT DISTINCT ON(account_id,fulfillment,lamoda_sku) lamoda_sku,
        nullif(payload->>'quantity','')::numeric AS units,snapshot_date
      FROM lamoda_v2_entities WHERE dataset='catalog' AND account_id IS NOT NULL
        AND coalesce(lamoda_sku,'')<>''
      ORDER BY account_id,fulfillment,lamoda_sku,synced_at DESC,snapshot_date DESC,record_key)
      SELECT lamoda_sku AS key,sum(units) AS units,max(snapshot_date) AS source_date,
        count(*) AS n,count(units) AS known FROM latest GROUP BY lamoda_sku""")
    for x in c.fetchall():stocks['lamoda',x['key']]={'units':x['units'],'date':str(x['source_date']),'partial':x['n']!=x['known']}

    return orders,stocks,wb_masters

def attach_totals(c, rows, days, client):
    end = datetime.now(ZoneInfo('Europe/Moscow')).date() - timedelta(days=1)
    start = end - timedelta(days=days-1)
    orders,stocks,wb_masters=source_maps(c,start,end,client)

    for row in rows:
        linked=defaultdict(list)
        for link in row['links']:
            if link['channel'] in LABELS:linked[link['channel']].append(link)
        values={'orders_rub':[],'orders_units':[],'stock_units':[]}
        notes={'orders':[],'stock':[]}; details={'orders':[],'stock':[]}
        for channel,links in linked.items():
            label=LABELS[channel]
            if len(links)!=1 or any(x['status']=='conflict' for x in links):
                notes['orders'].append(label+': конфликт связи');notes['stock'].append(label+': конфликт связи');continue
            link=links[0];key=str(link['data']['parent'] if channel=='wb' else link['key'])
            if channel=='wb' and wb_masters.get(key,0)!=1:
                notes['orders'].append('WB: нет разбивки по размеру');notes['stock'].append('WB: нет разбивки по размеру');continue
            for kind,mapping in [('orders',orders),('stock',stocks)]:
                metric=mapping.get((channel,key))
                if metric is None:
                    reason='нет состава заказа по SKU' if kind=='orders' and channel=='lamoda' else 'нет строк источника'
                    notes[kind].append(label+': '+reason);continue
                fields=[('orders_units','units'),('orders_rub','rub')] if kind=='orders' else [('stock_units','units')]
                for field,source in fields:
                    if metric[source] is not None:values[field].append(Decimal(str(metric[source])))
                details[kind].append(label+': по '+metric['date'])
                if metric['partial']:notes[kind].append(label+': неполные значения')
        row['totals']={k:sum(v) if v else None for k,v in values.items()}
        row['totals'].update(orders_partial=bool(notes['orders']),stock_partial=bool(notes['stock']),
            orders_detail='; '.join(details['orders']+notes['orders']),stock_detail='; '.join(details['stock']+notes['stock']))
    return {'days':days,'date_from':str(start),'date_to':str(end),
        'basis':'Заказы — по связанным площадкам за выбранный период; Яндекс — оплата покупателя плюс субсидии. Остатки — доступное количество по последним снимкам (Lamoda — каталог FBO/FBS). Даты и исключённые источники — в подсказке ячейки. Чеки 1С не добавляются к заказам маркетплейсов.'}
