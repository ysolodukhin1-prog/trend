"""Actual FBO warehouse snapshots; API reads only, atomic client-local storage."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from psycopg2.extras import execute_values

FBO_PATH = '/v1/product/info/stocks-by-warehouse/fbo'
CLUSTERS_PATH = '/v2/cluster/list'
LEGACY_CLUSTERS_PATH = '/v1/cluster/list'


def legacy_reference(payload):
    locations = defaultdict(set)
    for cluster in payload.get('clusters') or []:
        for logistic in cluster.get('logistic_clusters') or []:
            for warehouse in logistic.get('warehouses') or []:
                wid = str(warehouse.get('warehouse_id') or '')
                if wid and warehouse.get('name'):
                    locations[wid].add((str(warehouse['name']),str(cluster.get('id') or ''),str(cluster.get('name') or '')))
    return {wid:next(iter(values)) for wid,values in locations.items() if len(values)==1}


def warehouse_reference(payload):
    result = payload.get('result')
    if not isinstance(result, list) or not result:
        raise ValueError('Ozon: некорректный справочник кластеров')
    locations = defaultdict(set)
    for cluster in result:
        data = cluster.get('data') or {}
        region = (data.get('macrolocal_cluster') or {}).get('name') or ''
        cluster_id = str(cluster.get('macrolocal_cluster_id') or '')
        for warehouse in data.get('fulfillments') or []:
            wid = str(warehouse.get('warehouse_id') or '')
            if wid and warehouse.get('name'):
                locations[wid].add((str(warehouse['name']), cluster_id, region))
    # Ambiguous reference assignments remain unidentified; never guess geography.
    return {wid: next(iter(values)) for wid, values in locations.items() if len(values) == 1}


def quantity(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Ozon: отсутствует количество на складе')
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError('Ozon: некорректное количество на складе')
    return int(number)


def fetch_snapshot(post, skus):
    """post(path, body) returns decoded JSON. Cursor, not page length, ends a page."""
    skus = sorted({str(sku) for sku in skus if str(sku).isdigit()})
    if not skus:
        raise ValueError('Ozon: нет SKU для снимка FBO')
    reference = warehouse_reference(post(CLUSTERS_PATH, {'cluster_type': 'CLUSTER_TYPE_OZON'}))
    rows = {}; pages = 0
    started = datetime.now(timezone.utc)
    for offset in range(0, len(skus), 1000):
        batch = skus[offset:offset + 1000]; allowed = set(batch)
        cursor = ''; seen = set()
        while True:
            payload = post(FBO_PATH, {'skus': batch, 'limit': 1000, 'cursor': cursor})
            products = payload.get('products')
            if not isinstance(products, list) or not isinstance(payload.get('has_next'), bool):
                raise ValueError('Ozon: некорректная страница складов FBO')
            pages += 1
            for product in products:
                sku = str(product.get('sku') or ''); wid = str(product.get('warehouse_id') or '')
                if sku not in allowed or not wid.isdigit() or int(wid) <= 0:
                    raise ValueError('Ozon: неизвестный SKU или ID склада в ответе FBO')
                name, cluster_id, cluster_name = reference.get(wid, ('Склад Ozon #' + wid, '', 'Кластер не определён'))
                row = (sku, wid, name, cluster_id, cluster_name,
                       quantity(product.get('present')), quantity(product.get('reserved')))
                key = (sku, wid)
                if key in rows and rows[key] != row:
                    raise ValueError('Ozon: противоречивые дубли SKU/склада; снимок не заменён')
                rows[key] = row
            if not payload['has_next']:
                break
            next_cursor = str(payload.get('cursor') or '')
            if not next_cursor or next_cursor == cursor or next_cursor in seen or pages > 1000:
                raise ValueError('Ozon: неполная пагинация складов FBO; снимок не заменён')
            seen.add(next_cursor); cursor = next_cursor
    if not rows:
        raise ValueError('Ozon: пустая разбивка FBO; предыдущий снимок сохранён')
    if any(not row[3] for row in rows.values()):
        # v2 omits several operating/legacy warehouses. v1 is a name reference
        # fallback only; never overrides quantities or a resolved v2 assignment.
        try:
            fallback = legacy_reference(post(LEGACY_CLUSTERS_PATH, {'cluster_type':'CLUSTER_TYPE_OZON'}))
        except Exception:
            fallback = {}
        for key,row in list(rows.items()):
            if not row[3] and row[1] in fallback:
                name,cluster_id,cluster_name = fallback[row[1]]
                rows[key] = (row[0],row[1],name,cluster_id,cluster_name,row[5],row[6])
    return {'rows': list(rows.values()), 'skus': skus, 'started_at': started,
            'observed_at': datetime.now(timezone.utc), 'pages': pages}


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS public.ozon_fbo_location_snapshot (
            singleton boolean PRIMARY KEY DEFAULT TRUE CHECK(singleton),
            observed_at timestamptz NOT NULL, started_at timestamptz NOT NULL,
            requested_skus integer NOT NULL, returned_skus integer NOT NULL,
            row_count integer NOT NULL, page_count integer NOT NULL, source text NOT NULL);
            CREATE TABLE IF NOT EXISTS public.ozon_fbo_locations (
            sku text NOT NULL, warehouse_id text NOT NULL, warehouse_name text NOT NULL,
            cluster_id text NOT NULL, cluster_name text NOT NULL,
            present bigint NOT NULL CHECK(present>=0), reserved bigint NOT NULL CHECK(reserved>=0),
            PRIMARY KEY(sku,warehouse_id));
            CREATE TABLE IF NOT EXISTS public.ozon_fbo_location_skus (
            sku text PRIMARY KEY, returned boolean NOT NULL)''')


def store_snapshot(conn, snapshot, dry_run=False):
    if dry_run:
        return len(snapshot['rows'])
    ensure_schema(conn)
    returned = {row[0] for row in snapshot['rows']}
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(current_database()),hashtext('ozon-fbo-locations'))")
        cur.execute('SELECT observed_at FROM public.ozon_fbo_location_snapshot WHERE singleton')
        previous = cur.fetchone()
        if previous and previous[0] >= snapshot['observed_at']:
            raise ValueError('Ozon: более новый снимок складов уже сохранён')
        cur.execute('DELETE FROM public.ozon_fbo_locations; DELETE FROM public.ozon_fbo_location_skus')
        execute_values(cur, 'INSERT INTO public.ozon_fbo_locations VALUES %s', snapshot['rows'])
        execute_values(cur, 'INSERT INTO public.ozon_fbo_location_skus VALUES %s',
                       [(sku, sku in returned) for sku in snapshot['skus']])
        cur.execute('''INSERT INTO public.ozon_fbo_location_snapshot VALUES(TRUE,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(singleton) DO UPDATE SET observed_at=excluded.observed_at,
          started_at=excluded.started_at,requested_skus=excluded.requested_skus,
          returned_skus=excluded.returned_skus,row_count=excluded.row_count,
          page_count=excluded.page_count,source=excluded.source''',
                    (snapshot['observed_at'],snapshot['started_at'],len(snapshot['skus']),len(returned),
                     len(snapshot['rows']),snapshot['pages'],FBO_PATH))
    # Caller owns commit/rollback, including preservation after partial failures.
    return len(snapshot['rows'])


def report_stocks(cur, aggregates, now=None):
    """Replace only covered, fresh FBO aggregate SKUs, never add both grains."""
    cur.execute("SELECT to_regclass('public.ozon_fbo_location_snapshot') relation")
    if not cur.fetchone()['relation']:
        return aggregates, {}, ['Ozon FBO: разбивка по складам ещё не загружена.']
    cur.execute('SELECT * FROM public.ozon_fbo_location_snapshot WHERE singleton')
    snapshot = cur.fetchone()
    if not snapshot:
        return aggregates, {}, ['Ozon FBO: разбивка по складам ещё не загружена.']
    now = now or datetime.now(timezone.utc)
    if (now - snapshot['observed_at']).total_seconds() > 36 * 3600:
        return aggregates, {}, ['Ozon FBO: разбивка по складам устарела; показан общий снимок FBO.']
    cur.execute('SELECT * FROM public.ozon_fbo_locations ORDER BY cluster_name,warehouse_name,warehouse_id,sku')
    details = cur.fetchall(); by_sku = defaultdict(list)
    for row in details:
        by_sku[row['sku']].append(row)
    stamp = snapshot['observed_at'].astimezone(ZoneInfo('Europe/Moscow')).date()
    output = []; warehouses = {}; used = set(); fallback = 0; fallback_positive = 0
    for aggregate in aggregates:
        sku = aggregate['sku']
        fresh = (snapshot['observed_at'] - aggregate['observed_at']).total_seconds() >= -900
        if aggregate['warehouse'] != 'FBO' or sku not in by_sku or not fresh:
            row = dict(aggregate)
            if aggregate['warehouse'] == 'FBO':
                row['warehouse'] = 'FBO · без разбивки по складам'; fallback += 1
                fallback_positive += int(aggregate['units'] > 0)
            output.append(row); continue
        if sku in used:
            raise ValueError('Повторный агрегат FBO для SKU')
        used.add(sku)
        for location in by_sku[sku]:
            key = 'FBO:warehouse:' + location['warehouse_id']
            warehouses[key] = {'label': 'Ozon · FBO · ' + location['cluster_name'] + ' · ' + location['warehouse_name'],
                               'warehouse_name': location['warehouse_name'],
                               'date': str(stamp), 'warehouse_id': location['warehouse_id'],
                               'cluster': location['cluster_name'], 'scheme': 'FBO',
                               'observed_at': snapshot['observed_at'].isoformat()}
            output.append({'sku':sku,'warehouse':key,'units':location['present'],
                           'reserved':location['reserved'],'stamp':stamp})
    # Retain explicit source zeros. An absent SKU/warehouse pair remains missing.
    local_time = snapshot['observed_at'].astimezone(ZoneInfo('Europe/Moscow'))
    warnings = [f'Ozon FBO: склады на {local_time:%d.%m.%Y %H:%M} МСК; '
                f'{fallback} SKU без актуальной разбивки (с положительным остатком: {fallback_positive}). '
                'Остаток — present Ozon; резерв в подсказке. '
                'FBS/RFBS — общий остаток по схеме.']
    return output, warehouses, warnings
