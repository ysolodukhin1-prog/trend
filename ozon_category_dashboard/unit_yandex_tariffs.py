"""Read-only, account-scoped Yandex tariff quotes. Never change seller prices.

An API quote is approximate and may contain mutually exclusive service variants.
Preserve these variants; do not turn their sum into profit or a price recommendation.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from km_trade_finance import connect_km
from unit_economics_workspace import checked_config

URL = 'https://api.partner.market.yandex.ru/v2/tariffs/calculate'
DOCUMENTATION = 'https://yandex.ru/dev/market/partner-api/doc/ru/reference/tariffs/calculateTariffs'
LABELS = dict(AGENCY_COMMISSION='Приём платежа', PAYMENT_TRANSFER='Перевод платежа',
              FEE='Размещение', DELIVERY_TO_CUSTOMER='Доставка покупателю',
              CROSSREGIONAL_DELIVERY='Межрегиональная доставка', EXPRESS_DELIVERY='Экспресс-доставка',
              SORTING='Обработка заказа', MIDDLE_MILE='Средняя миля', ITEM_BOOKING='Бронирование')
_lock = threading.Lock()
_cache = {}
_last_request = 0.0


def number(value, name, positive=True):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or (result <= 0 if positive else result < 0):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'Некорректное значение: {name}') from None


def request_body(campaign, offer, price, frequency=None, delay=None):
    campaign_number = number(campaign, 'магазин')
    category = number(offer.get('categoryId'), 'категория Маркета')
    if campaign_number != campaign_number.to_integral() or category != category.to_integral():
        raise ValueError('Идентификатор магазина и категории должен быть целым')
    raw_price = number(price, 'цена')
    if raw_price > Decimal('100000000'):
        raise ValueError('Цена должна быть от 0,01 до 100 000 000 ₽')
    rounded = raw_price.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if rounded <= 0 or rounded > Decimal('100000000'):
        raise ValueError('Цена должна быть от 0,01 до 100 000 000 ₽')
    product = {'categoryId': int(category), 'price': float(rounded), 'quantity': 1}
    for field in ('length', 'width', 'height', 'weight'):
        product[field] = float(number(offer.get(field), field))
        if not math.isfinite(product[field]) or product[field] <= 0:
            raise ValueError(f'Некорректное значение: {field}')
    # Live API rejects campaignId combined with currency (2026-09-12), even
    # though the documentation example includes both. Verify RUR in response.
    params = {'campaignId': int(campaign_number)}
    if frequency not in (None, '', 'DAILY', 'WEEKLY', 'BIWEEKLY', 'MONTHLY'):
        raise ValueError('Неизвестная частота выплат')
    if frequency:
        params['frequency'] = frequency
    if delay is not None:
        if isinstance(delay, bool) or delay not in (0, 1, 2, 4) or frequency != 'WEEKLY':
            raise ValueError('Отсрочка 0, 1, 2 или 4 недели доступна только для еженедельных выплат')
        params['paymentDelayWeeks'] = delay
    return {'parameters': params, 'offers': [product]}


def normalize_response(body, response):
    """Check exact offer identity and retain all conditional tariffs, including zero."""
    if not isinstance(response, dict) or response.get('status') != 'OK':
        raise ValueError('Яндекс не вернул успешный расчёт тарифов')
    result = response.get('result')
    rows = result.get('offers') if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('Ответ тарифов не соответствует запрошенному товару')
    returned = rows[0].get('offer', {})
    if not isinstance(returned, dict):
        raise ValueError('Нет параметров товара в ответе тарифов')
    expected = body['offers'][0]
    for key, value in expected.items():
        # quantity is optional in the response when its default is one.
        if number(returned.get(key, 1 if key == 'quantity' else None), key) != Decimal(str(value)):
            raise ValueError('Параметры товара в ответе тарифов отличаются от запроса')
    tariffs = rows[0].get('tariffs')
    if not isinstance(tariffs, list) or not tariffs:
        raise ValueError('Яндекс вернул пустой список услуг; расходы неизвестны')
    groups = defaultdict(list)
    for tariff in tariffs:
        if not isinstance(tariff, dict) or not isinstance(tariff.get('type'), str):
            raise ValueError('Некорректная услуга в ответе Яндекса')
        if tariff.get('currency') != 'RUR':
            raise ValueError('Валюта тарифа не подтверждена как рубли')
        amount = number(tariff.get('amount'), 'стоимость услуги', positive=False)
        parameters = tariff.get('parameters', [])
        if not isinstance(parameters, list) or any(not isinstance(p, dict) or
                not isinstance(p.get('name'), str) or not isinstance(p.get('value'), str) for p in parameters):
            raise ValueError('Некорректные условия тарифа')
        groups[tariff['type']].append({'amount': float(amount), 'currency': 'RUR',
                                     'parameters': [{'name': p['name'], 'value': p['value']} for p in parameters]})
    services = [{'type': key, 'label': LABELS.get(key, key), 'variants': variants,
                 'requires_selection': len(variants) != 1} for key, variants in groups.items()]
    ambiguous = any(g['requires_selection'] for g in services)
    # This is only the subtotal of returned services, never all costs of sale.
    subtotal = None if ambiguous else float(sum(Decimal(str(g['variants'][0]['amount'])) for g in services))
    return {'services': services, 'has_alternatives': ambiguous, 'returned_services_subtotal': subtotal,
            'status': 'alternatives' if ambiguous else 'quoted', 'is_approximate': True,
            'profit': None, 'mrc': None, 'rrc': None,
            'limitations': ['Предварительная стоимость услуг на момент запроса, не фактические начисления.',
                'Варианты одной услуги не складываются; условия варианта нужно сопоставить с отгрузкой.',
                'Ответ не подтверждает полноту хранения, возвратов, рекламы, налогов и внешних затрат.',
                'Расчёт относится только к указанной цене. МРЦ и РРЦ по нему не экстраполируются.']}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Перенаправление API тарифов отклонено')


def fetch_quote(key, body):
    if not key or not key.isascii() or any(c.isspace() for c in key):
        raise ValueError('Нет доступного ключа Яндекс Маркета')
    req = Request(URL, data=json.dumps(body).encode(), method='POST',
                  headers={'Api-Key': key, 'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Ответ тарифов превышает допустимый размер')
        return json.loads(raw)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError('Ключ кабинета не даёт доступа к расчёту тарифов') from None
        if exc.code in (420, 429):
            raise ValueError('Лимит запросов Яндекса. Повторите проверку позже') from None
        if exc.code == 400:
            try:
                error_body = json.loads(exc.read(65536))
                codes = {e.get('code') for e in error_body.get('errors', []) if isinstance(e, dict)}
            except (ValueError, TypeError, AttributeError):
                codes = set()
            if 'INVALID_PAYMENT_SETTINGS' in codes:
                raise ValueError('Яндекс отклонил выбранный график выплат и отсрочку. Измените условия; предыдущие тарифы не применяются.') from None
        raise ValueError(f'Яндекс не рассчитал тарифы (HTTP {exc.code})') from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ValueError('Не удалось получить ответ API тарифов Яндекса') from None


def quote(config, client, payload, credential_getter, transport=fetch_quote):
    """Resolve source dimensions and account ownership before credentials/network."""
    global _last_request
    checked_config(config, client)
    cabinet = str(payload.get('cabinet') or '')
    sku = str(payload.get('sku') or '').strip()
    if not cabinet.isdecimal() or not 1 <= len(sku) <= 300:
        raise ValueError('Нужны магазин и точный артикул Яндекса')
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute('''SELECT business_id,store_name,placement_type FROM yandex_dim_store
                       WHERE client_key=%s AND campaign_id=%s AND is_accessible AND import_enabled''', (client, cabinet))
        store = cur.fetchone()
        if not store:
            raise ValueError('Магазин недоступен в выбранном аккаунте')
        cur.execute('''SELECT r.payload,j.finished_at,j.job_key,r.row_no FROM yandex_analytics_raw r
                       JOIN yandex_analytics_jobs j USING(job_key)
                       WHERE j.client_key=%s AND j.business_id=%s AND j.source_key='catalog'
                       AND j.state='completed' AND r.payload->'offer'->>'offerId'=%s
                       ORDER BY j.finished_at DESC,r.row_no DESC LIMIT 1''', (client, store['business_id'], sku))
        source = cur.fetchone()
    if not source:
        raise ValueError('Нет карточки Яндекса с категорией и габаритами для этого артикула')
    product = source['payload'].get('offer', {})
    if product.get('archived') or cabinet not in {str(c.get('campaignId')) for c in product.get('campaigns', [])}:
        raise ValueError('Карточка не подтверждена в этом магазине или находится в архиве')
    dimensions = product.get('weightDimensions') or {}
    offer = {**dimensions, 'categoryId': source['payload'].get('mapping', {}).get('marketCategoryId')}
    body = request_body(cabinet, offer, payload.get('price'), payload.get('frequency'), payload.get('payment_delay_weeks'))
    provenance = {'client': client, 'cabinet': cabinet, 'sku': sku, 'store_name': store['store_name'],
                  'scheme': store['placement_type'], 'catalog_at': str(source['finished_at']),
                  'catalog_ref': f"yandex_analytics_raw:{source['job_key']}:{source['row_no']}",
                  'request': body, 'documentation': DOCUMENTATION}
    cache_key = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    # Serial requests stay below the documented 100/min limit and coalesce duplicate clicks.
    with _lock:
        now = time.monotonic()
        for old in [k for k, (stamp, _) in _cache.items() if now - stamp > 900]:
            del _cache[old]
        if cache_key in _cache:
            return {**_cache[cache_key][1], 'cached': True}
        pause = max(0, .65 - (now - _last_request))
        if pause:
            time.sleep(pause)
        key = credential_getter(client, 'yandex_market_api_key')
        try:
            response = transport(key, body)
        finally:
            _last_request = time.monotonic()
        result = {**provenance, **normalize_response(body, response), 'ok': True,
                  'quoted_at': datetime.now(timezone.utc).isoformat(), 'cached': False, 'cache_minutes': 15}
        if len(_cache) >= 500:
            del _cache[next(iter(_cache))]
        _cache[cache_key] = (time.monotonic(), result)
        return result
