"""Read-only retail plan/fact; no marketplace or invented target substitution."""
from urllib.parse import parse_qs
import psycopg2
from one_c_import import access
from retail_bi import build

def handle(app,h,parsed):
 if parsed.path!='/api/retail-planfact':return False
 identity=app.CURRENT_ACCESS_USER.get() or {}
 try:q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
 except ValueError:
  h.send_json({'ok':False,'error':'Слишком много фильтров'},status=400);return True
 from data_access import permits
 allowed=identity and app.current_client_key()=='toptop' and q.get('client',[''])[0]=='toptop' and (identity.get('is_admin') or ('toptop' in identity.get('clients',[]) and 'planfact' in identity.get('reports',[]) and permits(identity.get('data_access'),'marketplace:toptop','planfact')))
 if not allowed or not access(app,identity,'sales'):
  h.send_json({'ok':False,'error':'Нет доступа к розничному план/факту'},status=403);return True
 if set(q)-{'client','from','to','store','sales_channel'} or any(len(v)!=1 for v in q.values()) or q.get('sales_channel',['retail'])[0]!='retail':
  h.send_json({'ok':False,'error':'Некорректные фильтры'},status=400);return True
 try:
  payload=build(app,q,identity,allow_future=True)
  payload['plan']={'status':'not_connected','revenue':None,'quantity':None,'receipts':None,'completion_pct':None,'message':'Розничные планы по магазинам не подключены. Факт показан по проведённым чекам 1С; выполнение плана и отклонение не рассчитываются.'}
  h.send_json(payload,headers={'Cache-Control':'no-store'})
 except ValueError as exc:h.send_json({'ok':False,'error':str(exc)},status=400)
 except psycopg2.Error:h.send_json({'ok':False,'error':'Источник розничных продаж временно недоступен'},status=503)
 return True
