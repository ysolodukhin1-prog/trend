"""Immutable plan versions and physically constrained sales, separate from demand."""
import calendar
import json
import math
import uuid
from datetime import date,timedelta


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_sales_plan_versions (
          version_id text PRIMARY KEY,marketplace text NOT NULL,name text NOT NULL,status text NOT NULL,
          months jsonb NOT NULL,assumptions jsonb NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now())""")


def versions(config,marketplace):
    from km_trade_finance import connect_km
    with connect_km(config) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.pulse_sales_plan_versions') AS name")
            if not cur.fetchone()['name']:
                return []
            cur.execute("""SELECT version_id,name,status,months,assumptions,created_at::text
              FROM public.pulse_sales_plan_versions WHERE marketplace=%s ORDER BY created_at DESC LIMIT 100""",(marketplace,))
            return [dict(x) for x in cur.fetchall()]


def save(config,payload):
    from km_trade_finance import connect_km
    market=str(payload.get('marketplace') or ''); status=payload.get('status','draft')
    name=str(payload.get('name') or '').strip()[:150]; months=payload.get('months')
    if market not in {'ozon','wb'} or status not in {'draft','review','approved'} or not name or not isinstance(months,list) or not 1<=len(months)<=12:
        raise ValueError('Нужны площадка, имя версии, статус и 1–12 месяцев')
    from sales_planning_workbench import validate_snapshot
    validated = validate_snapshot(payload)
    if validated:
        snapshot, months = validated
    clean=[]; seen=set()
    for row in months:
        start=date.fromisoformat(str(row['month_start'])); units=float(row['plan_units']); revenue=float(row['plan_revenue'])
        if start.day!=1 or start in seen or not all(math.isfinite(x) and x>=0 for x in (units,revenue)):
            raise ValueError('Месяцы должны быть уникальны, план неотрицателен')
        seen.add(start); clean.append({'month_start':str(start),'plan_units':units,'plan_revenue':revenue})
    assumptions=payload.get('assumptions') or {}
    allowed={key:assumptions[key] for key in ('growth_pct','trend_weight_pct','seasonality_pct','safety_stock_days') if key in assumptions}
    if validated:
        from urllib.parse import urlencode
        from km_trade_sales_planning import sales_forecast_payload
        query = urlencode({'client': payload.get('client', ''), 'marketplace': market, 'horizon': 'rolling',
                           **allowed, 'coefficient_overrides': json.dumps(snapshot['coefficient_overrides'])})
        model = sales_forecast_payload(config, query, include_all_products=True)
        if snapshot.get('source_to') != model['period']['available_to']:
            raise ValueError('Источник обновился. Пересчитайте сценарий перед сохранением версии.')
        planned = {r['sku'] for r in snapshot['rows']}
        snapshot['model_snapshot'] = {'period': model['period'], 'metric_contract': model['metric_contract'],
            'coefficients': model['coefficients'], 'products': [
                {'sku': p['sku'], 'category_name': p['category_name'], 'price_rub': p['price_rub'],
                 'stock_snapshot_date': p.get('stock_snapshot_date'), 'monthly': p['monthly']}
                for p in model['products'] if str(p['sku']) in planned]}
        allowed['snapshot'] = snapshot
    identifier=str(uuid.uuid4())
    with connect_km(config) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if validated:
                cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('sales-plan:'+market,))
                cur.execute("SELECT version_id FROM public.pulse_sales_plan_versions WHERE marketplace=%s AND assumptions->'snapshot'->>'metric'=%s ORDER BY created_at DESC LIMIT 1", (market, snapshot['metric']))
                latest = cur.fetchone()
                if (latest['version_id'] if latest else None) != snapshot.get('base_version_id'):
                    raise ValueError('Версия изменена другим пользователем. Обновите данные и сравните правки.')
                if status == 'approved' and market != 'wb':
                    raise ValueError('Для Ozon сначала требуется источник заказов вместо финансовых операций')
                if status == 'approved':
                    cur.execute("SELECT DISTINCT wb_nmid::text AS sku FROM public.wb_funnel_daily WHERE nullif(wb_nmid, '') IS NOT NULL UNION SELECT wb_nmid::text AS sku FROM public.wb_stock_api_current WHERE nullif(wb_nmid, '') IS NOT NULL")
                    known = {str(r['sku']) for r in cur.fetchall()}
                    planned = {r['sku'] for r in snapshot['rows']}
                    if not planned <= known or (snapshot['scope'] == 'portfolio' and planned != known):
                        raise ValueError('Состав товаров изменился или портфель сохранён не полностью. Обновите данные.')
            cur.execute('INSERT INTO public.pulse_sales_plan_versions(version_id,marketplace,name,status,months,assumptions,created_at) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,clock_timestamp())',
                        (identifier,market,name,status,json.dumps(clean),json.dumps(allowed)))
    return {'ok':True,'version_id':identifier,'status':status}


def apply_feasibility(products,months,as_of,supplies):
    """Daily consumption cannot spend an undated or suggested incoming quantity."""
    monthly={x['month_start']:x for x in months}
    for row in months:
        row['achievable_units']=0.; row['achievable_revenue']=0.; row['uncovered_demand_units']=0.; row['feasibility_missing_sku_count']=0
    by_sku={}
    for item in supplies:
        if item['status'] in {'confirmed','shipped'} and date.fromisoformat(item['eta'])>as_of:
            arrivals=by_sku.setdefault(item['sku'],{}); arrivals[item['eta']]=arrivals.get(item['eta'],0)+float(item['quantity'])
    next_month=(as_of.replace(day=28)+timedelta(days=4)).replace(day=1).isoformat()
    # Calendar dates are identical for all SKUs in a given month.
    calendar_days = {}
    for product in products:
        stock=max(float(product.get('stock_available_qty') or 0),0)
        arrivals=by_sku.get(str(product['sku']),{})
        snapshot = date.fromisoformat(product['stock_snapshot_date']) if product.get('stock_snapshot_date') else None
        stock_known = snapshot is not None and abs((as_of-snapshot).days) <= 2
        product['feasibility_quality'] = 'ready' if stock_known else 'missing_or_stale_stock'

        for row in product.get('monthly',[]):
            start=date.fromisoformat(row['month_start']); end=start.replace(day=calendar.monthrange(start.year,start.month)[1])
            if end >= as_of and not stock_known and float(row.get('forecast_units') or 0) > 0:
                row.update(achievable_units=None, achievable_revenue=None, uncovered_demand_units=None)
                if row['month_start'] in monthly:
                    monthly[row['month_start']]['feasibility_missing_sku_count'] += 1
                if row['month_start'] == next_month:
                    product['achievable_plan_units'] = None
                    product['achievable_plan_revenue'] = None
                continue
            if end<as_of:
                units=float(row.get('actual_units') or 0); revenue=float(row.get('actual_revenue') or 0); lost=0.
            else:
                fact=float(row.get('actual_units') or 0) if start<=as_of else 0
                demand=max(float(row.get('forecast_units') or 0)-fact,0)
                begin=max(start,as_of+timedelta(days=1)); days=max((end-begin).days+1,0)
                rate=demand/days if days else 0; sold=0.
                period_key = (begin, days)
                if period_key not in calendar_days:
                    calendar_days[period_key] = tuple(str(begin+timedelta(days=offset)) for offset in range(days))
                for day in calendar_days[period_key]:
                    stock+=arrivals.get(day,0)
                    daily=min(stock,rate); sold+=daily; stock-=daily
                units=fact+sold; lost=max(demand-sold,0)
                revenue=float(row.get('actual_revenue') or 0) if start<=as_of else 0
                revenue+=sold*float(product.get('price_rub') or 0)
            row.update(achievable_units=round(units,2),achievable_revenue=round(revenue,2),uncovered_demand_units=round(lost,2))
            if row['month_start'] in monthly:
                target=monthly[row['month_start']]
                for key in ('achievable_units','achievable_revenue','uncovered_demand_units'): target[key]+=row[key]
            if row['month_start']==next_month:
                product['achievable_plan_units']=row['achievable_units']; product['achievable_plan_revenue']=row['achievable_revenue']
        for row in product.get('monthly', []):
            if row.get('actual_source_available') is False and date.fromisoformat(row['month_start']) < as_of.replace(day=1):
                for key in ('achievable_units','achievable_revenue','uncovered_demand_units'): row[key]=None
        product['estimated_cogs_per_unit']=round(float(product.get('price_rub') or 0)/3,2)
    for row in months:
        if row['feasibility_missing_sku_count']:
            row['known_achievable_units'] = row['achievable_units']
            row['known_achievable_revenue'] = row['achievable_revenue']
            for key in ('achievable_units','achievable_revenue','uncovered_demand_units','supply_readiness_pct'): row[key]=None
            continue
        if row.get('actual_source_available') is False and date.fromisoformat(row['month_start']) < as_of.replace(day=1):
            for key in ('achievable_units','achievable_revenue','uncovered_demand_units','supply_readiness_pct'): row[key]=None
            continue
        demand=float(row.get('forecast_units') or 0)
        row['supply_readiness_pct']=min(100,row['achievable_units']/demand*100) if demand else None
