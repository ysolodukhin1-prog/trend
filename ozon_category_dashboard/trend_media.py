"""Daily media facts, with explicit incomplete coverage and atomic API replacement."""
from datetime import date
import importlib
import json
from pathlib import Path
import re
import time
import requests
from psycopg2.extras import Json


class Limited(Exception):
    pass


def wb_request(token, method, path, **kwargs):
    for attempt in range(4):
        response = requests.request(method, 'https://advert-media-api.wildberries.ru' + path,
                                    headers={'Authorization': token}, timeout=90, **kwargs)
        if response.status_code in (401, 403):
            raise Limited('WB Media: нет доступа к API')
        if response.status_code == 429 or response.status_code >= 500:
            wait = max(1, int(response.headers.get('X-Ratelimit-Retry', '10')))
            print(f'ПРОГРЕСС: HTTP {response.status_code}; повтор {attempt + 1}/4; пауза {wait}с', flush=True)
            time.sleep(wait)
            continue
        response.raise_for_status()
        time.sleep(1)
        return response.json() if response.content else []
    raise RuntimeError('WB Media: исчерпаны повторы API')


def wb_rows(payload, day):
    if not isinstance(payload, list):
        raise ValueError('WB Media statistics response is not a list')
    rows = []
    for campaign in payload:
        cid = str(campaign.get('advert_id') or campaign.get('advertId') or campaign.get('id') or '')
        if not cid:
            raise ValueError('WB Media campaign ID missing')
        metrics = []
        for placement in campaign.get('stats', []):
            for daily in placement.get('daily_stats', []):
                if str(daily.get('date', ''))[:10] != str(day):
                    raise ValueError('WB Media returned an unexpected date')
                for app in daily.get('app_type_stats', []):
                    metrics.extend(app.get('stats', []))
        if not metrics:
            continue
        row = dict(report_date=day, media_level='campaign', campaign_id=cid,
                   campaign_name=campaign.get('name') or cid)
        # Placement expenses are not a verified daily spend. Never spread them by day.
        for source, target in [('views', 'views'), ('clicks', 'clicks'), ('atbs', 'baskets'), ('orders', 'orders')]:
            row[target] = sum(m[source] for m in metrics) if all(m.get(source) is not None for m in metrics) else None
        rows.append(row)
    return rows


def fetch_wb(app, day):
    token = app.registered_client_credential(app.CURRENT_CLIENT.get(), 'wb_api_token')
    if not token:
        raise Limited('WB Media: не задан ключ')
    campaigns = []
    offset = 0
    for page_number in range(1000):
        page = wb_request(token, 'GET', '/adv/v1/adverts', params={'limit': 100, 'offset': offset})
        if not isinstance(page, list):
            raise ValueError('WB Media list response is not a list')
        campaigns.extend(page)
        print(f'ПРОГРЕСС: список WB Media; кампаний={len(campaigns)}; страница={page_number+1}', flush=True)
        if not page:
            break
        offset += len(page)
    else:
        raise RuntimeError('WB Media pagination did not finish')
    ids = sorted({int(c['advertId'] if 'advertId' in c else c['id']) for c in campaigns})
    rows, raw = [], []
    errors = 0
    for index in range(0, len(ids), 100):
        body = [{'id': cid, 'dates': [str(day)]} for cid in ids[index:index+100]]
        response = wb_request(token, 'POST', '/adv/v1/stats', json=body)
        if not isinstance(response, list):
            raise ValueError('WB Media statistics schema changed')
        errors += sum(bool(c.get('error')) for c in response)
        rows.extend(wb_rows([c for c in response if not c.get('error')], day))
        raw.append(response)
        print(f'ПРОГРЕСС: {min(index+100,len(ids))}/{len(ids)} | WB Media | rows={len(rows)}', flush=True)
    return rows, {'campaigns': campaigns, 'statistics': raw}, bool(ids or errors)


def fetch_ozon(app, day):
    import sync_km_ozon_api as ozon
    api = ozon.SerialApiClient()
    token = ozon.performance_token(api)
    response = api.request('GET', ozon.PERFORMANCE_BASE_URL + '/api/client/campaign',
                           headers={'Authorization': 'Bearer ' + token}, bucket='performance-campaigns',
                           interval=ozon.PERFORMANCE_INTERVAL).json()
    if not isinstance(response, dict) or not isinstance(response.get('list'), list):
        raise ValueError('Ozon campaign response schema changed')
    campaigns = response['list']
    media = {str(c['id']): c for c in campaigns if str(c.get('advObjectType', '')).upper() in {'BANNER', 'VIDEO', 'VIDEO_BANNER', 'BRAND_SHELF'}}
    unknown = {c.get('advObjectType') for c in campaigns} - {'SKU', 'SEARCH_PROMO', 'SKU_ALL', 'BANNER', 'VIDEO', 'VIDEO_BANNER', 'BRAND_SHELF'}
    facts = ozon.fetch_advertising_campaign_window(api, token, day, day) if media else []
    rows = []
    for fact in facts:
        cid = fact['campaign_id']
        if cid not in media:
            continue
        c = media[cid]
        # Campaign daily supplies base counters, not post-view attribution.
        rows.append({**{k: fact[k] for k in ('report_date','campaign_id','impressions','clicks','expense_rub','ctr_pct','cpm_rub','cpc_rub')},
                     'campaign_name': c.get('title'), 'campaign_format': c.get('advObjectType'),
                     'campaign_status': c.get('state')})
    return rows, {'campaigns': campaigns, 'statistics': facts}, bool(media or unknown)


def importer(market, app):
    module = importlib.import_module('import_' + market + '_media_adv_reports')
    cls = module.WbMediaAdvImporter if market == 'wb' else module.OzonMediaAdvImporter
    obj = cls(Path('/nonexistent'), skip_import=True)
    obj.connect()
    return obj


def ingest(market, day, app):
    started = time.monotonic()
    try:
        rows, payload, partial = (fetch_wb if market == 'wb' else fetch_ozon)(app, day)
    except Limited as exc:
        print('TREND_SOURCE_LIMITED: ' + str(exc), flush=True)
        return
    obj = importer(market, app)
    try:
        obj.create_schema(drop_views=False)
        obj.execute('CREATE TABLE IF NOT EXISTS trend_media_api_snapshots (market text, day date, payload jsonb NOT NULL, updated_at timestamptz DEFAULT now(), PRIMARY KEY(market,day))')
        obj.execute('INSERT INTO trend_media_api_snapshots(market,day,payload) VALUES (%s,%s,%s) ON CONFLICT(market,day) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()',
                    (market, day, Json(payload, dumps=lambda x: json.dumps(x, default=str))))
        table = market + '_media_adv_daily_raw'
        source = f'api:trend:{market}:media:{day}'
        obj.execute(f'DELETE FROM {table} WHERE source_file=%s', (source,))
        imported, skipped = 0, 0
        for index, row in enumerate(rows, 1):
            obj.execute(f'SELECT 1 FROM {table} WHERE report_date=%s AND campaign_id=%s AND source_file NOT LIKE %s LIMIT 1', (day, row['campaign_id'], 'api:trend:%'))
            if obj.cur.fetchone():
                skipped += 1
                continue
            row.update(source_file=source, source_sheet='API campaign daily', source_row_num=index)
            columns = list(row)
            obj.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join(["%s"]*len(columns))})', tuple(row.values()))
            imported += 1
        obj.conn.commit()
        print(f'ИТОГ: media rows={imported} manual_overlap={skipped} elapsed={time.monotonic()-started:.0f}с; output=client DB', flush=True)
        if partial or skipped:
            print('TREND_SOURCE_LIMITED: ответы API сохранены; часть статистики, дневных расходов или атрибуции недоступна; файловые данные сохранены', flush=True)
    finally:
        obj.close()


def refresh(market, app):
    obj = importer(market, app)
    try:
        obj.create_schema(drop_views=False)
        original = obj.execute
        def execute(query, params=None):
            if 'CREATE MATERIALIZED VIEW' in query:
                # Missing API metrics must remain NULL in report views.
                query = re.sub(r'coalesce\((\w+), 0\)', r'\1', query, flags=re.I)
                query = re.sub(r'ELSE 0 END', 'ELSE NULL END', query, flags=re.I)
            return original(query, params)
        obj.execute = execute
        (obj.rebuild_views if market == 'wb' else obj.rebuild_view)()
        obj.conn.commit()
    finally:
        obj.close()
