"""Read-only seller prices, isolated by client and campaign."""
import json
import time
import threading
from datetime import datetime, timezone
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from unit_yandex_tariffs import NoRedirect
from unit_economics_workspace import checked_config
from km_trade_finance import connect_km

_lock = threading.Lock()
_cache = {}

def fetch(key, path, body):
    request = Request('https://api.partner.market.yandex.ru/v2/' + path,
        data=json.dumps(body).encode(), method='POST',
        headers={'Api-Key': key, 'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.loads(response.read(4 * 1024 * 1024))
    except (HTTPError, URLError, TimeoutError):
        raise ValueError('Не удалось получить текущие цены Яндекса') from None
    if result.get('status') != 'OK':
        raise ValueError('Яндекс не подтвердил цены')
    return result.get('result', {})

def prices(config, client, payload, credential_getter, transport=fetch):
    checked_config(config, client)
    cabinet = str(payload.get('cabinet') or '')
    skus = payload.get('skus')
    if not cabinet.isdecimal() or not isinstance(skus, list) or not 1 <= len(skus) <= 500 or any(not isinstance(s, str) or not 1 <= len(s) <= 255 for s in skus):
        raise ValueError('Нужен магазин и от 1 до 500 SKU')
    skus = sorted(set(skus))
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute('SELECT business_id FROM yandex_dim_store WHERE client_key=%s AND campaign_id=%s AND is_accessible AND import_enabled', (client, cabinet))
        store = cur.fetchone()
    if not store:
        raise ValueError('Магазин недоступен в выбранном аккаунте')
    cache_key = (client, cabinet, tuple(skus))
    with _lock:
        old = _cache.get(cache_key)
        if old and time.monotonic() - old[0] < 300:
            return old[1]
        key = credential_getter(client, 'yandex_market_api_key')
        business = str(store['business_id'])
        settings = transport(key, f'businesses/{business}/settings', {})
        only_default = (settings.get('settings') or {}).get('onlyDefaultPrice')
        if not isinstance(only_default, bool):
            raise ValueError('Не подтверждены настройки цен Яндекса')
        basic = transport(key, f'businesses/{business}/offer-prices', {'offerIds': skus})
        source = {r['offerId']: r for r in basic.get('offers', []) if r.get('offerId') in skus}
        if not only_default:
            overrides = transport(key, f'campaigns/{cabinet}/offer-prices', {'offerIds': skus})
            source.update({r['offerId']: r for r in overrides.get('offers', []) if r.get('offerId') in skus})
        observed = datetime.now(timezone.utc).isoformat()
        result = {'ok': True, 'client': client, 'cabinet': cabinet, 'observed_at': observed, 'items': {}}
        for sku, row in source.items():
            price = row.get('price') or {}
            value = price.get('value')
            if price.get('currencyId') == 'RUR' and isinstance(value, (int, float)) and value > 0:
                result['items'][sku] = {'value': value, 'date': observed, 'updated_at': row.get('updatedAt') or price.get('updatedAt'), 'note': 'Цена продавца ЯМ с учётом настроек магазина; получена из API. Скидки Маркета покупателю не включены.'}
        _cache[cache_key] = (time.monotonic(), result)
        return result
