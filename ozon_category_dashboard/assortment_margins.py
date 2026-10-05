"""Reuse source-backed unit economics; never infer missing costs or scenarios."""
from collections import defaultdict
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
import threading,time
import psycopg2
from psycopg2.extras import RealDictCursor
from unit_economics_workspace import workspace
from unit_economics_engine import calculate

_cache={}
_lock=threading.Lock()

def facts(config,client,start,end):
 key=(client,start,end)
 with _lock:
  old=_cache.get(key)
  if old and old[0]>time.monotonic():return old[1]
  result=workspace(config,client,str(start),str(end))
  _cache[key]=(time.monotonic()+300,result)
  for k in list(_cache):
   if _cache[k][0]<time.monotonic():del _cache[k]
  return result

def attach_margins(app,rows,days,client,mode):
 end=datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)
 start=end-timedelta(days=days-1)
 if mode=='actual':result=facts(app.read_db_config(client),client,start,end)
 else:
  with psycopg2.connect(**app.read_db_config(client),cursor_factory=RealDictCursor) as conn:
   conn.set_session(readonly=True)
   with conn.cursor() as c:
    c.execute("SELECT DISTINCT ON(scenario_key) marketplace,sku,inputs,created_at FROM unit_scenario_versions ORDER BY scenario_key,revision DESC")
    result={'allocated_products':[],'scenarios':[dict(r) for r in c.fetchall()]}
 indexed=defaultdict(list)
 for product in result['allocated_products']:
  # Products can consolidate several SKU identities; retain original member keys.
  members=product.get('source_members') or [product]
  keys=set()
  for member in members:
   channel='yandex_market' if member['marketplace']=='yandex' else member['marketplace']
   if member['sku']=='__unallocated__':continue
   if channel=='wb':
    for barcode in member.get('barcodes',[]):keys.add((channel,str(member['sku']),str(barcode).zfill(14)))
   else:keys.add((channel,str(member['sku'])))
  # Multi-SKU financial products cannot be assigned in full to each variant.
  if len(keys)==1:indexed[next(iter(keys))].append(product)
 scenarios=defaultdict(list)
 for scenario in result['scenarios']:
  channel='yandex_market' if scenario['marketplace']=='yandex' else scenario['marketplace']
  scenarios[channel,str(scenario['sku'])].append(scenario)
 for row in rows:
  out={};known=[];missing=[]
  for channel in ['wb','ozon','yandex_market','lamoda']:
   links=[l for l in row['links'] if l['channel']==channel]
   metric={'rub':None,'pct':None,'detail':'Нет связи с площадкой'}
   if links:
    metric['detail']='Конфликт связи'
    if len(links)==1 and links[0]['status']!='conflict':
     link=links[0]
     if mode=='actual':
      if channel=='wb':
       groups=[]
       for barcode in link['data'].get('valid_barcodes',[]):groups+=indexed.get((channel,str(link['data']['parent']),str(barcode).zfill(14)),[])
       groups=list({p['key']:p for p in groups}.values())
      else:groups=indexed.get((channel,str(link['key'])),[])
      metric['detail']='Нет финансовых операций по варианту за период' if not groups else 'Нет подтверждённой себестоимости на даты операций или части начислений'
      if groups and all(p.get('contribution_actual') is not None for p in groups):
       revenue=sum(Decimal(str(p['revenue'] or 0)) for p in groups)
       rub=sum(Decimal(str(p['contribution_actual'])) for p in groups)
       metric.update(rub=rub,pct=rub/revenue*100 if revenue>0 else None,revenue=revenue,
        detail='За период '+str(start)+' — '+str(end)+'; по учтённым затратам, до налогов и внешних расходов. Последняя операция '+max(p['last_date'] for p in groups))
     else:
      candidates=scenarios.get((channel,str(link['data']['parent'] if channel=='wb' else link['key'])),[])
      price=link.get('price')
      metric['detail']='Нет сохранённого сценария юнит-экономики'
      if len(candidates)>1:metric['detail']='Несколько сценариев/схем: единая маржа не определена'
      elif len(candidates)==1:
       metric['detail']='Нет сопоставимой текущей цены варианта'
       if price and price.get('value') is not None and price.get('comparable') and not price.get('stale'):
        inputs={**candidates[0]['inputs'],'price':price['value']}
        calculation=calculate(inputs)
        metric['detail']='Не заполнены параметры: '+', '.join(calculation.get('missing_labels',[]))
        if calculation['status']=='calculated':
         current=calculation['current'];metric.update(rub=current['profit'],pct=current['margin_pct'],
          detail='На единицу; текущая цена от '+price['date']+'; сохранённые параметры сценария от '+str(candidates[0]['created_at']))
   out[channel]=metric
   row['totals']['margin_'+channel+'_rub']=metric['rub'];row['totals']['margin_'+channel+'_pct']=metric['pct']
   if links:
    if metric['rub'] is None:missing.append(channel)
    else:known.append(metric)
  total={'rub':None,'pct':None,'detail':'Расчётная маржа — на единицу по площадкам; суммы площадок не складываются' if mode=='calculated' else 'Нет подтверждённого расчёта по варианту','partial':bool(missing)}
  if mode=='actual' and known:
   rub=sum(Decimal(str(m['rub'])) for m in known);revenue=sum(m['revenue'] for m in known)
   total.update(rub=rub,pct=rub/revenue*100 if revenue>0 else None,detail='По учтённым затратам, до налогов и внешних расходов'+('; отсутствуют: '+', '.join(missing) if missing else ''))
  row['margins']={'mode':mode,'channels':out,'total':total}
  row['totals']['margin_rub']=total['rub'];row['totals']['margin_pct']=total['pct']
