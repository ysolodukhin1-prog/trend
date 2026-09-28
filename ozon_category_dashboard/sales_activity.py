"""Audited monthly marketing assumptions, independent of commercial plan versions."""
import math
import uuid
from datetime import date


def validate(payload):
    scope = payload.get('scope')
    entity = str(payload.get('entity') or '').strip()
    rows = payload.get('months')
    reason = str(payload.get('reason') or '').strip()
    if payload.get('marketplace') != 'wb' or scope not in {'category', 'sku'} or not entity or len(entity) > 250:
        raise ValueError('Укажите WB и категорию или SKU')
    if not reason or len(reason) > 1000 or not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise ValueError('Нужны основание и от 1 до 12 месяцев')
    clean, seen = [], set()
    for row in rows:
        month = date.fromisoformat(str(row['month_start']))
        value = row.get('value')
        if month.day != 1 or month in seen:
            raise ValueError('Месяцы должны быть уникальны и начинаться с первого числа')
        if value is not None:
            if isinstance(value, bool):
                raise ValueError('К активности должен быть числом от 0 до 10')
            value = float(value)
            if not math.isfinite(value) or not 0 <= value <= 10:
                raise ValueError('К активности должен быть числом от 0 до 10')
        seen.add(month)
        clean.append(dict(month_start=str(month), value=value, revision=row.get('revision')))
    return scope, entity, reason, clean


def read_settings(conn, marketplace):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.pulse_sales_activity_revisions') AS name")
        if not cur.fetchone()['name']:
            return []
        cur.execute("""SELECT DISTINCT ON (scope,entity,month_start)
            scope,entity,month_start::text,value,reason,revision,created_at::text
            FROM public.pulse_sales_activity_revisions WHERE marketplace=%s
            ORDER BY scope,entity,month_start,created_at DESC,revision DESC""", (marketplace,))
        return [dict(row) for row in cur.fetchall()]


def merge_overrides(overrides, settings):
    """A NULL revision removes even an old URL/snapshot override; SKU priority remains intact."""
    result = {(r['scope'], r['entity'], r['metric'], r['month_start']): dict(r) for r in overrides}
    for row in settings:
        key = (row['scope'], row['entity'], 'activity', row['month_start'])
        result.pop(key, None)
        if row['value'] is not None:
            result[key] = dict(scope=row['scope'], entity=row['entity'], metric='activity',
                               month_start=row['month_start'], value=float(row['value']), reason=row['reason'])
    return list(result.values())


def save(config, payload):
    from km_trade_finance import connect_km
    scope, entity, reason, rows = validate(payload)
    with connect_km(config) as conn:
        with conn.cursor() as cur:
            if scope == 'sku':
                cur.execute("""SELECT wb_nmid FROM public.wb_funnel_daily WHERE wb_nmid::text=%s
                    UNION SELECT wb_nmid FROM public.wb_stock_api_current WHERE wb_nmid::text=%s LIMIT 1""", (entity, entity))
            else:
                cur.execute("""SELECT 1 FROM public.wb_funnel_daily
                    WHERE coalesce(nullif(category_name,''),'Без категории')=%s LIMIT 1""", (entity,))
            if not cur.fetchone():
                raise ValueError('Категория или SKU отсутствует в данных выбранного клиента')
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('sales-activity:wb'))")
            cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_sales_activity_revisions (
                revision text PRIMARY KEY, marketplace text NOT NULL, scope text NOT NULL,
                entity text NOT NULL, month_start date NOT NULL, value double precision,
                reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
            current = {(r['scope'], r['entity'], r['month_start']): r for r in read_settings(conn, 'wb')}
            for row in rows:
                previous = current.get((scope, entity, row['month_start']), {})
                if row['revision'] != previous.get('revision'):
                    raise ValueError('Активность изменена другим пользователем. Обновите данные перед сохранением.')
            for row in rows:
                cur.execute("""INSERT INTO public.pulse_sales_activity_revisions
                    (revision,marketplace,scope,entity,month_start,value,reason)
                    VALUES(%s,'wb',%s,%s,%s,%s,%s)""",
                    (str(uuid.uuid4()), scope, entity, row['month_start'], row['value'], reason))
        settings = read_settings(conn, 'wb')
    return {'ok': True, 'activity_settings': settings, 'saved_months': len(rows)}
