"""Typed, whitelisted column queries. Missing values stay distinct from zero."""
import json
from decimal import Decimal, InvalidOperation

TEXT_OPS = {'contains', 'not_contains', 'eq', 'neq', 'with', 'without'}
NUMBER_OPS = {'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'between', 'with', 'without'}


def number(value):
    try:
        result = Decimal(str(value).replace('\u00a0', '').replace(' ', '').replace(',', '.'))
        if result.is_finite():
            return result
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise ValueError('Введите корректное число в фильтре столбца')


def parse(get, columns, default, direction='desc'):
    key = get('sort_col') or default
    order = get('sort_dir') or direction
    if key not in columns or order not in ('asc', 'desc'):
        raise ValueError('Неизвестный столбец или направление сортировки')
    raw = get('column_filters') or '{}'
    if len(raw) > 16000:
        raise ValueError('Слишком много условий фильтрации')
    try:
        filters = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError('Некорректный фильтр столбца') from None
    if not isinstance(filters, dict) or len(filters) > 160:
        raise ValueError('Некорректный фильтр столбца')
    validated = {}
    for column, spec in filters.items():
        if column not in columns or not isinstance(spec, dict):
            raise ValueError('Неизвестный столбец фильтра')
        kind = columns[column][0]
        op = spec.get('op')
        if op not in (NUMBER_OPS if kind in ('number', 'date') else TEXT_OPS):
            raise ValueError('Некорректное условие фильтра')
        value, upper = str(spec.get('value', '')).strip(), str(spec.get('upper', '')).strip()
        if len(value) > 300 or len(upper) > 300:
            raise ValueError('Слишком длинное значение фильтра')
        if op not in ('with', 'without'):
            if not value or (op == 'between' and not upper):
                raise ValueError('Заполните значение фильтра')
            if kind == 'number':
                value = number(value)
                if op == 'between':
                    upper = number(upper)
            elif kind == 'date':
                from datetime import date
                try:
                    value = date.fromisoformat(value).isoformat()
                    if op == 'between':
                        upper = date.fromisoformat(upper).isoformat()
                except ValueError:
                    raise ValueError('Введите дату в фильтре') from None
            else:
                value = value.casefold()
            if op == 'between' and value > upper:
                raise ValueError('Начало диапазона больше окончания')
        validated[column] = {'op': op, 'value': value, 'upper': upper}
    return key, order, validated


def matches(value, spec, kind):
    missing = value is None or value == ''
    op, target = spec['op'], spec['value']
    if op == 'with':
        return not missing
    if op == 'without':
        return missing
    if missing:
        return False
    if kind == 'number':
        value = number(value)
    elif kind == 'date':
        value = str(value)[:10]
    else:
        value = str(value).casefold()
    if op == 'contains':
        return target in value
    if op == 'not_contains':
        return target not in value
    if op == 'between':
        return target <= value <= spec['upper']
    return {'eq': lambda: value == target, 'neq': lambda: value != target,
            'gt': lambda: value > target, 'gte': lambda: value >= target,
            'lt': lambda: value < target, 'lte': lambda: value <= target}[op]()


def apply(rows, columns, query):
    key, direction, filters = query
    selected = list(rows)
    for column, spec in filters.items():
        kind, getter = columns[column]
        selected = [row for row in selected if matches(getter(row), spec, kind)]
    kind, getter = columns[key]
    # Partition first: unknowns always last, in either direction. Stable ties retain
    # the source's deterministic identity order and remain stable across pages.
    known, missing = [], []
    for row in selected:
        value = getter(row)
        if value is None or value == '':
            missing.append(row)
        else:
            known.append((number(value) if kind == 'number' else str(value).casefold(), row))
    known.sort(key=lambda item: item[0], reverse=direction == 'desc')
    return [row for _, row in known] + missing


def product_columns(channels, warehouses):
    def preferred(row):
        links = row['links']
        return next((link for ch in ('ozon', 'wb') for link in links if link['channel'] == ch), links[0] if links else {})

    def name(row):
        link = preferred(row)
        data = link.get('data', {})
        return data.get('name') or data.get('article') or link.get('key') or 'Без наименования'

    columns = {
        'name': ('text', name),
        'barcode': ('text', lambda row: ' · '.join(dict.fromkeys(str(v) for l in row['links'] for v in l['data'].get('barcodes', []) if v))),
        'ids': ('number', lambda row: sum(l['channel'] in ('wb', 'ozon', 'yandex_market', 'lamoda') for l in row['links'])),
        'dimensions': ('text', lambda row: ' × '.join(str(row['dimensions'][key] / 10) for key in ('width_mm', 'length_mm', 'height_mm')) if all(row.get('dimensions', {}).get(key) for key in ('width_mm', 'length_mm', 'height_mm')) else None),
        'prices': ('number', lambda row: sum(row.get('prices', {}).get(ch, {}).get('value') is not None for ch in channels) or None),
        'abc': ('text', lambda row: row.get('abc')),
        'xyz': ('text', lambda row: row.get('xyz')),
    }
    for key in ('sales_total', 'units_total', 'stock_total'):
        columns[key] = ('number', lambda row, k=key: row.get(k))
    for key in ('width_mm', 'length_mm', 'height_mm', 'weight_g', 'litres'):
        factor = 10 if key.endswith('_mm') else 1
        columns[key] = ('number', lambda row, k=key, f=factor: row.get('dimensions', {}).get(k) / f if row.get('dimensions', {}).get(k) is not None else None)
    for ch in ('wb', 'ozon', 'yandex_market', 'lamoda'):
        columns['id_' + ch] = ('text', lambda row, ch=ch: ' · '.join(str(l['data'].get('parent') or '') if ch == 'wb' else str(l['data'].get('article') or l['key']) for l in row['links'] if l['channel'] == ch))
    for ch in channels:
        columns['sales_' + ch] = ('number', lambda row, ch=ch: row.get('metrics', {}).get(ch, {}).get('rub'))
        columns['price_' + ch] = ('number', lambda row, ch=ch: row.get('prices', {}).get(ch, {}).get('value'))
        columns['margin_' + ch] = ('number', lambda row, ch=ch: row.get('margins', {}).get('channels', {}).get(ch, {}).get('unit_profit'))

        def subtotal(row, ch=ch):
            values = [value.get('units') for key, value in row.get('stocks', {}).items()
                      if (key.split(':')[1] if key.startswith(('toptop:', 'lera_nena:')) else key.split(':')[0]) == ch and value.get('units') is not None]
            return sum(values) if values else None
        columns['stock_group_' + ch] = ('number', subtotal)
    for key in warehouses:
        columns['stock_' + key] = ('number', lambda row, k=key: row.get('stocks', {}).get(k, {}).get('units'))
    for key, field in [('unit_profit', 'unit_profit'), ('margin_total', 'rub'), ('margin_pct', 'pct')]:
        columns[key] = ('number', lambda row, k=field: row.get('margins', {}).get('total', {}).get(k))
    return columns


def order_sql(get, labels):
    columns = {key: (kind, None) for key, kind in [('ordered_at', 'date'), ('channel', 'text'), ('order_id', 'text'), ('product', 'text'), ('units', 'number'), ('amount', 'number'), ('status', 'text')]}
    key, direction, filters = parse(get, columns, 'ordered_at')
    label_args = list(labels.values())
    channel_expr = 'CASE channel ' + ' '.join("WHEN '" + ch + "' THEN %s" for ch in labels) + ' END'
    conditions, args = [], []
    for col, spec in filters.items():
        kind = columns[col][0]
        expr = channel_expr if col == 'channel' else "concat_ws(' ',product,article)" if col == 'product' else "(ordered_at AT TIME ZONE 'Europe/Moscow')::date" if col == 'ordered_at' else col
        if col == 'channel':
            args.extend(label_args)
        op = spec['op']
        if op in ('with', 'without'):
            missing = f"({expr} IS NULL OR {expr}::text = '')"
            # channel expression appears twice in this condition.
            if col == 'channel':
                args.extend(label_args)
            conditions.append(('NOT ' if op == 'with' else '') + missing)
        elif op in ('contains', 'not_contains'):
            conditions.append(expr + (" NOT ILIKE %s ESCAPE '\\'" if op == 'not_contains' else " ILIKE %s ESCAPE '\\'"))
            args.append('%' + spec['value'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        elif op == 'between':
            conditions.append(expr + ' BETWEEN %s AND %s')
            args.extend([spec['value'], spec['upper']])
        else:
            if kind == 'text':
                expr = 'lower(' + expr + ')'
            conditions.append(expr + ' ' + {'eq': '=', 'neq': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op] + ' %s')
            args.append(spec['value'])
    sort_expr = channel_expr if key == 'channel' else "concat_ws(' ',product,article)" if key == 'product' else key
    order_args = label_args if key == 'channel' else []
    return conditions, args, sort_expr + ' ' + direction.upper() + ' NULLS LAST,channel,key', order_args


def sql_filters(get, columns, default, expressions=None):
    """Compile only code-owned column expressions; every user value is a parameter."""
    typed = {col['key']: (col['type'], None) for col in columns}
    _, _, filters = parse(get, typed, default)
    expressions = expressions or {}
    clauses, args = [], []
    for key, spec in filters.items():
        kind = typed[key][0]
        expr = expressions.get(key, key)
        op, value = spec['op'], spec['value']
        if op in ('with', 'without'):
            clauses.append(('NOT ' if op == 'with' else '') + f"({expr} IS NULL OR {expr}::text = '')")
        elif op in ('contains', 'not_contains'):
            clauses.append(expr + (" NOT ILIKE %s ESCAPE '\\'" if op == 'not_contains' else " ILIKE %s ESCAPE '\\'"))
            args.append('%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        elif op == 'between':
            clauses.append(expr + ' BETWEEN %s AND %s')
            args.extend([value, spec['upper']])
        else:
            clauses.append(('lower(' + expr + ')' if kind == 'text' else expr) + ' ' + {'eq': '=', 'neq': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op] + ' %s')
            args.append(value)
    return ' AND '.join(clauses) or 'TRUE', args
