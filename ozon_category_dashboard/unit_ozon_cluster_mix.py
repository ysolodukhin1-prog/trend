"""Ozon delivered-unit route mix. Cache aggregates only, never customer payloads."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
import time
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from psycopg2.extras import Json
from km_trade_finance import connect_km
from unit_economics_workspace import checked_config
from unit_price_conditions import NoRedirect

PATHS = {'FBO': '/v2/posting/fbo/list', 'FBS': '/v3/posting/fbs/list'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS unit_ozon_cluster_mix_cache (
 client text NOT NULL, date_from date NOT NULL, date_to date NOT NULL,
 fetched_at timestamptz NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(client,date_from,date_to))'''


def aggregate(postings, scheme, complete=True, error=None):
    counts = defaultdict(int)
    seen = set()
    total = missing = invalid = 0
    for p in postings:
        identity = p.get('posting_number')
        if not identity or identity in seen or p.get('status') != 'delivered':
            continue
        seen.add(identity)
        f = p.get('financial_data') or {}
        origin, destination = (str(f.get(k) or '').strip() for k in ('cluster_from', 'cluster_to'))
        for product in p.get('products') or []:
            qty = product.get('quantity')
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty <= 0:
                invalid += 1
                continue
            total += qty
            sku = str(product.get('sku') or '')
            if not sku or not origin or not destination or origin.startswith('SHIPMENT_TYPE_') or destination.startswith('SHIPMENT_TYPE_'):
                missing += qty
                continue
            counts[(sku, origin, destination)] += qty
    if invalid:
        complete, error = False, 'В отправлениях есть товары без корректного количества'
    return {'scheme': scheme, 'status': 'complete' if complete else 'partial',
            'error': error, 'postings': len(seen), 'units': total, 'missing_units': missing,
            'routes': [dict(sku=s, origin=o, destination=d, units=n) for (s, o, d), n in sorted(counts.items())]}


def fetch_page(headers, path, body):
    request = Request('https://api-seller.ozon.ru' + path, data=json.dumps(body).encode(), headers=headers)
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        return json.load(response)


def collect(credential, client, start, end, fetcher=fetch_page, schemes=None):
    headers = {'Client-Id': credential(client, 'ozon_client_id'),
               'Api-Key': credential(client, 'ozon_api_key'), 'Content-Type': 'application/json'}
    if not headers['Client-Id'] or not headers['Api-Key']:
        return {s: aggregate([], s, False, 'Нет доступа к Ozon Seller API') for s in (schemes if schemes is not None else PATHS)}
    # Half-open Moscow calendar windows, at most 31 days per API query.
    windows = []
    current = start
    while current <= end:
        stop = min(current + timedelta(days=31), end + timedelta(days=1))
        windows.append((current, stop)); current = stop
    output = {}
    print(f'ПЛАН: кластеры Ozon | {len(schemes or PATHS)} схем x {len(windows)} окон | страницы по 1000 | максимум 20 страниц/схему; при лимите partial', flush=True)
    for scheme, path in PATHS.items():
        if schemes is not None and scheme not in schemes: continue
        postings, error, calls = [], None, 0
        began = time.monotonic()
        try:
            for window_index, (begin, stop) in enumerate(windows, 1):
                offset = 0
                while True:
                    if calls >= 20 or time.monotonic() - began > 35:
                        raise ValueError('Загрузка не завершена: достигнут предел страниц/времени')
                    since = datetime.combine(begin, datetime.min.time(), timezone(timedelta(hours=3))).astimezone(timezone.utc)
                    to = datetime.combine(stop, datetime.min.time(), timezone(timedelta(hours=3))).astimezone(timezone.utc) - timedelta(microseconds=1)
                    body = {'dir': 'ASC', 'filter': {'since': since.isoformat(), 'to': to.isoformat(), 'status': 'delivered'},
                            'limit': 1000, 'offset': offset, 'with': {'analytics_data': False, 'financial_data': True}}
                    calls += 1
                    result = fetcher(headers, path, body).get('result')
                    if scheme == 'FBO':
                        if not isinstance(result, list): raise ValueError('Ozon не подтвердил формат отправлений FBO')
                        page, more = result, len(result) == 1000
                    else:
                        if not isinstance(result, dict) or not isinstance(result.get('postings'), list) or not isinstance(result.get('has_next'), bool):
                            raise ValueError('Ozon не подтвердил полноту отправлений FBS')
                        page, more = result['postings'], result['has_next']
                    postings.extend(page)
                    print(f'ПРОГРЕСС: {scheme} | окно {window_index}/{len(windows)} | запрос {calls}/20 max | отправлений {len(postings)} | {time.monotonic()-began:.1f}с | ETA неизвестно', flush=True)
                    if not more: break
                    if not page: raise ValueError('Ozon вернул пустую незавершённую страницу')
                    offset += len(page)
                    time.sleep(.2)
        except HTTPError as exc:
            error = f'Ozon {scheme}: HTTP {exc.code}; география не загружена полностью'
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else f'Ozon {scheme}: география временно недоступна'
        output[scheme] = aggregate(postings, scheme, error is None, error)
        output[scheme]['requests'] = calls
        print(f'ИТОГ: {scheme} | {output[scheme]["status"]} | {len(postings)} отправлений | {output[scheme]["units"]} шт | ошибки {int(error is not None)} | {time.monotonic()-began:.1f}с', flush=True)
    return output


def load(config, client, start, end, credential):
    checked_config(config, client)
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start > end or (end-start).days > 366: raise ValueError('Период должен быть от 1 до 367 дней')
    now = datetime.now(timezone.utc)
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s)) locked', ('ozon-cluster-mix:'+client,))
        if not cur.fetchone()['locked']:
            return {'client': client, 'date_from': str(start), 'date_to': str(end), 'schemes': {}, 'error': 'География уже загружается. Обновите распределение позже.'}
        cur.execute('SELECT * FROM unit_ozon_cluster_mix_cache WHERE client=%s AND date_from=%s AND date_to=%s', (client,start,end))
        cached = cur.fetchone()
        ttl = 21600 if cached and all(s['status']=='complete' for s in cached['payload']['schemes'].values()) else 300
        if cached and (now-cached['fetched_at']).total_seconds() < ttl: return cached['payload']
        previous = (cached['payload'].get('schemes') or {}) if cached else {}
        fresh = {s:dict(v, fetched_at=v.get('fetched_at') or cached['payload']['fetched_at']) for s,v in previous.items() if v['status']=='complete' and
                 (now-datetime.fromisoformat(v.get('fetched_at') or cached['payload']['fetched_at'])).total_seconds()<21600}
        updates = collect(credential, client, start, end, schemes=[s for s in PATHS if s not in fresh]) if len(fresh)<2 else {}
        for value in updates.values(): value['fetched_at']=now.isoformat()
        payload = {'client':client, 'date_from':str(start), 'date_to':str(end), 'fetched_at':now.isoformat(),
                   'basis':'Доставленные единицы по дате заказа (МСК), отдельно FBO/FBS; не чистые продажи после возвратов',
                   'source':'Ozon Seller API /v2/posting/fbo/list + /v3/posting/fbs/list',
                   'schemes':{**fresh, **updates}}
        cur.execute('''INSERT INTO unit_ozon_cluster_mix_cache VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(client,date_from,date_to) DO UPDATE SET fetched_at=excluded.fetched_at,payload=excluded.payload''',
                    (client,start,end,now,Json(payload)))
        return payload
