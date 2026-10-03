"""Commercial SKU snapshots. Demand, orders targets and feasibility stay distinct."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json
import math


def research_seasonality(client):
    if client not in {'lera_nena', 'toptop'}:
        return [], []
    path = Path(__file__).parent / 'data' / f'{client}_seasonality.json'
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding='utf-8'))
    rows, evidence = [], []
    for item in data:
        blocked = (client == 'lera_nena' and item['category'] in {'Браслеты', 'Парфюмерная вода'}) or item.get('status') in {'mapping_required', 'incomplete_history', 'zero_seasonal_base', 'source_unavailable'}
        evidence.append({**item, 'status': item.get('status', 'mapping_required') if blocked else 'research_estimate',
                         'source': 'MPStats WB category/trends • sales • FBO',
                         'training_period': '2023–2025',
                         'notice': 'Оценка рынка. Месячный и дневной API требуют сверки; точность прогноза не принята.'})
        if not blocked:
            rows.append({'category_name': item['category'], 'month_number': item['month'],
                         'seasonality_coefficient': item['seasonality_index']})
    return rows, evidence


def amount(units, price):
    return float((Decimal(str(units)) * Decimal(str(price))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def validate_snapshot(payload):
    """Validate the lower grain and derive totals; never trust supplied aggregates."""
    snapshot = payload.get('snapshot')
    if not isinstance(snapshot, dict):
        return None
    metric = 'finance_sales' if payload.get('marketplace') == 'ozon' else 'orders'
    if snapshot.get('metric') != metric or snapshot.get('scope') not in {'portfolio', 'category', 'selection'}:
        raise ValueError('Нужны метрика соответствующей площадки и явная область версии')
    owner, reason = str(snapshot.get('owner') or '').strip(), str(snapshot.get('reason') or '').strip()
    if not owner or not reason:
        raise ValueError('Укажите автора и причину версии')
    rows = snapshot.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > 120000:
        raise ValueError('Нужен план SKU × месяц')
    from datetime import date
    clean, seen, totals, catalog = [], set(), defaultdict(lambda: [0, 0.]), {}
    for row in rows:
        sku = str(row.get('sku') or '').strip()
        month = date.fromisoformat(str(row.get('month_start')))
        units, price = float(row['plan_units']), float(row['price_rub'])
        key = sku, str(month)
        if not sku or month.day != 1 or key in seen:
            raise ValueError('SKU и первый день месяца обязательны; дубли запрещены')
        if not all(math.isfinite(x) and x >= 0 for x in (units, price)) or units != int(units):
            raise ValueError('Количество — целые неотрицательные штуки; цена — неотрицательное число')
        if units > 0 and price <= 0:
            raise ValueError(f'Укажите цену для SKU {sku}')
        seen.add(key)
        category = str(row.get('category_name') or 'Без категории')
        if sku in catalog and catalog[sku] != category:
            raise ValueError('Один SKU не может относиться к двум категориям версии')
        catalog[sku] = category
        revenue = amount(units, price)
        clean.append({'sku': sku, 'category_name': category, 'month_start': str(month),
                      'plan_units': int(units), 'price_rub': price, 'plan_revenue': revenue,
                      'reason': str(row.get('reason') or reason)[:1000]})
        totals[str(month)][0] += int(units)
        totals[str(month)][1] += revenue
    if not 1 <= len(totals) <= 12 or len(clean) != len(catalog) * len(totals):
        raise ValueError('Все товары версии должны иметь одинаковые 1–12 месяцев')
    if snapshot['scope'] == 'category' and len(set(catalog.values())) != 1:
        raise ValueError('Область категории должна содержать ровно одну категорию')
    result = {'metric': metric, 'scope': snapshot['scope'], 'owner': owner[:150], 'reason': reason[:1000],
              'rows': clean, 'source_to': str(snapshot.get('source_to') or '')[:10],
              'base_version_id': snapshot.get('base_version_id'), 'catalog_count': len(catalog),
              'coefficient_overrides': validate_overrides(snapshot.get('coefficient_overrides', [])),
              'model': f'{metric}-rolling-v3-seasonal-transition'}
    return result, [{'month_start': m, 'plan_units': v[0], 'plan_revenue': round(v[1], 2)} for m, v in sorted(totals.items())]


def attach_commercial_plan(products, months, versions, anchor, filtered, marketplace):
    """A version can target a category; newest approved SKU/month wins, including zero."""
    metric = 'orders' if marketplace == 'wb' else 'finance_sales'
    applicable = [v for v in versions if v.get('assumptions', {}).get('snapshot', {}).get('metric') == metric]
    approved, approved_ids = {}, {}
    for version in applicable:
        if version['status'] != 'approved':
            continue
        for row in version['assumptions']['snapshot']['rows']:
            key = row['sku'], row['month_start']
            if key not in approved:
                approved[key] = row
                approved_ids[key] = version['version_id']
    categories = defaultdict(list)
    for product in products:
        categories[product['category_name']].append(product)
        for row in product['monthly']:
            saved = approved.get((str(product['sku']), row['month_start']))
            row['commercial_plan_units'] = saved['plan_units'] if saved else None
            row['commercial_plan_revenue'] = saved['plan_revenue'] if saved else None
            row['plan_price_rub'] = saved['price_rub'] if saved else product.get('price_rub')
            row['commercial_version_id'] = approved_ids.get((str(product['sku']), row['month_start']))
            if marketplace == 'wb':
                row['approved_plan_units'] = row['commercial_plan_units']
                row['approved_plan_revenue'] = row['commercial_plan_revenue']
    # Preserve product/row order and NULL semantics while indexing each scope once.
    rows_by_scope = {}
    def aggregate(items, month):
        scope = id(items)
        if scope not in rows_by_scope:
            indexed = defaultdict(list)
            for product in items:
                for row in product['monthly']:
                    indexed[row['month_start']].append(row)
            rows_by_scope[scope] = indexed
        rows = rows_by_scope[scope].get(month, [])
        result = {'month_start': month, 'sku_count': len(items)}
        for key in ('actual_units', 'actual_revenue', 'forecast_units', 'forecast_revenue', 'commercial_plan_units', 'commercial_plan_revenue', 'achievable_units', 'achievable_revenue'):
            vals = [r.get(key) for r in rows]
            result[key] = round(sum(vals), 2) if vals and all(v is not None for v in vals) else None
        result['approved_sku_count'] = sum(r.get('commercial_plan_units') is not None for r in rows)
        result['version_ids'] = sorted({r['commercial_version_id'] for r in rows if r.get('commercial_version_id')})
        return result
    for row in months:
        values = aggregate(products, row['month_start'])
        for key in ('commercial_plan_units', 'commercial_plan_revenue', 'approved_sku_count', 'version_ids'):
            row[key] = values[key]
        if marketplace == 'wb':
            row['orders_plan_units'] = row['approved_plan_units'] = values['commercial_plan_units']
            row['orders_plan_rub'] = row['approved_plan_revenue'] = values['commercial_plan_revenue']
    return {'metric': 'orders' if marketplace == 'wb' else 'finance_sales',
            'latest_version_id': applicable[0]['version_id'] if applicable else None,
            'scope': 'selection' if filtered else 'portfolio', 'sku_count': len(products),
            'categories': [{'name': name, 'sku_count': len(items), 'months': [aggregate(items, r['month_start']) for r in months]} for name, items in sorted(categories.items())]}


def validate_overrides(rows):
    from datetime import date
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError('Слишком много ручных коэффициентов')
    clean, seen = [], set()
    for row in rows:
        scope, metric, entity = row.get('scope'), row.get('metric'), str(row.get('entity') or '')
        month = date.fromisoformat(str(row.get('month_start')))
        value = float(row.get('value'))
        key = scope, entity, metric, str(month)
        if scope not in {'category', 'sku'} or metric not in {'seasonality', 'trend', 'activity', 'growth'} or not entity or month.day != 1 or key in seen:
            raise ValueError('Проверьте область, метрику, месяц и уникальность ручной правки')
        if not math.isfinite(value) or not 0 <= value <= 10 or not str(row.get('reason') or '').strip():
            raise ValueError('Множитель должен быть от 0 до 10; причина обязательна')
        seen.add(key)
        clean.append({'scope': scope, 'entity': entity, 'metric': metric, 'month_start': str(month), 'value': value, 'reason': str(row['reason'])[:1000]})
    return clean
