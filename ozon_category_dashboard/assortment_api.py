"""Assortment reports read only PostgreSQL; variant mapping is built separately."""
import json,calendar,math,statistics
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from urllib.parse import parse_qs
import psycopg2
from psycopg2.extras import RealDictCursor
REPORTS={'assortmentProducts','assortmentPrices','assortmentABC','assortmentXYZ'}
CHANNELS={'wb':'WB','ozon':'Ozon','yandex_market':'Яндекс Маркет','lamoda':'Lamoda','retail':'Розница','online':'Интернет магазин','wholesale':'Опт'}

def clean(v):
 if isinstance(v,Decimal):return float(v)
 if isinstance(v,date):return v.isoformat()
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [clean(x) for x in v]
 return v

def report_id(parsed):
 p=parse_qs(parsed.query);r=p.get('dashboard',['assortmentProducts'])[0]
 return r if r in REPORTS else 'assortmentProducts'

def handle(app,h,parsed):
 if parsed.path!='/api/assortment':return False
 identity=app.CURRENT_ACCESS_USER.get() or {};client=app.current_client_key()
 if not identity.get('is_admin') and client not in identity.get('clients',[]):h.send_json({'ok':False,'error':'Нет доступа к аккаунту'},status=403);return True
 try:h.send_json(clean(build(app,parsed,identity)))
 except (ValueError,psycopg2.Error):h.send_json({'ok':False,'error':'Не удалось прочитать отчёт ассортимента. Проверьте период и доступность источников.'},status=400)
 return True

def build(app,parsed,identity):
 q=parse_qs(parsed.query);g=lambda key,default='':q.get(key,[default])[0]
 client=app.current_client_key();report=report_id(parsed)
 page=max(1,int(g('page','1')));limit=50;search=g('q').strip()[:120];status=g('status');market=g('market','wb')
 sort_col=g('sort_col');sort_dir=g('sort_dir','asc')
 totals_columns={'orders_rub','orders_units','stock_units','margin_rub','margin_pct',*(f'{kind}_{ch}_{unit}' for ch in ['wb','ozon','yandex_market','lamoda'] for kind,unit in [('orders','rub'),('orders','units'),('stock','units'),('margin','rub'),('margin','pct')])}
 if sort_col not in {'','product','status',*CHANNELS,*totals_columns} or sort_dir not in {'asc','desc'}:raise ValueError('Invalid sort')
 if sort_col in totals_columns and report!='assortmentPrices':raise ValueError('Invalid totals sort')
 margin_mode=g('margin_mode','actual')
 if margin_mode not in {'actual','calculated'}:raise ValueError('Invalid margin mode')
 days=int(g('days','29'))
 if days not in {7,29,90}:raise ValueError('Invalid days')
 column_filters={}
 if report=='assortmentPrices' and g('column_filters'):
  try:column_filters=json.loads(g('column_filters'))
  except (TypeError,json.JSONDecodeError):raise ValueError('Invalid column filters')
  if not isinstance(column_filters,dict) or len(column_filters)>len(CHANNELS)+len(totals_columns)+2:raise ValueError('Invalid column filters')
  for key,config in column_filters.items():
   if key not in {'product','status',*CHANNELS,*totals_columns} or not isinstance(config,dict):raise ValueError('Invalid column filter')
   op=config.get('op');value=str(config.get('value') or '').strip()[:120]
   valid={'contains','not_contains','eq','neq'} if key in {'product','status'} else {'with','without','eq','neq','gt','gte','lt','lte'}
   if op not in valid or (op not in {'with','without'} and not value):raise ValueError('Invalid column filter')
   if key in {*CHANNELS,*totals_columns} and op not in {'with','without'}:
    try:
     if not Decimal(value.replace(',','.')).is_finite():raise ValueError()
    except Exception:raise ValueError('Invalid price value')
   column_filters[key]={'op':op,'value':value}
 price_filters={}
 if report=='assortmentPrices' and g('price_filters'):
  try:price_filters=json.loads(g('price_filters'))
  except (TypeError,json.JSONDecodeError):raise ValueError('Invalid price filters')
  if not isinstance(price_filters,dict) or len(price_filters)>len(CHANNELS) or any(k not in CHANNELS or v not in {'with','without'} for k,v in price_filters.items()):
   raise ValueError('Invalid price filters')
 include_retail=bool(identity.get('is_admin'))
 if not include_retail:
  from data_access import permits
  from one_c_import import access
  include_retail=access(app,identity,'prices' if report=='assortmentPrices' else 'catalog')
 with psycopg2.connect(**app.read_db_config(client),cursor_factory=RealDictCursor) as conn:
  conn.set_session(readonly=True)
  with conn.cursor() as c:
   c.execute("SET LOCAL statement_timeout='10s'")
   if report in {'assortmentABC','assortmentXYZ'}:return classify(c,client,report,g,include_retail)
   c.execute('SELECT finished_at,summary FROM assortment_master.runs ORDER BY id DESC LIMIT 1');run=dict(c.fetchone() or {})
   allowed=list(CHANNELS) if include_retail else [k for k in CHANNELS if k!='retail']
   where="channel=ANY(%s)";args=[allowed]
   # Filter at master level, retaining every authorized channel on the matching row.
   c.execute('''SELECT master_id::text,jsonb_agg(jsonb_build_object('channel',channel,'key',source_key,
    'status',status,'data',payload) ORDER BY channel) AS links FROM assortment_master.links
    WHERE channel=ANY(%s) GROUP BY master_id ORDER BY master_id''',(allowed,))
   allrows=[dict(r) for r in c.fetchall()]
   counts={'matched':0,'unmatched':0,'conflict':0}
   selected=[]
   for row in allrows:
    states={x['status'] for x in row['links']};row['status']='conflict' if 'conflict' in states else 'matched' if len(row['links'])>1 else 'unmatched'
    counts[row['status']]+=1
    if status and row['status']!=status:continue
    if search and search.casefold() not in json.dumps(row['links'],ensure_ascii=False).casefold():continue
    selected.append(row)
   totals_period={}
   if report=='assortmentPrices':
    from assortment_totals import attach_totals
    # Default page needs metrics for its 50 visible rows only. Global filters/sorts still use all rows.
    totals_rows=selected if sort_col or column_filters or price_filters else selected[(page-1)*limit:page*limit]
    totals_period=attach_totals(c,totals_rows,days,client)
    for row in totals_rows:
     for channel in ['wb','ozon','yandex_market','lamoda']:
      metrics=row.get('channel_totals',{}).get(channel,{})
      for key in ['orders_rub','orders_units','stock_units']:
       kind,unit=key.split('_');row['totals'][kind+'_'+channel+'_'+unit]=metrics.get(key)
    if g('include_margin')=='1' or sort_col.startswith('margin_') or any(k.startswith('margin_') for k in column_filters):
     from assortment_margins import attach_margins
     attach_prices(c,totals_rows)
     attach_margins(app,totals_rows,days,client,margin_mode)
   for channel,mode in price_filters.items():
    column_filters.setdefault(channel,{'op':mode,'value':''})
   price_maps={}
   def price_for(row,channel):
    if channel not in price_maps:price_maps[channel]=price_values(c,channel,selected)
    for link in row['links']:
     if link['channel']==channel:
      return price_maps[channel].get(str(link['data']['parent'] if channel=='wb' else link['key']))
    return None
   for column,config in column_filters.items():
    op,value=config['op'],config['value']
    if column in totals_columns:
     selected=[row for row in selected if price_filter_matches(row['totals'][column],op,value)]
    elif column in CHANNELS:
     price_maps[column]=price_values(c,column,selected)
     selected=[row for row in selected if price_filter_matches(price_for(row,column),op,value)]
    else:
     selected=[row for row in selected if text_filter_matches(
      row['status'] if column=='status' else str(row['links'][0]['data'].get('name') or ''),op,value)]
   if sort_col in totals_columns:
    available=[row for row in selected if row['totals'][sort_col] is not None]
    missing=[row for row in selected if row['totals'][sort_col] is None]
    available.sort(key=lambda row:(row['totals'][sort_col],row['master_id']),reverse=sort_dir=='desc')
    selected=available+missing
   elif sort_col in CHANNELS:
    price_maps[sort_col]=price_values(c,sort_col,selected)
    available=[row for row in selected if price_for(row,sort_col) is not None]
    missing=[row for row in selected if price_for(row,sort_col) is None]
    available.sort(key=lambda row:(price_for(row,sort_col),row['master_id']),reverse=sort_dir=='desc')
    selected=available+missing
   elif sort_col in {'product','status'}:
    selected.sort(key=lambda row:(str(row['status'] if sort_col=='status' else row['links'][0]['data'].get('name') or '').casefold(),row['master_id']),reverse=sort_dir=='desc')
   total=len(selected);selected=selected[(page-1)*limit:page*limit]
   if report=='assortmentPrices':attach_prices(c,selected)
   return {'client':client,'report':report,'rows':selected,'total':total,'page':page,'pages':max(1,math.ceil(total/limit)),
    'counts':counts,'channels':CHANNELS,'retail_access':include_retail,'updated_at':str(run.get('finished_at') or ''),'totals_period':totals_period,
    'limitations':['Связь по уникальному валидному GTIN на уровне размера/варианта. Совпадение названия или артикула не объединяет товары.',
      'Яндекс: штрихкоды пока не найдены в загруженном справочнике. Ассортимент и цены Розницы обновляются в разделе «Импорт из 1С». Вид цен для колонки Розницы выбирается там же; доступ требует прав на соответствующие таблицы. Интернет-магазин и опт пока не подключены.',
      'Цены показываются с типом и датой. Сравнительная заливка применяется только к свежим сопоставимым ценам одного варианта в рублях.']}


def text_filter_matches(actual,op,value):
 actual=str(actual or '').casefold();value=value.casefold()
 return {'contains':lambda:value in actual,'not_contains':lambda:value not in actual,
  'eq':lambda:actual==value,'neq':lambda:actual!=value}[op]()


def price_filter_matches(actual,op,value):
 if op=='with':return actual is not None
 if op=='without':return actual is None
 if actual is None:return False
 target=Decimal(value.replace(',','.'))
 return {'eq':lambda:actual==target,'neq':lambda:actual!=target,
  'gt':lambda:actual>target,'gte':lambda:actual>=target,
  'lt':lambda:actual<target,'lte':lambda:actual<=target}[op]()


def price_values(c,channel,rows):
 keys=sorted({str(link['data']['parent'] if channel=='wb' else link['key'])
  for row in rows for link in row['links'] if link['channel']==channel})
 if not keys:return {}
 if channel=='lamoda':
  c.execute('''SELECT DISTINCT ON(lamoda_sku) lamoda_sku,price FROM mv_lamoda_catalog_flat
   WHERE lamoda_sku=ANY(%s) ORDER BY lamoda_sku,snapshot_date DESC''',(keys,))
  return {str(r['lamoda_sku']):Decimal(str(r['price']))/100 for r in c.fetchall() if r['price'] is not None}
 if channel=='ozon':
  c.execute('''WITH catalog AS (
   SELECT DISTINCT ON(sku) sku,product_id FROM ozon_cat_products WHERE sku=ANY(%s)
   ORDER BY sku,coalesce(api_updated_at,updated_at,imported_at) DESC NULLS LAST)
   SELECT DISTINCT ON(c.sku) c.sku,p.price FROM catalog c
   JOIN ozon_product_price_snapshots p ON p.product_id=c.product_id::text
   ORDER BY c.sku,p.snapshot_date DESC''',(keys,))
  return {str(r['sku']):Decimal(str(r['price'])) for r in c.fetchall() if r['price'] is not None}
 if channel=='wb':
  c.execute('''SELECT DISTINCT ON(wb_nmid) wb_nmid,price_min_rub FROM wb_stock_api_current
   WHERE wb_nmid=ANY(%s) ORDER BY wb_nmid,snapshot_date DESC''',(keys,))
  return {str(r['wb_nmid']):Decimal(str(r['price_min_rub'])) for r in c.fetchall() if r['price_min_rub'] is not None}
 if channel=='yandex_market':
  c.execute('''SELECT DISTINCT ON(offer_id) offer_id,price FROM yandex_fact_prices
   WHERE offer_id=ANY(%s) ORDER BY offer_id,snapshot_date DESC''',(keys,))
  return {str(r['offer_id']):Decimal(str(r['price'])) for r in c.fetchall() if r['price'] is not None}
 if channel=='retail':
  return {key:Decimal(str(value['value'])) for key,value in retail_prices(c,keys).items()}
 return {}

def retail_prices(c,keys):
 c.execute("SELECT value FROM one_c_import.settings WHERE key='retail_price_type'")
 setting=c.fetchone()
 if not setting or not setting['value']:return {}
 c.execute("""SELECT product_id,variant_id,payload,loaded_at FROM one_c_import.current_prices
  WHERE database_name='1c_retail_prod' AND price_type_id=%s AND (product_id||':'||variant_id=ANY(%s) OR variant_id=%s)""",(setting['value'],keys,'0'*32))
 items={(r['product_id'],r['variant_id']):(r['payload'],r['loaded_at']) for r in c.fetchall()}
 result={}
 for key in keys:
  product,variant=key.split(':',1);item=items.get((product,variant))
  if not item:
   base=items.get((product,'0'*32))
   if base and base[0].get('inherit'):item=base
  if item and item[0].get('active'):
   p,loaded=item;result[key]={'value':Decimal(p['value']),'date':p['date'][:10],'checked_date':str(loaded.date()),'currency':p['currency'],'basis':p['type'],'comparable':False}
 return result

def attach_prices(c,rows):
 keys=defaultdict(list)
 for r in rows:
  for link in r['links']:keys[link['channel']].append(link['data']['parent'] if link['channel']=='wb' else link['key'])
 prices={}
 if keys['lamoda']:
  c.execute('''SELECT DISTINCT ON(lamoda_sku) lamoda_sku,price/100 AS price,currency,snapshot_date,price_status
    FROM mv_lamoda_catalog_flat WHERE lamoda_sku=ANY(%s) ORDER BY lamoda_sku,snapshot_date DESC''',(keys['lamoda'],))
  for x in c.fetchall():prices[('lamoda',x['lamoda_sku'])]={'value':x['price'],'date':str(x['snapshot_date']),'currency':x['currency'],'basis':'Базовая цена продавца','comparable':x['price_status']=='OK'}
 if keys['ozon']:
  # Price snapshot's legacy sku column actually contains product_id; catalog SKU is a different key.
  c.execute('''WITH catalog AS (
    SELECT DISTINCT ON(sku) sku,product_id FROM ozon_cat_products WHERE sku=ANY(%s)
    ORDER BY sku,coalesce(api_updated_at,updated_at,imported_at) DESC NULLS LAST)
    SELECT DISTINCT ON(c.sku) c.sku,p.price,p.currency,p.snapshot_date FROM catalog c
    JOIN ozon_product_price_snapshots p ON p.product_id=c.product_id::text
    ORDER BY c.sku,p.snapshot_date DESC''',(keys['ozon'],))
  for x in c.fetchall():prices[('ozon',x['sku'])]={'value':x['price'],'date':str(x['snapshot_date']),'currency':x['currency'],'basis':'Цена продавца','comparable':True}
 if keys['wb']:
  c.execute('''SELECT DISTINCT ON(wb_nmid) wb_nmid,price_min_rub,price_max_rub,snapshot_date FROM wb_stock_api_current
   WHERE wb_nmid=ANY(%s) ORDER BY wb_nmid,snapshot_date DESC''',(keys['wb'],))
  for x in c.fetchall():prices[('wb',x['wb_nmid'])]={'value':x['price_min_rub'],'max':x['price_max_rub'],'date':str(x['snapshot_date']),'currency':'RUB','basis':'Диапазон карточки WB','comparable':False}
 if keys['yandex_market']:
  c.execute('''SELECT DISTINCT ON(offer_id) offer_id,price,currency,snapshot_date FROM yandex_fact_prices
   WHERE offer_id=ANY(%s) ORDER BY offer_id,snapshot_date DESC''',(keys['yandex_market'],))
  for x in c.fetchall():prices[('yandex_market',x['offer_id'])]={'value':x['price'],'date':str(x['snapshot_date']),'currency':x['currency'],'basis':'Цена продавца','comparable':True}
 if keys['retail']:
  for key,value in retail_prices(c,keys['retail']).items():prices[('retail',key)]=value
 for r in rows:
  comparable=[]
  for link in r['links']:
   p=prices.get((link['channel'],link['data']['parent'] if link['channel']=='wb' else link['key']))
   link['price']=p
   if p:
    p['stale']=(date.today()-date.fromisoformat(p.get('checked_date',p['date']))).days>7
    if p['comparable'] and not p['stale'] and p['currency'] in {'RUB','RUR'} and p['value'] and p['value']>0 and r['status']=='matched':comparable.append(p)
  if len(comparable)>1:
   low=min(x['value'] for x in comparable);high=max(x['value'] for x in comparable)
   for p in comparable:p['tone']='low' if p['value']==low and low<high else 'high' if p['value']==high and low<high else 'same'

def classify(c,client,report,g,include_retail):
 market=g('market','wb')
 if market not in {'wb','ozon','retail'}:raise ValueError('Unsupported analytical channel')
 if market=='retail' and (client!='toptop' or not include_retail):raise ValueError('Retail not allowed')
 # Eight completed Monday-Sunday weeks, keeping source gaps explicit.
 today=date.today();finish=today-timedelta(days=today.weekday()+1)
 finish=date.fromisoformat(g('date_to',str(finish)))
 start=date.fromisoformat(g('date_from',str(finish-timedelta(days=55))))
 if finish<start or (finish-start).days>365:raise ValueError('Period invalid')
 if market=='retail':
  c.execute('''SELECT period::date AS day,product_id||':'||variant_id AS sku,max(product_name) AS name,max(article) AS article,
   sum(quantity) AS units,sum(revenue) AS revenue,1 AS n FROM retail_1c.sales WHERE channel='retail' AND period>=%s AND period<%s
   GROUP BY 1,2''',(start,finish+timedelta(days=1)))
 else:
  table,idcol,namecol,articlecol=('wb_funnel_daily','wb_nmid','product_name','seller_article') if market=='wb' else ('ozon_funnel_daily','sku','product_name','seller_article')
  c.execute(f'''SELECT report_date AS day,{idcol}::text AS sku,max({namecol}) AS name,max({articlecol}) AS article,
   sum(ordered_units) AS units,sum(ordered_amount_rub) AS revenue,count(*) AS n
   FROM {table} WHERE report_date BETWEEN %s AND %s GROUP BY 1,2''',(start,finish))
 data=[dict(r) for r in c.fetchall()];groups=defaultdict(list)
 for r in data:groups[r['sku']].append(r)
 total=sum(max(0,sum((r['revenue'] or 0) for r in rs)) for rs in groups.values() if all(r['n']==1 for r in rs));results=[]
 expected=(finish-start).days+1
 for sku,rs in groups.items():
  revenue=sum((r['revenue'] or 0) for r in rs);units=sum((r['units'] or 0) for r in rs)
  duplicate=any(r['n']>1 for r in rs);coverage=len({r['day'] for r in rs})
  weeks=defaultdict(float)
  for r in rs:weeks[r['day']-timedelta(days=r['day'].weekday())]+=float(r['units'] or 0)
  complete=start.weekday()==0 and finish.weekday()==6 and expected>=56 and coverage==expected and not duplicate
  avg=statistics.mean(weeks.values()) if weeks else 0
  cv=statistics.pstdev(weeks.values())/avg if complete and avg>0 else None
  xyz='X' if cv is not None and cv<=.1 else 'Y' if cv is not None and cv<=.25 else 'Z' if cv is not None else None
  results.append({'sku':sku,'name':rs[0]['name'],'article':rs[0]['article'],'revenue':None if duplicate else revenue,
   'units':None if duplicate else units,'share':float(revenue/total*100) if total and not duplicate and revenue>=0 else None,
   'abc':None,'xyz':xyz,'cv':cv*100 if cv is not None else None,'days':coverage,'expected_days':expected,'weeks':len(weeks),
   'reason':'Дубли источника' if duplicate else 'Период должен содержать минимум 8 полных недель' if start.weekday()!=0 or finish.weekday()!=6 or expected<56 else 'Нет записей за каждый день; пропуски не заменены нулями' if coverage!=expected else 'Нет положительного среднего спроса' if not avg else '',
   'duplicate':duplicate})
 results.sort(key=lambda r:r['revenue'] or 0,reverse=True);cumulative=0
 for r in results:
  if not r['duplicate'] and total>0 and r['revenue']>=0:r['abc']='A' if cumulative/total<.8 else 'B' if cumulative/total<.95 else 'C';cumulative+=r['revenue'] or 0
 search=g('q').casefold();selected=[r for r in results if not search or search in (str(r['sku'])+' '+str(r['article'])+' '+str(r['name'])).casefold()]
 status=g('status')
 if status:selected=[r for r in selected if (r['abc'] if report=='assortmentABC' else r['xyz'] or 'unknown')==status]
 page=max(1,int(g('page','1')))
 return {'client':client,'report':report,'market':market,'date_from':str(start),'date_to':str(finish),'rows':selected[(page-1)*50:page*50],
  'total':len(selected),'page':page,'pages':max(1,math.ceil(len(selected)/50)),
  'counts':{'sku':len(results),'classified_xyz':sum(r['xyz'] is not None for r in results),'duplicate_sku':sum(r['duplicate'] for r in results)},
  'updated_at':'','limitations':['ABC: доля суммы заказов (в рознице — продаж до возвратов); границы 80% / 95%. WB — карточка целиком, Ozon и розница — исходный SKU. Каналы не складываются.',
   'XYZ: коэффициент вариации недельного количества; X ≤ 10%, Y ≤ 25%, Z > 25%. Нужно минимум 8 полных недель и запись за каждый день. Отсутствующая запись не считается нулём.',
   'ABC рассчитывается по загруженной части периода. Это не маржинальность; XYZ не рассчитывается при недостаточной истории.']}
