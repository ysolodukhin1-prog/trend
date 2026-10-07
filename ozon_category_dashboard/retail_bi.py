"""Retail BI at receipt-line grain. Read-only, source-authorized and null-safe."""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from collections import defaultdict
from urllib.parse import parse_qs
import psycopg2
from psycopg2.extras import RealDictCursor
from one_c_import import access
from trend_retail import STORES

def clean(v):
 if isinstance(v,Decimal):return float(v)
 if isinstance(v,(datetime,date)):return v.isoformat()
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [clean(x) for x in v]
 return v

def aggregate(rows):
 if not rows:return {'revenue':None,'quantity':None,'receipts':None,'average_receipt':None,'units_per_receipt':None,'skus':None,'observed_days':0}
 revenue=sum((r['revenue'] for r in rows),Decimal(0));quantity=sum((r['quantity'] for r in rows),Decimal(0));receipts=len({r['recorder_id'] for r in rows})
 return {'revenue':revenue,'quantity':quantity,'receipts':receipts,'average_receipt':revenue/receipts if receipts else None,'units_per_receipt':quantity/receipts if receipts else None,'skus':len({(r['product_id'],r['variant_id']) for r in rows}),'observed_days':len({r['period'].date() for r in rows})}

def period_compare(current,previous,store_ids,start,finish):
 days=(finish-start).days+1;eligible=[]
 for sid in store_ids:
  if len({r['period'].date() for r in current if r['store_id']==sid})==days and len({r['period'].date() for r in previous if r['store_id']==sid})==days:eligible.append(sid)
 a=aggregate([r for r in current if r['store_id'] in eligible]);b=aggregate([r for r in previous if r['store_id'] in eligible]);total=aggregate(current)
 same=bool(eligible) and a['receipts']==total['receipts']
 deltas={k:(a[k]/b[k]-1)*100 if same and a[k] is not None and b[k] is not None and b[k]>0 else None for k in ['revenue','quantity','receipts','average_receipt','units_per_receipt','skus']}
 return {'from':start-timedelta(days=days),'to':start-timedelta(days=1),'eligible_stores':[STORES[x] for x in eligible],'excluded_stores':[STORES[x] for x in store_ids if x not in eligible],'matches_current':same,'current':a,'previous':b,'deltas':deltas,'method':'Равные последовательные периоды; сравнение только магазинов с чеками во все дни обоих периодов.'}

def build(app,q,identity,allow_future=False):
 get=lambda key,default='':q.get(key,[default])[0]
 sid=get('store')
 if sid and sid not in STORES:raise ValueError('Неизвестный магазин')
 today=datetime.now(timezone(timedelta(hours=3))).date()
 with psycopg2.connect(**app.read_db_config('toptop'),cursor_factory=RealDictCursor) as pg:
  pg.set_session(readonly=True,isolation_level='REPEATABLE READ')
  with pg.cursor() as c:
   c.execute("SET LOCAL statement_timeout='8s'")
   c.execute("SELECT store_id,min(period)::date first_date,max(period)::date last_date,count(DISTINCT period::date) observed_days FROM retail_1c.sales WHERE channel='retail' GROUP BY store_id");coverage={r['store_id']:dict(r) for r in c.fetchall()}
   cutoff=max((r['last_date'] for r in coverage.values()),default=None)
   default_end=cutoff or today
   try:start=date.fromisoformat(get('from',default_end.replace(day=1).isoformat()));finish=date.fromisoformat(get('to',default_end.isoformat()))
   except ValueError:raise ValueError('Укажите корректный период')
   if finish<start or (finish-start).days>365 or (finish>today and not allow_future):raise ValueError('Выберите корректный период не больше года')
   length=(finish-start).days+1;prev_start=start-timedelta(days=length);selected=[sid] if sid else list(STORES)
   c.execute("SELECT period,recorder_id,line_no,store_id,product_id,variant_id,product_name,article,quantity,revenue FROM retail_1c.sales WHERE channel='retail' AND store_id=ANY(%s) AND period>=%s AND period<%s ORDER BY period,recorder_id,line_no",(selected,prev_start,finish+timedelta(days=1)));facts=[dict(r) for r in c.fetchall()]
   if len({(r['period'],r['recorder_id'],r['line_no']) for r in facts})!=len(facts):raise ValueError('В источнике обнаружены дубли строк чеков')
   current=[r for r in facts if start<=r['period'].date()<=finish];previous=[r for r in facts if prev_start<=r['period'].date()<start]
   totals=aggregate(current);comparison=period_compare(current,previous,selected,start,finish)
   days=[]
   for i in range(length):
    day=start+timedelta(days=i);rows=[r for r in current if r['period'].date()==day]
    days.append({'date':day,**aggregate(rows),'stores':[{'id':s,'name':STORES[s],**aggregate([r for r in rows if r['store_id']==s])} for s in selected]})
   stores=[]
   for s in selected:
    rows=[r for r in current if r['store_id']==s];metrics=aggregate(rows);comp=period_compare(rows,[r for r in previous if r['store_id']==s],[s],start,finish)
    stores.append({'id':s,'name':STORES[s],**metrics,'share':metrics['revenue']/totals['revenue']*100 if metrics['revenue'] is not None and totals['revenue'] else None,'delta':comp['deltas']['revenue'],'last_date':coverage.get(s,{}).get('last_date'),'missing_days':length-metrics['observed_days']})
   stores.sort(key=lambda s:(s['revenue'] is None,-(s['revenue'] or 0)))
   grouped=defaultdict(list)
   for row in current:grouped[row['product_id']].append(row)
   products=[]
   for product,rows in grouped.items():
    metrics=aggregate(rows);variants=[]
    for vid in {r['variant_id'] for r in rows}:variants.append({'id':vid,'name':'Без характеристики' if vid=='0'*32 else 'Вариант '+vid[-8:],**aggregate([r for r in rows if r['variant_id']==vid])})
    products.append({'id':product,'name':rows[0]['product_name'],'article':rows[0]['article'],**metrics,'share':metrics['revenue']/totals['revenue']*100 if totals['revenue'] else None,'variants':sorted(variants,key=lambda x:-(x['revenue'] or 0))})
   products.sort(key=lambda p:-(p['revenue'] or 0));products=products[:20]
   if products and access(app,identity,'catalog'):
    c.execute("SELECT product_id,variant_id,payload->>'variant' name FROM one_c_import.current_catalog WHERE database_name='1c_retail_prod' AND product_id=ANY(%s)",([p['id'] for p in products],));names={(r['product_id'],r['variant_id']):r['name'] for r in c.fetchall()}
    for p in products:
     for v in p['variants']:v['name']=names.get((p['id'],v['id'])) or v['name']
   c.execute("SELECT s.checked_at,s.summary,s.source_key FROM one_c_import.active a JOIN one_c_import.snapshots s ON s.id=a.snapshot_id WHERE a.database_name='1c_retail_prod' AND a.dataset='sales'");snapshot=c.fetchone() or {}
 freshness=[{'id':s,'name':name,**coverage.get(s,{'first_date':None,'last_date':None,'observed_days':0}),'days_since_last':(today-coverage[s]['last_date']).days if s in coverage else None} for s,name in STORES.items()]
 return clean({'ok':True,'scope':'Проведённые непомеченные чеки ККМ подтверждённых розничных магазинов','period':{'from':start,'to':finish,'days':length},'store':sid,'today':today,'cutoff':cutoff,'loaded_at':snapshot.get('checked_at'),'checked_through':snapshot.get('summary',{}).get('checked_through'),'kpis':totals,'comparison':comparison,'daily':days,'stores':stores,'products':products,'freshness':freshness,'active_stores':sum(s['receipts'] is not None for s in stores),'selected_stores':len(selected),'definitions':{'revenue':'Сумма выручки активных движений проведённых чеков; возвраты отдельными документами не вычтены.','receipts':'Число уникальных чеков, а не товарных строк.','average_receipt':'Выручка по чекам / число чеков.','units_per_receipt':'Проданные единицы / число чеков.','skus':'Уникальные пары товар/характеристика с продажами.','gaps':'День без строк в источнике показан пропуском; он не считается нулевыми продажами.','comparison':comparison['method']},'limitations':['Возвраты, себестоимость и прибыль не сверены.','История файлов без НДС не включена в показатели по чекам.','Склад Большой не включён: розничный канал не подтверждён.']})

def handle(app,h,parsed):
 if parsed.path!='/api/retail-bi':return False
 from trend_channel_sales import allowed
 identity=app.CURRENT_ACCESS_USER.get() or {}
 try:q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
 except ValueError:
  h.send_json({'ok':False,'error':'Слишком много фильтров'},status=400);return True
 if not allowed(app,identity,q.get('client',[''])[0]) or not access(app,identity,'sales'):
  h.send_json({'ok':False,'error':'Нет доступа к источнику 1С Розница'},status=403);return True
 if set(q)-{'client','from','to','store','sales_channel'} or any(len(v)!=1 for v in q.values()):
  h.send_json({'ok':False,'error':'Некорректные фильтры'},status=400);return True
 if q.get('sales_channel',['retail'])[0]!='retail':
  h.send_json({'ok':False,'error':'Некорректный канал продаж'},status=400);return True
 try:h.send_json(build(app,q,identity),headers={'Cache-Control':'no-store'})
 except ValueError as exc:h.send_json({'ok':False,'error':str(exc)},status=400)
 except psycopg2.Error:h.send_json({'ok':False,'error':'Данные Розницы временно недоступны'},status=503)
 return True
