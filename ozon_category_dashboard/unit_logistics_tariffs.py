"""Read-only WB warehouse tariff snapshot, account isolated and rate limited."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from psycopg2.extras import Json
from km_trade_finance import connect_km
from unit_price_conditions import NoRedirect

def load(config, client, credential):
    now = datetime.now(timezone.utc)
    day = (now + timedelta(hours=3)).date().isoformat()
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS unit_logistics_cache
            (client text PRIMARY KEY, attempted_at timestamptz NOT NULL,
             payload jsonb NOT NULL, error text)''')
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('unit_logistics:'+client,))
        cur.execute('SELECT * FROM unit_logistics_cache WHERE client=%s', (client,))
        cached=cur.fetchone()
        if not cached or (now-cached['attempted_at']).total_seconds() >= 3600:
            payload, error = {}, None
            try:
                token=credential(client,'wb_api_token')
                if not token: raise ValueError('Ключ WB не подключён')
                url='https://common-api.wildberries.ru/api/v1/tariffs/box?date='+day
                with build_opener(NoRedirect()).open(Request(url,headers={'Authorization':token}),timeout=20) as response:
                    data=json.load(response)['response']['data']
                if data.get('currency') != 'RUB' or not isinstance(data.get('warehouseList'),list):
                    raise ValueError('WB не подтвердил рублёвые тарифы')
                payload={'warehouses':data['warehouseList'],'date':day,'fetched_at':now.isoformat(),'source':url}
            except HTTPError as exc: error=f'Тарифы WB: HTTP {exc.code}; повтор не раньше чем через час'
            except Exception: error='Тарифы складов WB недоступны; базовый сценарий не является тарифом выбранного склада'
            cur.execute('''INSERT INTO unit_logistics_cache VALUES(%s,%s,%s,%s)
                ON CONFLICT(client) DO UPDATE SET attempted_at=excluded.attempted_at,payload=excluded.payload,error=excluded.error''',
                (client,now,Json(payload),error))
            cached={'payload':payload,'error':error}
    ozon=json.loads((Path(__file__).parent/'data'/'ozon_logistics_20260828.json').read_text(encoding='utf8'))
    return {**cached['payload'],'ozon':ozon,'error':cached['error'],'stale':cached['payload'].get('date')!=day}
