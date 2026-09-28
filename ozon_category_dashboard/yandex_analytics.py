"""Typed Yandex analytical views, coverage and a client-scoped read API.

Run --install to migrate the selected client databases and register store dimensions.
No API calls or marketplace mutations occur in this module.
"""
from __future__ import annotations
import argparse
import json
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
import psycopg2
from psycopg2.extras import RealDictCursor,execute_values

ROOT=Path(__file__).resolve().parent

def install(conn,client):
    with conn.cursor() as c:
        c.execute((ROOT/'yandex_analytics.sql').read_text(encoding='utf-8'))
        rows=[]
        for a in client.get('marketplace_accounts',{}).get('yandex_market',[]):
            for s in a.get('stores',[]):
                rows.append((client['key'],str(a['business_id']),str(s['campaign_id']),s.get('name'),s.get('placement_type'),bool(s.get('is_accessible')),bool(s.get('import_enabled'))))
        if rows:execute_values(c,'INSERT INTO yandex_dim_store(client_key,business_id,campaign_id,store_name,placement_type,is_accessible,import_enabled) VALUES %s ON CONFLICT(client_key,business_id,campaign_id) DO UPDATE SET store_name=excluded.store_name,placement_type=excluded.placement_type,is_accessible=excluded.is_accessible,import_enabled=excluded.import_enabled,updated_at=now()',rows)
    conn.commit()
    refresh_marts(conn)

def source_version(conn):
    with conn.cursor() as c:
        c.execute("SELECT md5(COALESCE(max(finished_at)::text,'')||':'||count(*)::text||':'||COALESCE((SELECT max(synced_at)::text FROM yandex_market_entities),'')||':'||COALESCE((SELECT max(updated_at)::text FROM yandex_dim_store),'')) FROM yandex_analytics_jobs WHERE state='completed'")
        row=c.fetchone()
        return next(iter(row.values())) if isinstance(row,dict) else row[0]

def refresh_marts(conn):
    version=source_version(conn)
    with conn.cursor() as c:
        c.execute('REFRESH MATERIALIZED VIEW yandex_mart_funnel_daily; REFRESH MATERIALIZED VIEW yandex_mart_quality')
        c.execute("INSERT INTO yandex_analytics_mart_state(mart_name,source_version) VALUES('summary',%s) ON CONFLICT(mart_name) DO UPDATE SET source_version=EXCLUDED.source_version,refreshed_at=now()",(version,))
    conn.commit()

def jsonable(value):
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,Decimal):return float(value)
    if isinstance(value,dict):return {k:jsonable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [jsonable(v) for v in value]
    return value

def summary(conn,client_key,start,end,store=None):
    """Never infer a store for business-grain facts; coverage is returned with metrics."""
    params=[client_key,start,end]
    scope='client_key=%s AND {day} BETWEEN %s AND %s'
    if store:scope+=' AND campaign_id=%s';params.append(store)
    with conn.cursor(cursor_factory=RealDictCursor) as c:
        c.execute("SELECT to_regclass('public.yandex_fact_orders') IS NOT NULL AS ready")
        if not c.fetchone()['ready']:return {'ok':True,'status':'unavailable','reason':'Yandex analytical layer is not installed','client':client_key}
        c.execute('SELECT * FROM yandex_dim_store WHERE client_key=%s ORDER BY campaign_id',(client_key,));stores=c.fetchall()
        if store and not any(s['campaign_id']==store for s in stores):raise ValueError('Unknown Yandex store for this client')
        result={'ok':True,'client':client_key,'date_from':start,'date_to':end,'store':store,'stores':stores,'status_scope':'client_dataset','quality_scope':'client_dataset'}
        version=source_version(conn)
        c.execute("SELECT source_version,refreshed_at FROM yandex_analytics_mart_state WHERE mart_name='summary'")
        cache=c.fetchone();cached=bool(cache and cache['source_version']==version)
        result['aggregate_cache_current']=cached
        for key,view,day in [
            ('orders_daily','yandex_orders_daily','order_date'),
            ('funnel_daily','yandex_funnel_daily','metric_date'),
            ('services_daily','yandex_services_daily','service_date'),
            ('transactions_daily','yandex_transactions_daily','transaction_date')]:
            if view=='yandex_funnel_daily' and cached:view='yandex_mart_funnel_daily'
            c.execute(f'SELECT * FROM {view} WHERE '+scope.format(day=day)+f' ORDER BY {day},campaign_id',params)
            result[key]=c.fetchall()
        c.execute('SELECT campaign_id,offer_id,currency,sum(ordered_units) ordered_units,sum(buyer_payment) buyer_payment,sum(delivered_units_current_status) delivered_units_current_status FROM yandex_sku_orders_daily WHERE '+scope.format(day='order_date')+' GROUP BY 1,2,3 ORDER BY sum(buyer_payment) DESC NULLS LAST,offer_id LIMIT 100',params)
        result['top_sku_by_buyer_payment']=c.fetchall()
        c.execute('SELECT campaign_id,return_date,return_type,count(*) returns,sum(amount) amount FROM yandex_fact_returns WHERE '+scope.format(day='return_date')+' GROUP BY 1,2,3 ORDER BY 2,1',params)
        result['returns_daily']=c.fetchall()
        c.execute('SELECT campaign_id,snapshot_date,warehouse,count(*) sku_warehouse_rows,sum(available_for_order) available_for_order,sum(reserved) reserved,count(available_for_order) rows_with_available_stock FROM yandex_fact_stocks WHERE client_key=%s AND snapshot_date<=%s'+(' AND campaign_id=%s' if store else '')+' GROUP BY 1,2,3 ORDER BY 2 DESC,1,3',[client_key,end]+([store] if store else []))
        result['stock_snapshots']=c.fetchall()
        c.execute('SELECT campaign_id,date_from,date_to,event_type,sum(units) units,sum(amount) amount,count(*) source_rows,count(amount) priced_rows FROM yandex_fact_realization WHERE client_key=%s AND date_from<=%s AND date_to>=%s'+(' AND campaign_id=%s' if store else '')+' GROUP BY 1,2,3,4 ORDER BY 2,1',[client_key,end,start]+([store] if store else []))
        result['realization_periods']=c.fetchall()
        if store:
            result['marketing_daily']=None
            result['boost_sales_periods']=None
        else:
            c.execute('SELECT * FROM yandex_marketing_daily WHERE client_key=%s AND metric_date BETWEEN %s AND %s ORDER BY metric_date,source_key',(client_key,start,end))
            result['marketing_daily']=c.fetchall()
            c.execute('SELECT business_id,date_from,date_to,sum(attributed_units) attributed_units,sum(attributed_delivered_amount) attributed_delivered_amount,sum(billed_amount) billed_amount FROM yandex_fact_boost_sales_period WHERE client_key=%s AND date_from<=%s AND date_to>=%s GROUP BY 1,2,3',(client_key,end,start))
            result['boost_sales_periods']=c.fetchall()
        c.execute('SELECT * FROM yandex_data_coverage WHERE client_key=%s ORDER BY source_key,campaign_id,actual_date_from',(client_key,))
        result['coverage']=c.fetchall()
        c.execute('SELECT * FROM '+('yandex_mart_quality' if cached else 'yandex_analytics_quality')+' WHERE client_key=%s',(client_key,))
        result['quality']=c.fetchall()
        states=[r['coverage_status'] for r in result['coverage']]
        result['status']='partial' if any(s not in ('completed','empty') for s in states) or any(r['invalid_keys'] or r['missing_main_metric'] for r in result['quality']) else ('available' if states else 'unavailable')
        result['definitions']={
            'orders_daily':'Date of order creation; delivered/cancelled counts use current status, excluding test orders.',
            'buyer_payment':'Total buyer payment for all units; excludes separately reported subsidy and Plus cashback.',
            'services_daily':'Billed services; incomplete priced_rows means amount coverage is partial. Do not add order commissions.',
            'transactions_daily':'Separate settlement types and statuses; not revenue or profit.',
            'store_filter':'Business/unresolved expenses and marketing are not allocated to stores.',
            'funnel_daily':'Reported daily SKU metrics; ratios are recomputed from sums, missing and zero denominator remain null.',
            'profit':'Unavailable until complete cost of goods, tax and off-platform cost coverage is validated.'}
        return jsonable(result)

def handle(app,parsed):
    q=parse_qs(parsed.query)
    if q.get('mode')==['progress']:
        with app.get_conn() as conn,conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute("SELECT to_regclass('public.yandex_data_coverage') IS NOT NULL AS ready")
            if not c.fetchone()['ready']:return {'ok':True,'status':'unavailable','jobs':[]}
            c.execute('SELECT * FROM yandex_data_coverage WHERE client_key=%s ORDER BY source_key,campaign_id,actual_date_from',(app.current_client_key(),))
            jobs=c.fetchall();done=sum(j['state']=='completed' for j in jobs)
            return jsonable({'ok':True,'client':app.current_client_key(),'completed':done,'total':len(jobs),'percent':round(100*done/len(jobs),1) if jobs else None,'rows':sum(j['rows_count'] for j in jobs),'jobs':jobs})
    start=date.fromisoformat(q.get('date_from',['2026-05-01'])[0])
    end=date.fromisoformat(q.get('date_to',[date.today().isoformat()])[0])
    if start>end or (end-start).days>400:raise ValueError('Date range must be 0..400 days')
    store=q.get('campaign_id',[None])[0]
    if store and not store.isdigit():raise ValueError('campaign_id must be numeric')
    with app.get_conn() as conn:return summary(conn,app.current_client_key(),start,end,store)

def main():
    import app,client_registry
    p=argparse.ArgumentParser();p.add_argument('--clients',nargs='+',required=True);p.add_argument('--install',action='store_true')
    args=p.parse_args()
    with app.client_registry_connection() as c:clients=client_registry.list_clients(c)
    for key in args.clients:
        client=next(c for c in clients if c['key']==key)
        cfg=dict(app.read_db_config());cfg['database']=client['db_name']
        with psycopg2.connect(**cfg) as conn:
            if args.install:install(conn,client)
            data=summary(conn,key,date(2026,5,1),date.today())
            print(json.dumps({'client':key,'status':data['status'],'stores':len(data.get('stores',[])),**{k:len(data.get(k,[])) for k in ('orders_daily','funnel_daily','services_daily','transactions_daily','coverage')}},ensure_ascii=False))

if __name__=='__main__':main()
