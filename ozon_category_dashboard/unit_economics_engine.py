"""Auditable scenario economics per final retained unit. No source inference or I/O.

Money inputs include VAT; input VAT credit is explicit. Percentages use 0..100.
Each scenario is an assumption, never a settlement or an official tariff quote.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING

D = Decimal
VERSION = '2026-09-12.3'
FIELDS = {
    'price': ('Цена продавца до удержаний, ₽', None),
    'cogs': ('Себестоимость, ₽', None),
    'commission_pct': ('Комиссия, % цены продавца', None),
    'acquiring_pct': ('Приём и перевод платежа, %', None),
    'forward': ('Доставка на отправку, ₽', None),
    'reverse_reject': ('Обратная доставка невыкупа, ₽', None),
    'reverse_return': ('Обратная доставка возврата, ₽', None),
    'fulfillment': ('Фулфилмент на отправку, ₽', None),
    'packaging': ('Упаковка на отправку, ₽', None),
    'inbound': ('Поставка на сохранённую продажу, ₽', None),
    'storage': ('Хранение на сохранённую продажу, ₽', None),
    'other': ('Прочие расходы на сохранённую продажу, ₽', None),
    'buyout_pct': ('Выкуп от отправленных, %', None),
    'return_pct': ('Возврат после выкупа, % выкупов', None),
    'loss_pct': ('Списание от вернувшихся единиц, %', None),
    'ad_pct': ('Реклама, % выручки сохранённых продаж', None),
    'vat_pct': ('Исходящий НДС, %', None),
    'input_vat': ('Вычет входного НДС на ед., ₽', None),
    'tax_pct': ('Ставка налога модели, %', None),
    'minimum_tax_pct': ('Минимальный налог, % выручки без НДС', None),
    'capital_days': ('Заморозка капитала, дней', None),
    'capital_pct': ('Стоимость капитала, % годовых', None),
    'platform_discount_pct': ('Скидка покупателю за счёт площадки, %', 0),
    'commission_refund_pct': ('Возврат комиссии при возврате, %', 100),
    'acquiring_refund_pct': ('Возврат платёжной комиссии, %', 0),
    'mrc_margin_pct': ('Целевая маржа МРЦ, %', 0),
    'rrc_margin_pct': ('Целевая маржа РРЦ, %', 20),
    'promo_discount_pct': ('Скидка продавца в акции, %', 0),
    'promo_uplift_pct': ('Прирост сохранённых продаж в акции, %', 0),
}


def decimal(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Логическое значение не является числом')
    try:
        result = D(str(value).strip().replace(',', '.'))
    except InvalidOperation:
        raise ValueError('Некорректное число') from None
    if not result.is_finite():
        raise ValueError('Число должно быть конечным')
    return result


def validate(inputs):
    unknown = set(inputs) - set(FIELDS) - {'tax_mode', 'acquiring_basis', 'commission_tiers', 'retail_target'}
    if unknown:
        raise ValueError('Неизвестные параметры: ' + ', '.join(sorted(unknown)))
    result = {'retail_target':decimal(inputs.get('retail_target'))}
    if result['retail_target'] is not None and result['retail_target']<=0:
        raise ValueError('Целевая розничная цена должна быть больше нуля')
    for key, (label, default) in FIELDS.items():
        value = decimal(inputs.get(key, default))
        if value is not None and (value < 0 or (key.endswith('_pct') and key != 'promo_uplift_pct' and value > 100)):
            raise ValueError(label + ': значение вне допустимого диапазона')
        result[key] = value
    result['tax_mode'] = inputs.get('tax_mode', 'revenue')
    if result['tax_mode'] not in ('revenue', 'profit'):
        raise ValueError('Налоговая модель: revenue или profit')
    result['acquiring_basis'] = inputs.get('acquiring_basis', 'seller')
    if result['acquiring_basis'] not in ('seller', 'buyer'):
        raise ValueError('База платёжной комиссии: seller или buyer')
    tiers = inputs.get('commission_tiers') or []
    if not isinstance(tiers, list) or len(tiers) > 30:
        raise ValueError('Не более 30 тарифных диапазонов')
    last = D(0)
    for tier in tiers:
        upper, rate = decimal(tier.get('up_to')), decimal(tier.get('pct'))
        if upper is None or rate is None or upper <= last or not 0 <= rate <= 100:
            raise ValueError('Тарифные границы должны возрастать, ставки от 0 до 100')
        last = upper
    result['commission_tiers'] = tiers
    return result


def _evaluate(p, price):
    buyout, returned = p['buyout_pct'] / 100, p['return_pct'] / 100
    retained = buyout * (1 - returned)
    if retained <= 0:
        return {'status': 'impossible', 'reason': 'Нет сохранённых продаж: выкуп 0% или возврат 100%'}
    shipments = 1 / retained
    rejects = (1 - buyout) / retained
    returns = returned / (1 - returned)
    buyer_price = price * (1 - p['platform_discount_pct'] / 100)
    commission_pct = p['commission_pct']
    for tier in p['commission_tiers']:
        if price <= decimal(tier['up_to']):
            commission_pct = decimal(tier['pct'])
            break
    acquisition_base = buyer_price if p['acquiring_basis'] == 'buyer' else price
    costs = {
        'cogs': p['cogs'],
        'loss': p['cogs'] * (rejects + returns) * p['loss_pct'] / 100,
        'commission': price * commission_pct / 100 * (1 + returns * (1 - p['commission_refund_pct'] / 100)),
        'acquiring': acquisition_base * p['acquiring_pct'] / 100 * (1 + returns * (1 - p['acquiring_refund_pct'] / 100)),
        'forward': p['forward'] * shipments,
        'reverse_reject': p['reverse_reject'] * rejects,
        'reverse_return': p['reverse_return'] * returns,
        'fulfillment': p['fulfillment'] * shipments,
        'packaging': p['packaging'] * shipments,
        'inbound': p['inbound'], 'storage': p['storage'], 'other': p['other'],
        'advertising': price * p['ad_pct'] / 100,
    }
    output_vat = price * p['vat_pct'] / (100 + p['vat_pct'])
    # Negative VAT means a credit, not an invented cash refund.
    costs['vat'] = output_vat - p['input_vat']
    costs['capital'] = (costs['cogs'] + costs['loss']) * p['capital_days'] * p['capital_pct'] / 36500
    pretax = price - sum(costs.values())
    tax_revenue = price - output_vat
    tax_base = tax_revenue if p['tax_mode'] == 'revenue' else max(D(0), pretax + costs['capital'])
    costs['tax'] = max(tax_base * p['tax_pct'] / 100, tax_revenue * p['minimum_tax_pct'] / 100)
    profit = price - sum(costs.values())
    return {'status': 'calculated', 'price': price, 'buyer_price': buyer_price,
            'revenue_ex_vat': tax_revenue, 'shipments_per_retained': shipments,
            'rejects_per_retained': rejects, 'returns_per_retained': returns,
            'costs': costs, 'total_costs': sum(costs.values()), 'profit': profit,
            'margin_pct': profit / price * 100 if price else None,
            'roi_pct': profit / (costs['cogs'] + costs['loss']) * 100 if costs['cogs'] + costs['loss'] else None}


def solve_price(p, margin):
    """Find first feasible kopeck across all discontinuous commission intervals."""
    if margin >= 100 or p['buyout_pct'] == 0 or p['return_pct'] == 100:
        return None
    boundaries = [D(0)] + [decimal(t['up_to']) for t in p['commission_tiers']] + [D('1000000000')]
    for idx in range(len(boundaries) - 1):
        lo = int(boundaries[idx] * 100) + 1
        hi = int(boundaries[idx + 1] * 100)
        def meets(price):
            r = _evaluate(p, price)
            return r['status'] == 'calculated' and r['profit'] >= price * margin / 100
        if lo > hi:
            continue
        if meets(D(lo) / 100):
            return D(lo) / 100
        if not meets(D(hi) / 100):
            continue
        while lo < hi:
            mid = (lo + hi) // 2
            if meets(D(mid) / 100): hi = mid
            else: lo = mid + 1
        return D(hi) / 100
    return None


def json_numbers(value):
    if isinstance(value, D): return float(value)
    if isinstance(value, dict): return {k: json_numbers(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [json_numbers(v) for v in value]
    return value


def safe_discount(p, margin):
    """Largest contiguous price reduction that never crosses an infeasible kopeck."""
    def meets(cents):
        price = D(cents) / 100
        return _evaluate(p, price)['profit'] >= price * margin / 100
    high = int(p['price'] * 100)
    if high < 1 or not meets(high):
        return D(0)
    cuts = sorted({0, *[int(decimal(t['up_to']) * 100) for t in p['commission_tiers'] if decimal(t['up_to']) < p['price']]}, reverse=True)
    for cut in cuts:
        low = cut + 1
        if not meets(high):
            return (1 - D(high + 1) / 100 / p['price']) * 100
        if not meets(low):
            while low < high:
                mid = (low + high) // 2
                if meets(mid): high = mid
                else: low = mid + 1
            return (1 - D(high) / 100 / p['price']) * 100
        high = cut
    return (1 - D('.01') / p['price']) * 100


def calculate(inputs):
    p = validate(inputs)
    missing = [k for k in FIELDS if p[k] is None]
    if missing:
        return {'status': 'partial', 'missing': missing, 'missing_labels': [FIELDS[k][0] for k in missing], 'version': VERSION}
    if p['price'] <= 0:
        return {'status': 'impossible', 'reason': 'Цена должна быть больше нуля', 'version': VERSION}
    if p['mrc_margin_pct'] > p['rrc_margin_pct']:
        raise ValueError('Маржа РРЦ должна быть не ниже маржи МРЦ')
    current = _evaluate(p, p['price'])
    if current['status'] != 'calculated': return {**current, 'version': VERSION}
    mrc, rrc = solve_price(p, p['mrc_margin_pct']), solve_price(p, p['rrc_margin_pct'])
    breakeven = solve_price(p, D(0))
    sensitivity=[]
    for label,key,factor in [('Себестоимость +10%', 'cogs', D('1.1')),
                             ('Себестоимость +20%', 'cogs', D('1.2')),
                             ('Себестоимость −10%', 'cogs', D('.9')),
                             ('Цена −10%', 'price', D('.9')),
                             ('Цена +10%', 'price', D('1.1')),
                             ('Прямая доставка +20%', 'forward', D('1.2')),
                             ('Фулфилмент +20%', 'fulfillment', D('1.2')),
                             ('Упаковка +20%', 'packaging', D('1.2')),
                             ('Реклама +20%', 'ad_pct', D('1.2'))]:
        v={**p,key:min(D(100),p[key]*factor) if key.endswith('_pct') else p[key]*factor}
        evaluated=_evaluate(v,v['price'])
        requested=p[key]*factor
        sensitivity.append({'name':label,'changed_field':key,'changed_value':v[key],
          'before_value':p[key],'requested_value':requested,'limited':requested!=v[key],
          'limit_note':'Применена граница модели 100%; это меньше запрошенного изменения.' if requested!=v[key] else '',
          'profit':evaluated['profit'],'margin_pct':evaluated['margin_pct'],
          'delta_profit':evaluated['profit']-current['profit'],'rrc':solve_price(v,v['rrc_margin_pct'])})
    for label,key,delta in [('Выкуп −10 п.п.','buyout_pct',D(-10)),('Возвраты +5 п.п.','return_pct',D(5)),('Комиссия +3 п.п.','commission_pct',D(3))]:
        v={**p,key:max(D(0),min(D(100),p[key]+delta))}
        if key=='commission_pct':v['commission_tiers']=[{**t,'pct':min(D(100),decimal(t['pct'])+delta)} for t in p['commission_tiers']]
        evaluated=_evaluate(v,v['price'])
        requested=p[key]+delta
        tier_limited=key=='commission_pct' and any(decimal(t['pct'])+delta>100 for t in p['commission_tiers'])
        sensitivity.append({'name':label,'changed_field':key,'changed_value':v[key],
          'before_value':p[key],'requested_value':requested,'limited':requested!=v[key] or tier_limited,
          'changed_tiers':v['commission_tiers'] if key=='commission_pct' else [],
          'limit_note':'Изменение ограничено диапазоном модели 0–100%, включая тарифные ступени.' if requested!=v[key] or tier_limited else '',
          'profit':evaluated.get('profit'),'margin_pct':evaluated.get('margin_pct'),
          'delta_profit':evaluated['profit']-current['profit'] if 'profit' in evaluated else None,
          'rrc':solve_price(v,v['rrc_margin_pct'])})
    target=decimal(inputs.get('retail_target'))
    if target is not None and target<=0:raise ValueError('Целевая розничная цена должна быть больше нуля')
    retail_target=None
    if target is not None:
        retail_target=(_evaluate(p,(target/(1-p['platform_discount_pct']/100)).quantize(D('.01'),rounding=ROUND_CEILING)) if p['platform_discount_pct']<100
                       else {'status':'impossible','reason':'При скидке площадки 100% положительная цена покупателя недостижима'})
    promo = _evaluate(p, p['price'] * (1 - p['promo_discount_pct'] / 100))
    promo['volume_ratio'] = 1 + p['promo_uplift_pct'] / 100
    promo['profit_change_per_baseline_unit'] = promo['profit'] * promo['volume_ratio'] - current['profit']
    promo['required_uplift_pct'] = max(D(0), (current['profit'] / promo['profit'] - 1) * 100) if promo['profit'] > 0 and current['profit'] > 0 else None
    # Tax may depend on advertising, so solve DRR using the same model.
    lo, hi = D(0), D(100)
    if _evaluate({**p, 'ad_pct': lo}, p['price'])['profit'] < 0:
        max_ad = None
    else:
        for _ in range(50):
            mid = (lo + hi) / 2
            if _evaluate({**p, 'ad_pct': mid}, p['price'])['profit'] >= 0: lo = mid
            else: hi = mid
        max_ad = lo
    return json_numbers({'status': 'calculated', 'version': VERSION, 'current': current,
        'mrc': mrc, 'rrc': rrc, 'breakeven': breakeven, 'promo': promo, 'max_ad_pct': max_ad,
        'sensitivity':sensitivity,'retail_target':retail_target,
        'max_discount_pct': safe_discount(p, p['mrc_margin_pct']),
        'inputs': p, 'notice': 'Сценарий на одну сохранённую продажу. Налоговая модель требует проверки режима и допустимых вычетов; стоимость капитала — управленческий расход.'})
