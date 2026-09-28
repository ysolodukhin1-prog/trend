"""Inspectable coefficient forecasts from the same stages used by sales planning."""
from collections import defaultdict
from datetime import date, timedelta
import math


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def product_trend(rows, source_from, source_to, category_trend):
    """Conservative SKU estimate; sparse/new products explicitly inherit category."""
    start = source_to - timedelta(days=29)
    totals = [0.0, 0.0, 0.0]
    for row in rows:
        day = row.get('sale_date')
        if day and start <= day <= source_to:
            totals[(day - start).days // 10] += max(numeric(row.get('units')) or 0, 0)
    earliest = min((r['sale_date'] for r in rows if r.get('sale_date')), default=None)
    enough_span = source_from <= start and earliest is not None and earliest <= start
    enough_volume = totals[0] + totals[1] >= 10
    own = enough_span and enough_volume
    raw = totals[2] / ((totals[0] + totals[1]) / 2) if own else category_trend
    return {
        'raw': raw, 'basis': 'sku_history' if own else 'category_inherited',
        'reason': 'Три окна по 10 дней; в первых двух суммарно не менее 10 шт.' if own else
                  'Тренд категории: у товара нет 30 дней истории или в первых двух окнах менее 10 шт.',
        'period_from': start.isoformat(), 'period_to': source_to.isoformat(),
        'window_units': totals,
    }


FIELDS = {'growth': 'effective_growth_coefficient', 'trend': 'effective_trend_coefficient',
          'seasonality': 'effective_seasonality_coefficient', 'activity': 'promotion_coefficient',
          'total': 'planning_coefficient'}


def build_coefficient_forecast(products, page_products, category_trends, market_rows, anchor, source_from, source_to, marketplace):
    """Category factors are stage ratios across ALL selected products, before pagination."""
    grouped = defaultdict(list)
    for product in products:
        grouped[product['category_name']].append(product)
    seasonal = {(r['category_name'], int(r['month_number'])) for r in market_rows
                if numeric(r.get('seasonality_coefficient')) is not None}
    trends = {r['category_name']: r for r in category_trends}
    visible_skus = {str(p['sku']) for p in page_products}
    start = date.fromisoformat(anchor)
    months = [date((start.year * 12 + start.month - 1 + offset) // 12,
                   (start.year * 12 + start.month - 1 + offset) % 12 + 1, 1).isoformat() for offset in range(12)]
    result = []
    for category, items in sorted(grouped.items()):
        detail = trends.get(category, {})
        category_months = []
        for month in months:
            rows = [r for p in items for r in p.get('monthly', []) if r['month_start'] == month]
            stages = ['planning_baseline_units', 'planning_after_growth_units', 'planning_after_trend_units', 'planning_after_seasonality_units', 'planning_forecast_units']
            sums = [sum(numeric(r.get(key)) or 0 for r in rows) for key in stages]
            values = {key: round(sums[i + 1] / sums[i], 6) if sums[i] > 0 else None
                      for i, key in enumerate(['growth', 'trend', 'seasonality', 'activity'])}
            values['total'] = round(sums[-1] / sums[0], 6) if sums[0] > 0 else None
            # With zero demand a common multiplier remains identifiable; a weighted
            # blend of different multipliers does not.
            for metric, field in FIELDS.items():
                if values[metric] is None:
                    common = {numeric(r.get(field)) for r in rows}
                    if len(common) == 1 and None not in common:
                        values[metric] = next(iter(common))
            category_months.append({'month_start': month, **values,
                                    'seasonality_available': (category, int(month[5:7])) in seasonal})
        children = []
        for product in items:
            if str(product['sku']) not in visible_skus:
                continue
            children.append({
                'id': str(product['sku']), 'name': product.get('article') or str(product['sku']),
                'product_name': product.get('product_name') or '',
                'trend_evidence': product.get('trend_evidence', {}),
                'months': [{'month_start': r['month_start'],
                            **{key: numeric(r.get(field)) for key, field in FIELDS.items()},
                            'seasonality_available': (category, int(r['month_start'][5:7])) in seasonal}
                           for r in product.get('monthly', []) if r['month_start'] >= anchor],
            })
        result.append({'id': category, 'name': category, 'product_count': len(items),
                       'sku_trend_count': sum(p.get('trend_evidence', {}).get('basis') == 'sku_history' for p in items),
                       'trend_evidence': {'basis': 'category_stage_ratios', 'reason': 'Отношения сумм последовательных этапов расчёта всех выбранных товаров категории; не среднее коэффициентов.',
                                          'period_from': detail.get('period_start'), 'period_to': detail.get('period_end'),
                                          'raw': detail.get('trend_coefficient'),
                                          'window_units': [d.get('sales_units') for d in detail.get('decades', [])]},
                       'months': category_months, 'products': children})
    return {'version': 'monthly-coefficients-v2-12m', 'anchor_month': anchor, 'months': months,
            'source_from': source_from.isoformat(), 'source_to': source_to.isoformat(),
            'fact_basis': 'Заказы WB' if marketplace == 'wb' else 'Продажи Ozon по финансовым операциям',
            'categories': result, 'product_count': len(products), 'visible_product_count': len(page_products),
            'methodology': [
                'Горизонт коэффициентов — 12 месяцев: текущий M0 и следующие M1–M11, включая следующий календарный год. Сезонность выбирается по месяцу года, активность — по конкретному году и месяцу.',
                'Тренд товара = объём последнего 10-дневного окна / средний объём двух предыдущих. Для новой или редкой позиции используется тренд категории. Это оценка по доступной истории, без поправки на OOS.',
                'В план входит 1 + (тренд в коридоре 0,70–1,35 − 1) × вес тренда. Этот множитель применяется каждый будущий месяц; отдельная сезонность меняется по месяцам.',
                'В скользящем плане сезонность — переход: индекс месяца / индекс предыдущего месяца. SKU наследует категорию; ручная правка SKU приоритетнее. Если источника нет, используется допущение 1; оно отмечено звёздочкой.',
                'Ручной К активности — изменение спроса к предыдущему месяцу; SKU приоритетнее категории. Без ручных правок применяется отношение сохранённых уровней активности, без уровней — допущение 1. Это сценарий маркетинга, а не измеренный эффект рекламы.',
                'Общий множитель = рост × тренд × сезонность × активность. Рост к базе применяется в M0 и M1; дальше переносится в базе без повторного начисления.',
                'M0 коэффициенты относятся к расчётному плану от прошлого факта; M1 — к Run-Rate M0, далее — к предыдущему прогнозу. Категории считаются по всему фильтру, товары показаны постранично. При нулевой базе категории отношение не определено: —.',
            ]}
