"""Account-scoped planning commissions; external GET only, durable DB cache."""
from datetime import datetime, timezone
import json
import math
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from psycopg2.extras import Json
from km_trade_finance import connect_km
from unit_economics_workspace import checked_config
from unit_economics_engine import json_numbers

WB_URL = 'https://common-api.wildberries.ru/api/v1/tariffs/commission?locale=ru'
WB_SCHEMES = {'FBW': 'paidStorageKgvp', 'FBS': 'kgvpMarketplace', 'DBS': 'kgvpSupplier', 'EDBS': 'kgvpSupplierExpress', 'C&C': 'kgvpPickup'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS unit_commission_cache (
 client text PRIMARY KEY, fetched_at timestamptz, attempted_at timestamptz NOT NULL,
 payload jsonb NOT NULL DEFAULT '[]', error text)'''

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def normalize_wb(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('report'), list):
        raise ValueError('WB не вернул список комиссий')
    result = []
    for r in payload['report']:
        if not isinstance(r, dict) or not r.get('subjectID'):
            continue
        rates = {}
        for scheme, field in WB_SCHEMES.items():
            raw = r.get(field)
            if raw is None or raw == '' or isinstance(raw, bool):
                continue
            value = float(raw)
            if not math.isfinite(value) or not 0 <= value < 100:
                raise ValueError('WB вернул некорректную комиссию')
            rates[scheme] = value
        result.append({'subject_id': str(r['subjectID']), 'subject_name': r.get('subjectName', ''), 'rates': rates})
    if not result:
        raise ValueError('WB вернул пустой список комиссий')
    return result

def fetch_wb(token):
    if not token:
        raise ValueError('Ключ WB не подключён')
    request = Request(WB_URL, headers={'Authorization': token, 'Accept': 'application/json'})
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        return normalize_wb(json.load(response))

def load(config, client, credential, refresh=False, fetcher=fetch_wb):
    checked_config(config, client)
    now = datetime.now(timezone.utc)
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        # Cross-process exclusion: this transaction owns refresh and publishes cache together.
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('unit_commission:'+client,))
        cur.execute('SELECT * FROM unit_commission_cache WHERE client=%s', (client,))
        cached = cur.fetchone()
        age = (now-cached['fetched_at']).total_seconds() if cached and cached['fetched_at'] else float('inf')
        since_attempt = (now-cached['attempted_at']).total_seconds() if cached else float('inf')
        if (refresh or age > 86400) and since_attempt >= 720:
            payload, fetched, error = (cached['payload'], cached['fetched_at'], None) if cached else ([], None, None)
            try:
                payload = fetcher(credential(client, 'wb_api_token'))
                fetched = now
            except HTTPError as exc:
                error = f'WB API: HTTP {exc.code}; повтор не раньше чем через 12 минут'
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) else 'WB API временно недоступен'
            cur.execute('''INSERT INTO unit_commission_cache(client,fetched_at,attempted_at,payload,error)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(client) DO UPDATE SET
                fetched_at=excluded.fetched_at,attempted_at=excluded.attempted_at,payload=excluded.payload,error=excluded.error''',
                (client, fetched, now, Json(payload), error))
            cached = dict(payload=payload, fetched_at=fetched, attempted_at=now, error=error)
        by_id = {r['subject_id']: r for r in cached['payload']}
        by_name = {}
        for r in cached['payload']:
            by_name.setdefault(r['subject_name'].strip().casefold(), []).append(r)
        cur.execute('''SELECT wb_nmid::text sku,min(subject_id)::text subject_id
          FROM wb_stock_api_current WHERE subject_id IS NOT NULL GROUP BY wb_nmid
          HAVING count(DISTINCT subject_id)=1''')
        subjects = {r['sku']: r['subject_id'] for r in cur.fetchall()}
        cur.execute('''SELECT nm_id::text sku,min(subject_name) subject_name FROM wb_finance_lines
          WHERE nm_id IS NOT NULL AND nullif(subject_name,'') IS NOT NULL GROUP BY nm_id
          HAVING count(DISTINCT subject_name)=1''')
        for r in cur.fetchall():
            matches = by_name.get(r['subject_name'].strip().casefold(), [])
            if r['sku'] not in subjects and len(matches) == 1:
                subjects[r['sku']] = matches[0]['subject_id']
        items = []
        for sku, subject in subjects.items():
            tariff = by_id.get(subject)
            if tariff:
                for scheme, pct in tariff['rates'].items():
                    items.append(dict(marketplace='wb', cabinet=client, sku=sku, scheme=scheme,
                        commission_pct=pct, category=tariff['subject_name'], source='WB API /tariffs/commission',
                        source_date=str(cached['fetched_at']), commission_kind='category_tariff'))
        # The importer stores product_id in snapshot.sku for some Ozon API versions.
        # Resolve through the exact catalog product_id; never match identifier domains by coincidence.
        cur.execute('''SELECT sku,coalesce(nullif(ozon_product_id,0),product_id)::text product_id
            FROM ozon_cat_products WHERE nullif(sku,'') IS NOT NULL''')
        products_by_sku = {}
        for r in cur.fetchall():
            if r['product_id']:
                products_by_sku.setdefault(str(r['sku']),set()).add(r['product_id'])
        skus_by_product = {}
        for sku, product_ids in products_by_sku.items():
            if len(product_ids)==1:
                skus_by_product.setdefault(next(iter(product_ids)),set()).add(sku)
        cur.execute('''SELECT DISTINCT ON(sku) sku,product_id,offer_id,snapshot_date,
            sales_percent_fbo,sales_percent_fbs,sales_percent_rfbs,
            fbo_logistics_min,fbo_logistics_max,fbo_return_amount,
            fbs_logistics_min,fbs_logistics_max,fbs_return_amount,acquiring
            FROM ozon_product_price_snapshots ORDER BY sku,snapshot_date DESC''')
        for r in cur.fetchall():
            for scheme in ('FBO','FBS','rFBS'):
                pct = r['sales_percent_'+scheme.lower()]
                if pct is not None:
                    sku_aliases=skus_by_product.get(str(r['product_id']),set())
                    if not sku_aliases and str(r['sku'])!=str(r['product_id']):
                        sku_aliases={str(r['sku'])}
                    for sku in sku_aliases:
                        items.append(dict(marketplace='ozon', cabinet=client, sku=sku, article=r['offer_id'],
                            product_id=r['product_id'],snapshot_sku=r['sku'],scheme=scheme, commission_pct=pct,
                            source='Ozon API → ozon_product_price_snapshots', source_date=str(r['snapshot_date']), commission_kind='sku_tariff',
                            logistics_min=r.get(scheme.lower()+'_logistics_min'), logistics_max=r.get(scheme.lower()+'_logistics_max'),
                            return_amount=r.get(scheme.lower()+'_return_amount')))
    from unit_logistics_tariffs import load as load_logistics
    logistics=load_logistics(config,client,credential)
    return json_numbers(dict(ok=True,client=client,items=items,logistics=logistics,wb=dict(fetched_at=str(cached['fetched_at']) if cached['fetched_at'] else None,
        error=cached['error'],stale=not cached['fetched_at'] or (now-cached['fetched_at']).total_seconds()>86400,
        categories=len(cached['payload'])),source_note='Комиссия категории WB не включает индивидуальные опции конструктора; надбавка задаётся отдельно.'))
