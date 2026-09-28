"""Read-only WB order positions and SKU inventory evidence; no inferred missing zeros."""
from datetime import date, timedelta
from urllib.parse import parse_qs
from decimal import Decimal


def json_ready(value):
    if isinstance(value,dict): return {k:json_ready(v) for k,v in value.items()}
    if isinstance(value,list): return [json_ready(v) for v in value]
    if isinstance(value,Decimal): return float(value)
    if isinstance(value,date): return value.isoformat()
    return value


def options(parsed):
    p = parse_qs(parsed.query)
    if p.get('marketplace', ['wb'])[0] != 'wb':
        raise ValueError('Лента заказов доступна для WB')
    end = date.fromisoformat(p.get('date_to', [date.today().isoformat()])[0])
    start = date.fromisoformat(p.get('date_from', [(end-timedelta(days=29)).isoformat()])[0])
    if end < start or (end-start).days > 365:
        raise ValueError('Выберите период от 1 до 366 дней')
    return p, start, end


def numeric(field):
    # Field names are code-owned constants. A malformed source value remains unknown.
    return f"CASE WHEN payload->>'{field}' ~ '^-?[0-9]+([.][0-9]+)?$' THEN (payload->>'{field}')::numeric END"


def order_feed(parsed, get_conn):
    p, start, end = options(parsed)
    page = max(1, int(p.get('page', ['1'])[0]))
    limit = min(200, max(1, int(p.get('page_size', ['50'])[0])))
    status = p.get('order_status', ['all'])[0]
    if status not in ('all', 'active', 'cancelled'):
        raise ValueError('Неизвестный статус заказа')
    clauses = ["source_key='statistics.orders'", 'record_date BETWEEN %s AND %s']
    args = [start, end]
    skus = p.get('inventory_skus') or ([p['sku'][0]] if p.get('sku') else [])
    if skus:
        clauses.append("payload->>'nmId'=ANY(%s)"); args.append(skus)
    search = p.get('order_search', [''])[0].strip()
    if search:
        clauses.append("concat_ws(' ',payload->>'nmId',payload->>'supplierArticle',payload->>'barcode',payload->>'srid',payload->>'subject') ILIKE %s")
        args.append('%'+search+'%')
    if status != 'all':
        clauses.append("lower(coalesce(payload->>'isCancel','false')) " + ('=' if status == 'cancelled' else '<>') + " 'true'")
    base = f"""WITH orders AS (
      SELECT entity_key, record_date, payload->>'date' AS ordered_at,
        payload->>'nmId' AS sku, payload->>'supplierArticle' AS article,
        payload->>'subject' AS product, payload->>'barcode' AS barcode,
        payload->>'techSize' AS size, payload->>'warehouseName' AS warehouse,
        payload->>'regionName' AS region, payload->>'srid' AS order_id,
        lower(coalesce(payload->>'isCancel','false'))='true' AS cancelled,
        {numeric('finishedPrice')} AS buyer_price_raw,
        {numeric('priceWithDisc')} AS seller_price_raw,
        {numeric('totalPrice')} AS list_price,
        {numeric('discountPercent')} AS seller_discount,
        {numeric('spp')} AS spp
      FROM public.wb_api_entities WHERE {' AND '.join(clauses)}), priced AS (
      SELECT *, CASE WHEN buyer_price_raw>0 THEN buyer_price_raw END AS buyer_price,
        CASE WHEN seller_price_raw>0 THEN seller_price_raw END AS seller_price FROM orders)
    """
    payload = dict(marketplace='wb', date_from=start.isoformat(), date_to=end.isoformat(),
                   rows=[], daily=[], total=0, page=page, page_size=limit, total_pages=1,
                   status='unavailable', source='statistics.orders', coverage={})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_api_entities') AS relation")
        if not cur.fetchone()['relation']:
            return payload
        cur.execute("""SELECT min(record_date) AS first_date,max(record_date) AS last_date,
          max(captured_at) AS imported_at FROM public.wb_api_entities WHERE source_key='statistics.orders'""")
        payload['coverage'] = dict(cur.fetchone())
        cur.execute(base+"""SELECT count(*) AS total, count(*) FILTER(WHERE cancelled) AS cancelled,
          count(buyer_price) AS priced, avg(buyer_price) AS buyer_mean,
          min(buyer_price) AS buyer_min,max(buyer_price) AS buyer_max,
          avg(seller_price) AS seller_mean FROM priced""", args)
        payload['summary'] = dict(cur.fetchone())
        total = payload['summary']['total']
        payload.update(total=total, status='partial' if payload['coverage']['first_date'] else 'unavailable',
                       total_pages=max(1,(total+limit-1)//limit))
        page = min(page,payload['total_pages']);payload['page']=page
        cur.execute(base+"""SELECT record_date AS report_date,count(*) AS orders,
          count(*) FILTER(WHERE cancelled) AS cancelled,count(buyer_price) AS priced,
          avg(buyer_price) AS buyer_mean,min(buyer_price) AS buyer_min,max(buyer_price) AS buyer_max,
          avg(seller_price) AS seller_mean FROM priced GROUP BY record_date ORDER BY record_date""", args)
        observed = {str(r['report_date']):dict(r) for r in cur.fetchall()}
        payload['daily'] = [observed.get(str(start+timedelta(days=i)),
                            dict(report_date=str(start+timedelta(days=i)),orders=None,priced=None,
                                 cancelled=None,buyer_mean=None,buyer_min=None,buyer_max=None,seller_mean=None))
                            for i in range((end-start).days+1)]
        cur.execute(base+"SELECT * FROM priced ORDER BY ordered_at DESC NULLS LAST,entity_key DESC LIMIT %s OFFSET %s",args+[limit,(page-1)*limit])
        payload['rows'] = [dict(r) for r in cur.fetchall()]
    return json_ready(payload)


def stock_matrix(rows, locations, start, end, grouping='cluster'):
    days = [(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    groups = {}
    def key(r):
        cluster, warehouse = r.get('cluster_name') or '', r.get('warehouse_name') or ''
        # Do not assign unnamed WB warehouses to invented geographical clusters.
        label = cluster if grouping=='cluster' and cluster else warehouse or 'Общий снимок без склада'
        return (r['source_type'],label, bool(cluster) if grouping=='cluster' else False)
    for location in locations:
        k=key(location)
        groups.setdefault(k,dict(label=k[1],source=k[0],cluster_known=k[2],values={},members=set()))['members'].add((location.get('warehouse_name') or '',location.get('cluster_name') or ''))
    for r in rows:
        k=key(r)
        if k not in groups: continue
        day=str(r['snapshot_date'])
        groups[k]['values'].setdefault(day,[]).append(r.get('stock_total_qty'))
    result=[]
    for k,g in sorted(groups.items()):
        values={}
        for day in days:
            seen=g['values'].get(day,[])
            # In a cluster, absent warehouse observations invalidate the full cluster sum.
            values[day]=sum(seen) if len(seen)==len(g['members']) and all(v is not None for v in seen) else None
        result.append(dict(label=g['label'],source=g['source'],cluster_known=g['cluster_known'],values=values))
    return dict(dates=days,rows=result,cluster_coverage=bool(result) and all(r['cluster_known'] for r in result))


def inventory_evidence(parsed,get_conn):
    p,start,end=options(parsed)
    sku=p.get('inventory_skus',p.get('sku',['']))[0].strip()
    if not sku: raise ValueError('Выберите один SKU')
    grouping=p.get('stock_group',['cluster'])[0]
    if grouping not in ('cluster','warehouse'): raise ValueError('Неизвестная группировка')
    with get_conn() as conn,conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.inventory_history_daily') AS relation")
        if not cur.fetchone()['relation']:
            return dict(sku=sku,**stock_matrix([],[],start,end,grouping))
        cur.execute("""SELECT warehouse_name,cluster_name,source_type FROM public.inventory_history_daily
          WHERE marketplace='wb' AND sku=%s AND snapshot_date<=%s
          GROUP BY warehouse_name,cluster_name,source_type HAVING max(stock_total_qty)>0""",(sku,end))
        locations=[dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT snapshot_date,warehouse_name,cluster_name,source_type,stock_total_qty
          FROM public.inventory_history_daily WHERE marketplace='wb' AND sku=%s
          AND snapshot_date BETWEEN %s AND %s ORDER BY snapshot_date""",(sku,start,end))
        rows=[dict(r) for r in cur.fetchall()]
    return json_ready(dict(sku=sku,**stock_matrix(rows,locations,start,end,grouping)))
