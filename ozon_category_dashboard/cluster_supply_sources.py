"""Read-only source adapter. Source coverage and geography are never invented.

The stock slice is deliberately separate from buyer-region demand: a warehouse
does not establish which region's demand it can fulfil.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
from km_trade_finance import connect_km

MARKETS = {'wb', 'ozon', 'yandex_market'}


def jsonable(value):
    if isinstance(value, (date,)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, dict): return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [jsonable(v) for v in value]
    return value


def table_exists(cur, name):
    cur.execute('SELECT to_regclass(%s) AS relation', ('public.' + name,))
    return bool(cur.fetchone()['relation'])


def validate_scope(client, market, start, end, sku=''):
    if not client or market not in MARKETS: raise ValueError('Укажите клиента и площадку')
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if start > end or (end-start).days > 365 or end > date.today():
        raise ValueError('Выберите прошедший период до 366 дней')
    if len(str(sku)) > 150: raise ValueError('Слишком длинный SKU')
    return start, end


def aggregate_observations(rows, start, end):
    """Observed events only. No-event days require import evidence, not filling.

    A calendar-denominator rate is explicitly a lower-bound observation and is
    not a forecast-ready velocity. Missing buyer geography stays its own group.
    """
    grouped = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        key = (str(row['sku']), str(row.get('size') or ''),
               row.get('buyer_region') or None, row.get('scheme') or None)
        grouped[key].append(row)
    result = []
    for (sku, size, region, scheme), group in sorted(grouped.items(), key=lambda p: str(p[0])):
        days = sorted({str(r['day']) for r in group})
        units = sum(Decimal(str(r['orders'])) for r in group)
        daily = defaultdict(Decimal)
        for r in group: daily[str(r['day'])] += Decimal(str(r['orders']))
        rates = {}
        for window in (7, 30, 90):
            begin = max(start, end-timedelta(days=window-1))
            selected = [r for r in group if begin <= date.fromisoformat(str(r['day'])) <= end]
            count = sum(Decimal(str(r['orders'])) for r in selected)
            rates[str(window)] = {'orders': float(count) if selected else None,
                'calendar_days': (end-begin).days+1,
                'orders_per_calendar_day': float(count/((end-begin).days+1)) if selected else None,
                'kind': 'observed_lower_bound', 'coverage': 'unverified'}
        result.append({'key': '|'.join((sku, size, region or '', scheme or '')),
            'sku': sku, 'size': size, 'product_name': next((r.get('product_name') for r in group if r.get('product_name')), sku),
            'buyer_region': region, 'cluster': None, 'scheme': scheme,
            'orders': float(units), 'buyouts': None, 'returns': None,
            'observed_event_days': len(days), 'first_event_date': days[0], 'last_event_date': days[-1],
            'daily': [{'date': day, 'orders': float(qty)} for day, qty in sorted(daily.items())],
            'velocity': rates, 'status': 'partial',
            'missing': ['Полнота импорта по дням', 'Выкупы и зрелость когорты', 'Подтверждённый маппинг кластера'],
            'forecast_qty': None, 'plan_qty': None, 'result_before_tax': None,
            'delta_center': None})
    return result


def read_yandex_stock(cur, client, end, sku=''):
    if not all(table_exists(cur, name) for name in ('yandex_analytics_jobs', 'yandex_fact_stocks')):
        return {'status': 'blocked', 'reason': 'Нет отчёта складских остатков Яндекс Маркета', 'rows': []}
    args=[client,end,client]
    extra=''
    if sku: extra=' AND f.offer_id=%s'; args.append(sku)
    cur.execute('''WITH latest AS (
        SELECT DISTINCT ON (business_id,campaign_id) business_id,campaign_id,job_key,date_to
        FROM yandex_analytics_jobs WHERE client_key=%s AND source_key='stocks'
          AND state='completed' AND date_to<=%s
        ORDER BY business_id,campaign_id,date_to DESC,finished_at DESC,job_key DESC)
        SELECT f.offer_id AS sku,f.business_id,f.campaign_id,f.warehouse AS warehouse_name,
          NULL::text AS cluster_name,f.available_for_order AS stock_available_qty,
          f.reserved AS stock_reserved_qty,NULL::numeric AS stock_total_qty,
          f.snapshot_date,f.job_key,f.sheet,f.row_no,'yandex_stocks_report' AS source_type
        FROM latest l JOIN yandex_fact_stocks f USING(job_key)
        WHERE f.client_key=%s''' + extra + ' ORDER BY f.campaign_id,f.offer_id,f.sheet,f.row_no LIMIT 25001', args)
    rows=[dict(r) for r in cur.fetchall()]
    if not rows: return {'status': 'blocked', 'reason': 'Нет завершённого снимка выбранного периода', 'rows': []}
    truncated=len(rows)>25000; rows=rows[:25000]
    dates=sorted({r['snapshot_date'] for r in rows})
    return jsonable({'status':'partial','as_of':max(dates),'oldest_as_of':min(dates),
        'stale':(date.today()-min(dates)).days>2,'truncated':truncated,
        'reason':'Срез по завершённому отчёту каждого магазина; строки листов не суммированы, требуется проверка зерна и кластера',
        'missing_warehouse_rows':sum(not r['warehouse_name'] for r in rows),'rows':rows})


def read_stock(cur, market, end, sku='', client=''):
    if market=='yandex_market': return read_yandex_stock(cur,client,end,sku)
    if not table_exists(cur, 'inventory_history_daily'):
        return {'status': 'blocked', 'reason': 'Нет источника остатков', 'rows': []}
    args = [market, end]
    sku_filter = ''
    if sku: sku_filter = ' AND sku=%s'; args.append(sku)
    cur.execute('''SELECT max(snapshot_date) AS latest,
        max(snapshot_date) FILTER(WHERE warehouse_name<>'') AS warehouse_latest
        FROM inventory_history_daily WHERE marketplace=%s AND snapshot_date<=%s''' + sku_filter, args)
    dates = dict(cur.fetchone())
    if not dates['latest']:
        return {'status': 'blocked', 'reason': 'Нет снимка на выбранную дату', 'rows': []}
    # Read the latest actual snapshot, never carry an older warehouse split into it.
    args = [market, dates['latest']]
    if sku: args.append(sku)
    cur.execute('''SELECT sku,seller_article,product_name,warehouse_name,cluster_name,
        stock_available_qty,stock_reserved_qty,stock_total_qty,source_type,snapshot_date
        FROM inventory_history_daily WHERE marketplace=%s AND snapshot_date=%s''' + sku_filter +
        ' ORDER BY sku,warehouse_name,cluster_name LIMIT 25001', args)
    rows = [dict(r) for r in cur.fetchall()]
    truncated = len(rows)>25000
    rows = rows[:25000]
    missing_warehouse = sum(not r['warehouse_name'] for r in rows)
    stale = (date.today()-dates['latest']).days > 2
    return jsonable({'status': 'partial' if stale or missing_warehouse or truncated else 'observed',
        'as_of': dates['latest'], 'warehouse_as_of': dates['warehouse_latest'],
        'stale': stale, 'missing_warehouse_rows': missing_warehouse, 'truncated': truncated,
        'reason': 'Требуется проверка полноты снимка и соответствия склада кластеру', 'rows': rows})


def read_demand(cur, client, market, start, end, sku=''):
    if market == 'ozon':
        # Cached delivered routes are not a daily order or buyout time series.
        routes = []
        if table_exists(cur, 'unit_ozon_cluster_mix_cache'):
            cur.execute('''SELECT payload,fetched_at FROM unit_ozon_cluster_mix_cache
                WHERE client=%s AND date_from=%s AND date_to=%s''', (client, start, end))
            saved = cur.fetchone()
            if saved:
                for scheme, info in (saved['payload'].get('schemes') or {}).items():
                    for route in info.get('routes') or []:
                        if sku and str(route.get('sku')) != sku: continue
                        routes.append({**route, 'scheme': scheme, 'source_status': info.get('status'),
                            'source_as_of': str(saved['fetched_at']), 'metric': 'delivered_units'})
        return [], {'status': 'partial' if routes else 'blocked', 'source': 'unit_ozon_cluster_mix_cache',
            'reason': 'Нужны дневные заказы по региону и зрелые выкупы; доставленные отправления показаны отдельно',
            'delivered_routes': routes}
    required = ['wb_api_entities'] if market == 'wb' else [
        'yandex_fact_order_items', 'yandex_fact_orders', 'yandex_market_entities']
    if not all(table_exists(cur, name) for name in required):
        return [], {'status': 'blocked', 'reason': 'Не все таблицы источника доступны', 'source': ', '.join(required)}
    args = [start, end]
    if market == 'wb':
        query = '''SELECT record_date AS day, payload->>'nmId' AS sku,
            payload->>'techSize' AS size, payload->>'subject' AS product_name,
            nullif(payload->>'regionName','') AS buyer_region,
            nullif(payload->>'warehouseType','') AS scheme, 1::numeric AS orders
            FROM wb_api_entities WHERE source_key='statistics.orders'
            AND record_date BETWEEN %s AND %s
            AND lower(coalesce(payload->>'isCancel','false'))<>'true'
            AND nullif(payload->>'nmId','') IS NOT NULL'''
        if sku: query += " AND payload->>'nmId'=%s"; args.append(sku)
    else:
        args = [client, start, end]
        # Keep grain exactly as the typed item fact and raw stats uniqueness contract.
        query = '''SELECT i.order_date AS day,i.offer_id AS sku,''::text AS size,
            i.offer_name AS product_name,nullif(s.payload#>>'{deliveryRegion,name}','') AS buyer_region,
            NULL::text AS scheme,i.units AS orders
            FROM yandex_fact_order_items i JOIN yandex_fact_orders o
              ON o.client_key=i.client_key AND o.business_id=i.business_id
              AND o.campaign_id=i.campaign_id AND o.order_id=i.order_id
            LEFT JOIN yandex_market_entities s ON s.client_key=i.client_key AND s.business_id=i.business_id
              AND s.campaign_id=i.campaign_id AND s.entity_id=i.order_id AND s.source_key='yandex_order_stats'
            WHERE i.client_key=%s AND i.order_date BETWEEN %s AND %s AND NOT i.is_test
              AND i.currency='RUR' AND o.status<>'CANCELLED' AND i.units IS NOT NULL'''
        if sku: query += ' AND i.offer_id=%s'; args.append(sku)
    cur.execute(query + ' ORDER BY day,sku LIMIT 200001', args)
    raw = cur.fetchall()
    truncated = len(raw)>200000
    rows = aggregate_observations(raw[:200000], start, end)
    return rows, {'status': 'partial' if raw else 'blocked', 'source': ', '.join(required),
        'rows_observed': min(len(raw),200000), 'truncated': truncated,
        'reason': 'Наблюдаемые события; календарное покрытие импорта не подтверждено',
        'as_of': max((r['last_event_date'] for r in rows), default=None)}


def load(config, client, market, start, end, sku=''):
    start, end = validate_scope(client, market, start, end, sku)
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        rows, demand = read_demand(cur, client, market, start, end, sku)
        stock = read_stock(cur, market, end, sku, client)
    return jsonable({'client': client, 'marketplace': market, 'date_from': start, 'date_to': end,
        'status': 'partial' if rows or demand.get('delivered_routes') else 'blocked',
        'rows': rows, 'sources': {'demand': demand, 'stock': {k:v for k,v in stock.items() if k!='rows'}},
        'stock_rows': stock['rows'], 'geography_kind': 'buyer_region_unmapped',
        'notice': 'Склад отгрузки не является кластером спроса. Финансовая рекомендация требует подтверждённых входов.'})
