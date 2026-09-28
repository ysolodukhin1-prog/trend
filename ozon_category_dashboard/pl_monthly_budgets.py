"""Versioned monthly fixed-cost budgets, isolated in each client's database."""
import calendar
import hashlib
from contextlib import closing
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from km_trade_finance import connect_km

MARKETS = ('wb', 'ozon', 'yandex')
TABLE = 'public.pl_monthly_budget_versions'
MODES = ('calendar',)


def allocation_window(start, mode, as_of=None):
    """Monthly budgets are spread over every calendar day, including leap days."""
    if mode not in MODES:
        raise ValueError('Выбери способ распределения бюджета')
    as_of = as_of or date.today()
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    days = max(0, (end-start).days+1)
    return dict(allocation_days=days, allocation_end=str(end) if days else None,
                allocation_mode=mode, allocation_as_of=str(as_of))


def allocated_days(amount, start, mode, as_of=None):
    """Cumulative cent rounding makes subranges additive and preserves the month total."""
    count = allocation_window(start, mode, as_of)['allocation_days']
    amount = Decimal(str(amount))
    previous = Decimal(0)
    for i in range(1, count+1):
        cumulative = (amount*i/count).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        yield str(start+timedelta(days=i-1)), cumulative-previous
        previous = cumulative


def read_range(config, client, start, end):
    check_client(config, client)
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        if not present(cur): return []
        cur.execute(f'''SELECT DISTINCT ON (month) month,revision,rows,allocation_mode
            FROM {TABLE} WHERE month BETWEEN %s AND %s ORDER BY month,revision DESC''',
            (start.replace(day=1), end.replace(day=1)))
        return [dict(r) for r in cur.fetchall()]


def apply_budgets(daily, articles, records, market, as_of=None):
    """Add budget articles without marking missing marketplace operations as observed."""
    metadata = []
    for record in records:
        start = record['month']; mode = record['allocation_mode']
        window = allocation_window(start, mode, as_of)
        metadata.append(dict(month=str(start)[:7], revision=record['revision'], **window))
        for row in record['rows']:
            amount = row['amounts'].get(market)
            if amount is None: continue
            key = 'budget_'+hashlib.sha1(row['expense_name'].strip().casefold().encode()).hexdigest()[:16]
            articles.setdefault(key, dict(key=key, label=row['expense_name'], group='fixed',
                unit='money', kind='article', help='Месячный бюджет. Распределение по дням отдельно для каждого месяца.'))
            for dt, value in allocated_days(amount, start, mode, as_of):
                if dt in daily: daily[dt][key] = Decimal(str(daily[dt].get(key) or 0))-value
    return metadata


def budget_only(config, payload):
    """Keep recorded fixed costs visible even when marketplace operations are unavailable."""
    from pl_multi_model import pack
    from pl_financial_model import tax_profile, finalize
    start, end = map(date.fromisoformat, (payload['date_from'], payload['date_to']))
    records = read_range(config, payload['client'], start, end)
    market = payload['marketplace']
    if not any(r['amounts'].get(market) is not None for b in records for r in b['rows']):
        return payload
    daily = {str(start+timedelta(days=i)): dict(date=str(start+timedelta(days=i)),
        revenue=None, known_cogs=Decimal(0), finance_present=False, missing_cost_units=0, sku_cost_units=0)
        for i in range((end-start).days+1)}
    articles = {}
    budgets = apply_budgets(daily, articles, records, market)
    tax = tax_profile({})
    days = finalize(list(daily.values()), list(articles.values()), tax)
    for row in days:
        row['ebitda'] = row['net_profit'] = None
    payload['financial_model'] = pack(days, list(articles.values()), tax,
        marketplace=market, label={'wb':'WB','ozon':'Ozon','yandex':'Яндекс Маркет'}[market],
        monthly_budgets=budgets, registry_barcodes=0,
        source_note='Показан бюджет постоянных расходов. Финансовые операции площадки недоступны; прибыль не рассчитана.')
    return payload


def sync_statement(payload):
    model=payload.get('financial_model')
    if not model: return payload
    from unit_result_contract import financial_model_contract
    model['calculation_status']=financial_model_contract(model)
    for row in model.get('rows',[]):
        if row['key']=='net_profit':row['label']='Расчётная прибыль'
    values=model['total']['values'];amount=values.get('fixed')
    statement=[r for r in payload.get('statement',[]) if r['key']!='monthly_fixed_expenses']
    index=next((i for i,r in enumerate(statement) if r['key'] in ('vat_model','vat','tax','management_result')),len(statement))
    statement.insert(index,dict(key='monthly_fixed_expenses',label='Постоянные расходы',kind='expense',
        amount=amount,revenue_pct=amount/values['revenue']*100 if amount is not None and values.get('revenue') else None))
    payload['statement']=statement
    return payload


def month_start(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('Выбери месяц бюджета')
    try:
        return date.fromisoformat(value + '-01')
    except ValueError:
        raise ValueError('Некорректный месяц бюджета') from None


def check_client(config, client):
    if not client or config.get('database') != client:
        raise ValueError('Клиент и база бюджета не совпадают')


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('Допустимо до 200 статей бюджета')
    result, ids, names = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Некорректная статья бюджета')
        name = str(row.get('expense_name') or '').strip()
        notes = str(row.get('notes') or '').strip()
        if not name or len(name) > 300 or len(notes) > 1000:
            raise ValueError('Заполни название статьи (до 300 символов) и комментарий (до 1000)')
        try:
            key = str(UUID(str(row.get('id'))))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Некорректный идентификатор статьи') from None
        if key in ids or name.casefold() in names:
            raise ValueError('Статьи бюджета не должны повторяться')
        ids.add(key); names.add(name.casefold())
        amounts = row.get('amounts')
        if not isinstance(amounts, dict) or set(amounts) - set(MARKETS):
            raise ValueError('Некорректные площадки бюджета')
        normalized = {}
        for market in MARKETS:
            raw = amounts.get(market)
            if raw is None or raw == '':
                normalized[market] = None
                continue
            try:
                if isinstance(raw, bool): raise InvalidOperation
                amount = Decimal(str(raw).replace(',', '.'))
            except (InvalidOperation, ValueError):
                raise ValueError('Бюджет должен быть числом') from None
            if not amount.is_finite() or amount < 0 or amount > Decimal('1000000000000'):
                raise ValueError('Бюджет должен быть от 0 до 1 трлн ₽')
            if amount != amount.quantize(Decimal('.01')):
                raise ValueError('Укажи бюджет с точностью до копейки')
            normalized[market] = str(amount.quantize(Decimal('.01')))
        result.append(dict(id=key, expense_name=name, notes=notes, amounts=normalized))
    return result


def totals(rows):
    values = {m: sum((Decimal(r['amounts'][m]) for r in rows if r['amounts'].get(m) is not None), Decimal(0)) for m in MARKETS}
    return {**{m: float(v) for m, v in values.items()}, 'all': float(sum(values.values()))}


def present(cur):
    cur.execute("SELECT to_regclass('public.pl_monthly_budget_versions') present")
    return bool(cur.fetchone()['present'])


def load(config, client, month):
    check_client(config, client)
    start = month_start(month)
    record = None
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        if present(cur):
            cur.execute(f'SELECT revision,rows,allocation_mode,saved_at::text FROM {TABLE} WHERE month=%s ORDER BY revision DESC LIMIT 1', (start,))
            record = cur.fetchone()
    rows = record['rows'] if record else []
    return dict(month=month, revision=record['revision'] if record else 0, rows=rows,
                saved_at=record['saved_at'] if record else None, totals=totals(rows),
                calendar_days=calendar.monthrange(start.year, start.month)[1],
                **(allocation_window(start, record['allocation_mode']) if record else dict(allocation_mode=None)))


def save(config, client, payload):
    check_client(config, client)
    start = month_start(payload.get('month'))
    rows = validate_rows(payload.get('rows'))
    mode = payload.get('allocation_mode', 'calendar')
    allocation_window(start, mode)
    revision = payload.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError('Некорректная версия бюджета')
    with closing(connect_km(config)) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('pl_monthly_budget_versions'))")
        cur.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE} (
            month date NOT NULL CHECK (extract(day from month)=1),
            revision integer NOT NULL CHECK (revision>0), rows jsonb NOT NULL,
            allocation_mode text NOT NULL CHECK (allocation_mode IN ('calendar')),
            saved_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(month, revision))''')
        cur.execute(f'SELECT revision,rows,allocation_mode FROM {TABLE} WHERE month=%s ORDER BY revision DESC LIMIT 1', (start,))
        previous = cur.fetchone()
        current = previous['revision'] if previous else 0
        if revision != current:
            raise ValueError('Бюджет изменён в другой вкладке. Загрузи сохранённую версию перед сохранением.')
        if not previous or previous['rows'] != rows or previous['allocation_mode'] != mode:
            cur.execute(f'INSERT INTO {TABLE}(month,revision,rows,allocation_mode) VALUES (%s,%s,%s::jsonb,%s)',
                        (start, current + 1, json.dumps(rows, ensure_ascii=False, allow_nan=False), mode))
    return load(config, client, payload['month'])
