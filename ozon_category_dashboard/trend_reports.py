"""Personal TREND discovery and bounded read gateway; rights stay in TREND."""
import json,re,time,subprocess,sys
from pathlib import Path
from datetime import date,datetime,timedelta,UTC
from urllib.parse import urlencode,urlparse
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from data_access import permits
from galactica_entitlement import SourceDenied,configured_subject,require_access_origin,read_entitlement
from trend_database import authority

ROUTES={
 'abc':['summary','stats'], 'product':['product-summary','product-stats'],
 'sku':['sku-summary','sku-stats','sku-card'],
 'adv':['adv-summary','adv-daily','adv-waterfalls','adv-stats','adv-campaigns'],
 'mediaAdv':['media-adv-summary','media-adv-daily','media-adv-waterfalls','media-adv-stats'],
 'funnel':['funnel-summary','funnel-daily','funnel-waterfalls','funnel-stats','funnel-products','order-feed','sales-order-days'],
 'weeklyDynamics':['weekly-dynamics'],
 'inventoryHistory':['inventory-history','inventory-history-summary','inventory-history-products','weekly-sku-inventory','cluster-supply/status'],
 'planfact':['planfact-summary','planfact-daily','planfact-monthly','planfact-scorecard','planfact-products'],
 'salesPlanning':['sales-forecast'], 'mediaPlan':['media-plan'],
 'profitLoss':['profit-loss','pl-monthly-budget','pl-cost-registry'],
 'unitEconomics':['unit-workspace','unit-economics'],
 'seoMonitoring':['seo-monitoring-products','seo-projects','seo-project','seo-project-full-run/status'],
 'wbSearchQueries':['wb-search-query-products','wb-search-queries-dashboard'],
 'wbAdSearchQueries':['wb-ad-search-queries-dashboard'],
 'wbEntrance':['wb-entrance-products','wb-entrance-dashboard'],
 'reviews':['reviews-dashboard','reviews-insights'],
 'commercialRadar':['planfact-funnel-matrix'],
 **{r:['assortment'] for r in ('assortmentProducts','assortmentPrices','assortmentABC','assortmentXYZ')},
 **{r:['yandex-market/analytics'] for r in ('yandexOverview','yandexFunnel','yandexFinance','yandexPromotion','yandexInventory')},
 **{r:['lamoda/dashboard'] for r in ('lamodaSales','lamodaReturns','lamodaCatalog','lamodaOperations')},
}
# Legacy TREND handler paths are implementation details, never client/source IDs.
# Only the TOPTOP-scoped runtime may dispatch these exact internal paths.
INTERNAL_REPORT_PATHS={
 'sales-forecast':'km-trade/sales-forecast', 'media-plan':'km-trade/media-plan',
 'profit-loss':'km-trade/pl', 'pl-monthly-budget':'km-trade/pl-monthly-budget',
 'pl-cost-registry':'km-trade/pl-cost-registry',
 'unit-workspace':'km-trade/unit-workspace', 'unit-economics':'km-trade/unit-economics',
}

def toptop_report_config(config):
 if config.get('database')!='toptop':raise SourceDenied()
 return {**config,'options':'-c statement_timeout=8000 -c default_transaction_read_only=on'}

PARAMS={k:{'type':'string','maxLength':500} for k in (
 'date_from date_to marketplace category category_exact product article sku search q status market sort sort_col sort_dir order '
 'campaign_id project_id job_id month year channel sales_channel store inventory_skus metric group_by collection_status seo_status '
 'abc_orders abc_sales abc_stock abc_combined brand subject category_name product_name offer_id nm_id period week_from week_to '
 'columns fields view tab mode metric_family granularity warehouse_id scheme model currency'.split())}
PARAMS.update({k:{'type':'integer','minimum':1,'maximum':v} for k,v in [('limit',200),('page_size',200),('page',1000),('weeks',52)]})
PARAMS.update({k:{'type':'boolean'} for k in ('compact','include_details')})
PARAMS['column_filters']={'type':'object'}
YANDEX_KEYS={
 'yandexOverview':['orders_daily','top_sku_by_buyer_payment','returns_daily'],
 'yandexFunnel':['funnel_daily'], 'yandexFinance':['services_daily','transactions_daily','realization_periods'],
 'yandexPromotion':['marketing_daily','boost_sales_periods'], 'yandexInventory':['stock_snapshots'],
}
MPSTATS=json.loads(Path(__file__).with_name('mpstats_read_catalog.json').read_text(encoding='utf-8'))

class Unavailable(Exception):
 def __init__(self,code,http_status=None):self.code=code;self.http_status=http_status

def rights(app,subject):
 grants=authority(app,subject)
 with app.client_registry_connection() as db:
  with db.cursor() as c:
   c.execute('SELECT report_id FROM public.bi_user_reports WHERE user_id=%s',(subject[0],));assigned={r['report_id'] for r in c.fetchall()}
   c.execute("SELECT reports,status,marketplaces FROM public.bi_client_registry WHERE client_key='toptop'");row=c.fetchone()
 enabled=set(app.effective_client_reports('toptop',config=row) if row else app.effective_client_reports('toptop'))
 if row and row['status']!='active':raise SourceDenied()
 return grants,assigned,enabled

def authorize_report(app,subject,report):
 grants,assigned,enabled=rights(app,subject)
 if report not in assigned or report not in enabled or not permits(grants,'marketplace:toptop',report):raise SourceDenied()
 with app.client_registry_connection() as db:read_entitlement(db,subject,'toptop',report)
 return grants

def allowed_mp(grants):
 resources=next((g['resources'] for g in grants if g.get('source')=='mpstats'),[])
 return [op for op in MPSTATS if '*' in resources or 'GET '+op['path'] in resources or any(re.fullmatch(re.sub(r'\{\w+\}',r'[A-Za-z0-9_-]+',op['path']),x[4:]) for x in resources if x.startswith('GET '))]

def catalog(app,subject):
 grants,assigned,enabled=rights(app,subject)
 labels={r['id']:r['label'] for r in app.ADMIN_REPORT_CATALOG}
 reports=[]
 for report in sorted(assigned & enabled):
  if permits(grants,'marketplace:toptop',report):
   reports.append({'id':report,'label':labels.get(report,report),'operations':ROUTES.get(report,[]),'availability':'not_checked' if report in ROUTES else 'adapter_missing'})
 sources=[{'source':'marketplace:toptop','kind':'reports','reports':reports}]
 with app.client_registry_connection() as db:
  with db.cursor() as c:
   c.execute("SELECT DISTINCT service_key FROM public.bi_service_credentials WHERE service_key='1c' OR service_key LIKE '1c:%'");registered={r['service_key'] for r in c.fetchall()}
 for g in grants:
  if g['source'].startswith('1c') and g.get('resources'):
   sources.append({'source':g['source'],'kind':'database','resources':g['resources'],'availability':'not_checked' if g['source'] in registered else 'connection_not_configured'})
 if any(g.get('source')=='mpstats' and g.get('resources') for g in grants):
  sources.append({'source':'mpstats','kind':'methods','resources':next(g['resources'] for g in grants if g['source']=='mpstats'),'read_methods':len(allowed_mp(grants)),'availability':'not_checked' if app.service_credential('mpstats') else 'connection_not_configured','catalog_snapshot':'2026-09-25'})
 authority(app,subject)
 return {'sources':sources,'scope':'TOPTOP only','galactica_permissions_separate':True}

def validate_params(values):
 if not isinstance(values,dict) or set(values)-set(PARAMS):raise ValueError('Unknown report parameter')
 for k,v in values.items():
  typ=PARAMS[k]['type']
  if typ=='integer' and (type(v) is not int or not 1<=v<=PARAMS[k]['maximum']):raise ValueError('Parameter outside limit')
  if typ=='string' and (not isinstance(v,str) or len(v)>500 or any(ord(c)<32 for c in v)):raise ValueError('Invalid string parameter')
  if typ=='boolean' and type(v) is not bool:raise ValueError('Invalid boolean')
  if typ=='object' and (not isinstance(v,dict) or len(json.dumps(v))>2000):raise ValueError('Invalid filter')
 if 'marketplace' in values and values['marketplace'] not in ('wb','ozon','yandex_market','lamoda','total'):raise SourceDenied()
 if values.get('sales_channel','retail') not in ('retail','online','wholesale'):raise ValueError('Unknown sales channel')
 start=date.fromisoformat(values.get('date_from',str(date.today()-timedelta(days=29))))
 end=date.fromisoformat(values.get('date_to',str(date.today())))
 if start>end or (end-start).days>366:raise ValueError('Maximum period366days')
 return {**values,'date_from':str(start),'date_to':str(end),'limit':values.get('limit',50),'page_size':values.get('page_size',50)}

def bound(data,limit=200):
 truncated=[];budget=[2000]
 def walk(v,path):
  if isinstance(v,list):
   count=min(len(v),limit,max(0,budget[0]));budget[0]-=count
   if count<len(v):truncated.append({'path':path,'returned':count,'source_count':len(v)})
   return [walk(x,path+'[]') for x in v[:count]]
  if isinstance(v,dict):return {str(k):walk(x,path+'.'+str(k)) for k,x in v.items()}
  return v
 return walk(data,'data'),truncated

def run_report(app,subject,args):
 if set(args)-{'operation','report','report_operation','parameters'}:raise ValueError('Unknown request field')
 report=args.get('report');grants=authorize_report(app,subject,report)
 if report not in ROUTES:raise Unavailable('report_adapter_missing')
 if args['operation']=='describe':return {'report':report,'operations':ROUTES[report],'parameters':{'type':'object','properties':PARAMS,'additionalProperties':False},'limits':{'seconds':28,'rows_per_array':200,'bytes':800000},'data_origin':'TREND operational store; source freshness must be checked'}
 operation=args.get('report_operation',ROUTES[report][0])
 if operation not in ROUTES[report]:raise SourceDenied()
 params=validate_params(args.get('parameters',{}));params.update(client='toptop',dashboard=report)
 if report.startswith('wb'):params['marketplace']='wb'
 if 'sales_channel' in params:
  from one_c_import import source_key,report_tables
  SOURCE_KEY=source_key(app);SOURCE_TABLES=report_tables()
  if not all(permits(grants,SOURCE_KEY,t) for t in SOURCE_TABLES):raise SourceDenied()
 # This is the same runtime handler and personal identity as the TREND UI.
 # The worker uses the SELECT-only business connection and no administrator flag.
 class Capture(app.DashboardHandler):
  def __init__(self):
   self.path='/api/'+INTERNAL_REPORT_PATHS.get(operation,operation)+'?'+urlencode({k:json.dumps(v) if isinstance(v,dict) else str(v).lower() if isinstance(v,bool) else v for k,v in params.items()})
   self.headers={};self.result=None;self.status=200
  def dashboard_access_granted(self):return True
  def dashboard_access_identity(self):return {'user_id':subject[0],'username':subject[1],'is_admin':False,'clients':['toptop'],'reports':[report],'data_access':grants,'admin_sections':[]}
  def send_json(self,payload,status=200,headers=None):self.result=payload;self.status=status
  def send_error(self,status,*a,**kw):self.status=status;self.result={'reason_code':'report_route_not_available'}
 h=Capture();app.DashboardHandler.do_GET(h)
 if h.status in (401,403):raise SourceDenied()
 if h.result is None:raise Unavailable('report_empty_response',h.status)
 data=h.result
 if h.status>=400:raise Unavailable(data.get('reason_code') or {'database_error':'report_storage_error','statement_timeout':'sql_statement_timeout'}.get(data.get('kind'),'trend_report_http_'+str(h.status)),h.status)
 if report in YANDEX_KEYS:
  keys=set(YANDEX_KEYS[report])|{'ok','client','date_from','date_to','store','status','reason','aggregate_cache_current'}
  data={k:v for k,v in data.items() if k in keys}
 authorize_report(app,subject,report)
 if 'sales_channel' in params:
  current=authority(app,subject)
  if source_key(app)!=SOURCE_KEY or not all(permits(current,SOURCE_KEY,t) for t in SOURCE_TABLES):raise SourceDenied()
 data,truncated=bound(data,params['limit'])
 unavailable=isinstance(data,dict) and (data.get('available') is False or data.get('status')=='unavailable' or data.get('data_status')=='unavailable' or data.get('ok') is False)
 return {'source':'marketplace:toptop','report':report,'report_operation':operation,'data':data,'availability':'unavailable' if unavailable else 'available','reason_code':data.get('reason_code') or data.get('reason') if unavailable else None,'truncated':truncated,'freshness_status':'inspect_source_timestamps; query time is not refresh time'}

def mp_permitted(grants,template,path):
 return permits(grants,'mpstats','GET '+template) or permits(grants,'mpstats','GET '+path)

class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*a,**k):return None

def run_mpstats(app,subject,args):
 if set(args)-{'operation','path','parameters','search','offset','limit'}:raise ValueError('Unknown MPStats field')
 grants=authority(app,subject)
 if not any(g.get('source')=='mpstats' and g.get('resources') for g in grants):raise SourceDenied()
 if args['operation']=='methods':
  search=args.get('search','');offset=args.get('offset',0);limit=args.get('limit',30)
  if not isinstance(search,str) or len(search)>100 or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('Invalid page')
  ops=[x for x in allowed_mp(grants) if search.casefold() in (x['path']+' '+x['title']).casefold()]
  return {'methods':ops[offset:offset+limit],'total':len(ops),'catalog_snapshot':'2026-09-25','method':'GET'}
 path=args.get('path','');values=args.get('parameters',{})
 if not isinstance(path,str) or not re.fullmatch(r'/api/analytics/v1/(?:wb|oz)/[A-Za-z0-9_/{}/-]+',path) or '..' in path:raise ValueError('Invalid MPStats path')
 match=None;path_values={}
 for op in MPSTATS:
  pattern=re.sub(r'\{\w+\}',r'([A-Za-z0-9_-]+)',op['path'])
  m=re.fullmatch(pattern,path)
  if m:match=op;path_values=dict(zip(re.findall(r'\{(\w+)\}',op['path']),m.groups()));break
 if not match:raise ValueError('Use a published GET method from catalog')
 if not mp_permitted(grants,match['path'],path):raise SourceDenied()
 schema={p['name']:p for p in match['parameters'] if p.get('in')=='query'}
 if not isinstance(values,dict) or set(values)-set(schema):raise ValueError('Unknown MPStats parameter')
 if any(p.get('required') and k not in values for k,p in schema.items()):raise ValueError('Required MPStats parameter missing')
 if any(not isinstance(v,(str,int,float,bool)) or len(str(v))>500 for v in values.values()):raise ValueError('Invalid MPStats parameter')
 for a,b in [('d1','d2')]:
  if a in values and b in values and not 0<=(date.fromisoformat(values[b])-date.fromisoformat(values[a])).days<=366:raise ValueError('Invalid period')
 token=app.service_credential('mpstats')
 if not token:raise Unavailable('mpstats_connection_not_configured')
 request=Request('https://mpstats.io'+path+'?'+urlencode(values),headers={'X-Mpstats-TOKEN':token,'Accept':'application/json'},method='GET')
 try:
  with build_opener(NoRedirect).open(request,timeout=12) as response:
   body=response.read(800001)
   if len(body)>800000:raise Unavailable('result_exceeds_800kb')
   if 'json' not in response.headers.get('Content-Type',''):raise Unavailable('mpstats_non_json_response')
   data=json.loads(body)
 except HTTPError as e:raise Unavailable('mpstats_http_'+str(e.code),e.code) from None
 except (URLError,TimeoutError):raise Unavailable('mpstats_connection_timeout_or_network') from None
 if not mp_permitted(authority(app,subject),match['path'],path):raise SourceDenied()
 data,truncated=bound(data)
 return {'source':'mpstats','method':'GET','path':path,'data':data,'truncated':truncated,'availability':'available','freshness_status':'source_response'}

def execute(app,subject,args):
 if not isinstance(args,dict):raise ValueError('Invalid request')
 op=args.get('operation')
 if op=='catalog':
  if set(args)!={'operation'}:raise ValueError('Unknown catalog field')
  return catalog(app,subject)
 if op in ('describe','report'):return run_report(app,subject,args)
 if op in ('methods','mpstats'):return run_mpstats(app,subject,args)
 raise ValueError('Unknown read operation')

def audit(app,subject,args,status,reason,elapsed):
 with app.client_registry_connection() as db:
  with db.cursor() as c:
   c.execute('CREATE TABLE IF NOT EXISTS public.bi_galactica_read_events(id bigserial PRIMARY KEY,user_id bigint,source text,resource text,operation text,status text,reason_code text,duration_ms integer,created_at timestamptz NOT NULL DEFAULT now())')
   c.execute('INSERT INTO public.bi_galactica_read_events(user_id,source,resource,operation,status,reason_code,duration_ms) VALUES(%s,%s,%s,%s,%s,%s,%s)',(subject[0] if subject else None,'mpstats' if args.get('operation') in ('mpstats','methods') else 'marketplace:toptop',str(args.get('report',''))[:120],str(args.get('operation',''))[:30],status,reason,int(elapsed)))
  db.commit()

def handle(app,h):
 start=time.monotonic();subject=None;args={};status='error';reason=None;http=200
 try:
  require_access_origin(h.headers)
  length=int(h.headers.get('Content-Length','0'))
  if not 0<length<=16000:raise ValueError('Invalid size')
  args=json.loads(h.rfile.read(length))
  if not isinstance(args,dict):raise ValueError('Invalid request')
  subject=configured_subject(app,app.dashboard_access_session_from_cookie(h.headers.get('Cookie')))
  authority(app,subject)
  worker=subprocess.run([sys.executable,str(Path(__file__).with_name('trend_reports_worker.py'))],input=json.dumps({'subject':subject,'args':args}),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=28)
  reply=json.loads(worker.stdout)
  if reply.get('error')=='denied':raise SourceDenied()
  if reply.get('error')=='invalid':raise ValueError(reply.get('reason_code','Invalid request'))
  if reply.get('error'):raise Unavailable(reply.get('reason_code','worker_failed'),reply.get('upstream_http_status'))
  result=reply['result']
  # Recompute discovery after worker execution. Never return a stale grant list.
  if args['operation']=='catalog':result=catalog(app,subject)
  elif args['operation'] in ('describe','report'):authorize_report(app,subject,args.get('report'))
  else:
   authority(app,subject)
   if args['operation']=='methods':result=run_mpstats(app,subject,args)
   elif not any(mp_permitted(authority(app,subject),x['path'],args.get('path','')) for x in MPSTATS if re.fullmatch(re.sub(r'\{\w+\}',r'[A-Za-z0-9_-]+',x['path']),args.get('path',''))):raise SourceDenied()
  result.update(client_key='toptop',queried_at=datetime.now(UTC).isoformat(),read_only=True)
  if len(json.dumps(result,default=str).encode())>800000:raise Unavailable('result_exceeds_800kb')
  status='allowed' if result.get('availability')!='unavailable' else 'unavailable';reason=result.get('reason_code')
 except SourceDenied:result={'error':'TREND_ACCESS_DENIED'};status='denied';reason='permission_or_session_denied';http=403
 except (ValueError,TypeError,KeyError):result={'error':'INVALID_READ_REQUEST'};status='invalid';reason='invalid_request';http=422
 except subprocess.TimeoutExpired:result={'error':'TREND_SOURCE_UNAVAILABLE','reason_code':'worker_deadline_28s'};status='unavailable';reason='worker_deadline_28s';http=503
 except Unavailable as e:result={'error':'TREND_SOURCE_UNAVAILABLE','reason_code':e.code,'upstream_http_status':e.http_status};status='unavailable';reason=e.code;http=503
 except Exception:result={'error':'TREND_SOURCE_UNAVAILABLE','reason_code':'gateway_internal_failure'};status='unavailable';reason='gateway_internal_failure';http=503
 try:audit(app,subject,args if isinstance(args,dict) else {},status,reason,(time.monotonic()-start)*1000)
 except Exception:result={'error':'AUDIT_UNAVAILABLE'};http=503
 h.send_json(result,status=http,headers={'Cache-Control':'no-store'})
