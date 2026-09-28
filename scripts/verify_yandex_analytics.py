"""Read-only reconciliation of live Yandex analytical data; aggregate evidence only."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ozon_category_dashboard'))
import app,client_registry,psycopg2
from yandex_analytics import jsonable

def verify(conn):
    checks={}
    with conn.cursor() as c:
        c.execute("""WITH i AS (SELECT client_key,business_id,campaign_id,order_id,sum(buyer_payment) payment FROM yandex_fact_order_items GROUP BY 1,2,3,4)
        SELECT count(*) FILTER(WHERE o.buyer_payment IS DISTINCT FROM i.payment) FROM yandex_fact_orders o JOIN i USING(client_key,business_id,campaign_id,order_id)""")
        checks['order_header_item_amount_mismatches']=c.fetchone()[0]
        c.execute("""SELECT count(*) FROM (SELECT client_key,business_id,campaign_id,metric_date,offer_id FROM yandex_fact_funnel GROUP BY 1,2,3,4,5 HAVING count(*)>1) d""")
        checks['duplicate_funnel_keys']=c.fetchone()[0]
        c.execute("""SELECT count(*) FROM yandex_market_entities stats LEFT JOIN yandex_market_entities orders
        ON stats.client_key=orders.client_key AND stats.business_id=orders.business_id AND stats.campaign_id=orders.campaign_id
        AND stats.entity_id=orders.entity_id AND orders.source_key='yandex_orders'
        WHERE stats.source_key='yandex_order_stats' AND orders.entity_id IS NULL""")
        checks['stats_without_order']=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_analytics_jobs j WHERE state='completed' AND rows_count<>(SELECT count(*) FROM yandex_analytics_raw r WHERE r.job_key=j.job_key)")
        checks['completed_report_row_count_mismatches']=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_fact_funnel WHERE metric_date<date_from OR metric_date>date_to")
        checks['funnel_dates_outside_report_window']=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_fact_order_items i LEFT JOIN yandex_dim_store d USING(client_key,business_id,campaign_id) WHERE d.campaign_id IS NULL")
        checks['orders_with_unknown_store']=c.fetchone()[0]
        c.execute("SELECT source_key,count(*),sum(rows_count),min(date_from),max(date_to) FROM yandex_analytics_jobs WHERE state='completed' GROUP BY 1 ORDER BY 1")
        reports=[dict(zip(['source','jobs','raw_rows','date_from','date_to'],r)) for r in c.fetchall()]
        c.execute('SELECT * FROM yandex_analytics_quality')
        columns=[d[0] for d in c.description];quality=[dict(zip(columns,r)) for r in c.fetchall()]
        c.execute("SELECT state,count(*) FROM yandex_analytics_jobs GROUP BY state")
        states=dict(c.fetchall())
        c.execute("SELECT coverage_status,count(*) FROM yandex_data_coverage GROUP BY 1")
        coverage=dict(c.fetchall())
        c.execute("SELECT count(*) FROM yandex_dim_offer");offers=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_fact_orders");orders=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_fact_returns");returns=c.fetchone()[0]
        c.execute("SELECT count(*) FROM yandex_inventory_current");stock_rows=c.fetchone()[0]
    return jsonable({'checks':checks,'checks_passed':not any(checks.values()),'states':states,'coverage':coverage,'reports':reports,'quality':quality,'offers':offers,'orders':orders,'returns':returns,'current_sku_warehouse_rows':stock_rows})

def main():
    p=argparse.ArgumentParser();p.add_argument('--clients',nargs='+',required=True);p.add_argument('--output',type=Path)
    args=p.parse_args();results={};started=time.monotonic()
    with app.client_registry_connection() as con:clients=client_registry.list_clients(con)
    print(f'PLAN | reconcile {len(args.clients)} client databases; read-only; no API calls',flush=True)
    for n,key in enumerate(args.clients,1):
        client=next(c for c in clients if c['key']==key)
        cfg=dict(app.read_db_config());cfg['database']=client['db_name']
        with psycopg2.connect(**cfg) as conn:results[key]=verify(conn)
        print(f'PROGRESS | {n}/{len(args.clients)} {key} checks_passed={results[key]["checks_passed"]} states={results[key]["states"]} elapsed={time.monotonic()-started:.1f}s',flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return 0 if all(r['checks_passed'] for r in results.values()) else 1

if __name__=='__main__':raise SystemExit(main())
