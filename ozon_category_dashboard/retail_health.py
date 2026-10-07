"""Source-backed retail checks, with explicit unknowns and operational thresholds."""
from datetime import date,timedelta,datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs
import psycopg2
from one_c_import import access
from retail_bi import build

FRESH_WARNING=2
FRESH_CRITICAL=7
DROP_WARNING=-20

def normalize_period(q,today=None):
 q={k:list(v) for k,v in q.items()}
 mode=q.pop('period',[''])[0]
 if mode not in ('','28d','custom'):raise ValueError('Неизвестный период')
 missing=lambda value:value in ('','undefined','null')
 start=q.get('from',[''])[0];finish=q.get('to',[''])[0]
 if mode=='28d' or not mode and missing(start) and missing(finish):
  today=today or datetime.now(ZoneInfo('Europe/Moscow')).date()
  q['from']=[(today-timedelta(days=28)).isoformat()];q['to']=[(today-timedelta(days=1)).isoformat()]
  return q,'28d'
 if missing(start) or missing(finish):raise ValueError('Укажите начало и конец периода')
 return q,'custom'

def assess(payload):
 today=date.fromisoformat(payload['today']);start=date.fromisoformat(payload['period']['from']);finish=date.fromisoformat(payload['period']['to']);end=min(finish,today-timedelta(days=1))
 checks=[];stores=[];freshness={x['id']:x for x in payload['freshness']};quality={x['id']:x for x in payload['quality']}
 def add(sid,key,title,status,value,rule,detail):checks.append({'store_id':sid,'store':freshness[sid]['name'],'key':key,'title':title,'status':status,'value':value,'rule':rule,'detail':detail})
 for store in payload['stores']:
  sid=store['id'];source=freshness[sid];lag=source['days_since_last']
  state='unknown' if lag is None else 'critical' if lag>FRESH_CRITICAL else 'warning' if lag>FRESH_WARNING else 'pass'
  add(sid,'freshness','Свежесть чеков',state,lag,'Внимание: более 2 календарных дней; критично: более 7.','Последние чеки: '+str(source['last_date'] or 'не найдены')+'. Дата загрузки не заменяет дату продаж.')
  first=date.fromisoformat(source['first_date']) if source.get('first_date') else None
  due_start=max(start,first) if first else start
  due_dates=[(due_start+timedelta(days=i)).isoformat() for i in range(max(0,(end-due_start).days+1))]
  seen={row['date'] for row in payload['daily'] if next((s['receipts'] for s in row['stores'] if s['id']==sid),None) is not None}
  missing=[d for d in due_dates if d not in seen]
  state='unknown' if not first or not due_dates else 'warning' if missing else 'pass'
  add(sid,'coverage','Покрытие периода',state,len(missing) if due_dates else None,'Проверяются завершённые календарные дни выбранного периода, начиная с первой даты источника.','Нет чеков за '+str(len(missing))+' дней. Это пробел источника или календаря работы, а не подтверждённые нулевые продажи.' if missing else 'Во все проверяемые дни есть чеки.' if due_dates else 'В выбранном периоде ещё нет завершённых дней с известным источником.')
  q=quality[sid];comparison=q.get('comparison');delta=comparison['delta'] if comparison else None;state='unknown' if delta is None else 'warning' if delta<=DROP_WARNING else 'pass'
  add(sid,'sales_change','Изменение выручки',state,delta,'Внимание при снижении на 20% и более; чеки во все дни обоих периодов. '+('Периоды: '+comparison['current_from']+' — '+comparison['current_to']+' и '+comparison['previous_from']+' — '+comparison['previous_to']+'.' if comparison else 'Нет завершённого периода источника.'),'Недостаточное покрытие для сопоставимого сравнения.' if delta is None else 'Изменение суммы чеков относительно предыдущего равного периода; причина изменения не установлена.')
  q=quality[sid];issues=sum(q[k] for k in ['missing_article','missing_product','nonpositive_quantity','negative_revenue','zero_revenue']);state='unknown' if not q['lines'] else 'warning' if issues else 'pass'
  add(sid,'line_quality','Проверка строк чеков',state,issues if q['lines'] else None,'Пустой артикул/товар, количество ≤ 0, выручка ≤ 0 требуют сверки.','Проверено '+str(q['lines'])+' строк. Счётчик — срабатывания правил, одна строка может нарушать несколько; исправления не выполнялись.')
  stores.append({**store,'delta':delta,'days_since_last':lag,'completed_days':len(due_dates),'observed_completed_days':len(due_dates)-len(missing),'missing_dates':missing,'quality':q})
 order={'critical':0,'warning':1,'unknown':2,'pass':3}
 checks.sort(key=lambda x:(order[x['status']],x['store'],x['key']))
 alerts=[x for x in checks if x['status'] in ('critical','warning')]
 return {'checked_through':end.isoformat(),'checks':checks,'alerts':alerts,'stores':stores,'counts':{s:sum(c['status']==s for c in checks) for s in order},'lines_checked':sum(q['lines'] for q in quality.values()),'unavailable':[{'name':'Остатки и дефицит','reason':'Нет сверенного начального сальдо и движений остатков.'},{'name':'Маржа и прибыль','reason':'Себестоимость и расходы не сверены.'},{'name':'Возвраты','reason':'Возвратные документы не сверены с продажами.'},{'name':'Выполнение плана','reason':'Розничные планы магазинов не подключены.'}],'method':'Пороги — правила внимания, не доказательство сбоя магазина. Календарь его работы не подтверждён; текущий и будущие дни исключены из проверки покрытия.'}

def handle(app,h,parsed):
 if parsed.path!='/api/retail-health':return False
 identity=app.CURRENT_ACCESS_USER.get() or {}
 try:q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
 except ValueError:
  h.send_json({'ok':False,'error':'Слишком много фильтров'},status=400);return True
 from data_access import permits
 allowed=identity and app.current_client_key()=='toptop' and q.get('client',[''])[0]=='toptop' and (identity.get('is_admin') or ('toptop' in identity.get('clients',[]) and 'commercialRadar' in identity.get('reports',[]) and permits(identity.get('data_access'),'marketplace:toptop','commercialRadar')))
 if not allowed or not access(app,identity,'sales'):
  h.send_json({'ok':False,'error':'Нет доступа к Health Check розницы'},status=403);return True
 if set(q)-{'client','from','to','store','sales_channel','period'} or any(len(v)!=1 for v in q.values()) or q.get('sales_channel',['retail'])[0]!='retail':
  h.send_json({'ok':False,'error':'Некорректные фильтры'},status=400);return True
 try:
  q,mode=normalize_period(q)
  payload=build(app,q,identity,allow_future=True,include_quality=True);payload['period_mode']=mode;payload['health']=assess(payload)
  h.send_json(payload,headers={'Cache-Control':'no-store'})
 except ValueError as exc:h.send_json({'ok':False,'error':str(exc)},status=400)
 except psycopg2.Error:h.send_json({'ok':False,'error':'Источник Health Check розницы временно недоступен'},status=503)
 return True
