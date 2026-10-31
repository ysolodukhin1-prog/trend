import sys,pathlib,json,datetime
from datetime import date
sys.path.insert(0,'/workspace/ozon_category_dashboard');import pulse_vps_admin as v;v.configure_scope();import psycopg2,inventory_history
root=pathlib.Path('/var/lib/pulse');out=root/'wb_stock_grain_repair_20261031.json';s=json.loads((root/'admin_all_clients_daily_state.json').read_text());assert s['run_id']=='49622bf3-7ef1-4e1a-a644-1f98670964f5' and s['status'] in ['completed','idle'] and not s.get('active');assert not out.exists(),'Already repaired; inspect receipt'
v._USE_WRITER_CONFIG.set(True);config=v.app.read_db_config('toptop');record={'run_id':s['run_id'],'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'action':'remove product-total/warehouse-grain double counting only WB29/30; no API calls'}
with psycopg2.connect(**config) as conn:
 with conn.cursor() as cur:
  cur.execute("SET LOCAL statement_timeout='60s'; SELECT to_regclass('public.wb_inventory_before_grain_repair_20261031')");assert cur.fetchone()[0] is None,'Rollback table already exists'
  cur.execute("SELECT snapshot_date,source_type,count(*),sum(stock_available_qty) FROM inventory_history_daily WHERE marketplace='wb' AND snapshot_date BETWEEN '2026-10-29' AND '2026-10-30' GROUP BY 1,2 ORDER BY 1,2");record['before']=[list(map(str,x)) for x in cur.fetchall()]
  cur.execute("SELECT snapshot_date,sum(quantity),count(*) FILTER(WHERE nm_id IS NULL) FROM wb_inventory_history_daily_csv WHERE snapshot_date BETWEEN '2026-10-29' AND '2026-10-30' GROUP BY 1 ORDER BY 1");source=cur.fetchall();assert len(source)==2 and all(x[1]>0 and x[2]==0 for x in source),'CSV source incomplete/unmapped'
  cur.execute("CREATE TABLE public.wb_inventory_before_grain_repair_20261031 AS SELECT * FROM inventory_history_daily WHERE marketplace='wb' AND snapshot_date BETWEEN '2026-10-29' AND '2026-10-30'")
 rows=inventory_history.publish_wb_stock_history_csv(conn,date(2026,10,29),date(2026,10,30),replace=True)
 with conn.cursor() as cur:
  cur.execute("SELECT snapshot_date,sum(stock_available_qty),count(*) FROM inventory_history_daily WHERE marketplace='wb' AND snapshot_date BETWEEN '2026-10-29' AND '2026-10-30' GROUP BY 1 ORDER BY 1");after=cur.fetchall();assert [(x[0],x[1]) for x in after]==[(x[0],x[1]) for x in source],'CSV/mart quantities do not match; rollback'
  record.update(status='ok',after=[list(map(str,x)) for x in after],source=[list(map(str,x)) for x in source],published_rows=rows,rollback_table='wb_inventory_before_grain_repair_20261031')
record['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();out.write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps(record,ensure_ascii=False))