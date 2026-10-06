"""Read-only retail adapters for existing TREND report contracts.

All facts come from the VPS PostgreSQL. Never query production 1C from a report.
"""
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict
from urllib.parse import parse_qs
import calendar
import psycopg2
from psycopg2.extras import RealDictCursor

NOTICE = 'Проверенные продажи по чекам. Возвраты и себестоимость ещё не сверены; чистая выручка и прибыль не рассчитаны.'
SOURCE = '1С Розница · проведённые чеки · PostgreSQL'
SOURCE_KEY = '1c:4581e31cb63342fe96b05c3e1cc608b4'
SOURCE_TABLES = ('dbo._AccumRg44751','dbo._Document949','dbo._Reference333','dbo._Reference558')
STORES = {'817900155D321E0311EA04897619B7D8':'Грибоедова, 18',
 '820900155D321E0311EB362502AF06E2':'Авиапарк',
 'BAA00050569E751211EC1D3088CECBE6':'Афимолл'}
SUPPORTED = {'/api/filters','/api/stats','/api/product-stats','/api/sku-stats','/api/summary',
 '/api/product-summary','/api/sku-summary','/api/product-options',
 '/api/planfact-funnel-matrix','/api/sales-order-days','/api/inventory-history','/api/km-trade/pl'}

def clean(v):
    if isinstance(v, Decimal): return float(v)
    if isinstance(v,(date,)): return v.isoformat()
    if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [clean(x) for x in v]
    return v

def context(parsed):
    q=parse_qs(parsed.query)
    return q,lambda key, default='':q.get(key,[default])[0]

def fetch_rows(app, parsed):
    q,get=context(parsed)
    month=get('month',date.today().isoformat()[:7])[:7]
    start=date.fromisoformat(get('date_from',month+'-01'))
    finish=date.fromisoformat(get('date_to',date.today().isoformat()))
    if parsed.path=='/api/planfact-funnel-matrix':
        start=date.fromisoformat(month+'-01')
        finish=min(finish,date(start.year,start.month,calendar.monthrange(start.year,start.month)[1]))
    if finish < start or (finish-start).days>731: raise ValueError('Выберите период не больше двух лет')
    store=get('store')
    if store and store not in STORES: raise ValueError('Неизвестная точка розницы')
    with psycopg2.connect(**app.read_db_config('toptop'),cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as c:
            c.execute("SET LOCAL statement_timeout='8s'")
            c.execute('SELECT metadata,loaded_at,row_count,checked_receipts FROM retail_1c.import_runs ORDER BY loaded_at DESC LIMIT 1')
            imported=dict(c.fetchone() or {})
            c.execute("SELECT max(period)::date AS cutoff FROM retail_1c.sales WHERE channel='retail'")
            cutoff=c.fetchone()['cutoff']
            c.execute("""SELECT * FROM retail_1c.sales WHERE channel='retail'
                AND period >= %s AND period < %s AND (%s='' OR store_id=%s) ORDER BY period,recorder_id,line_no""",
                (start-timedelta(days=56),finish+timedelta(days=1),store,store))
            rows=[dict(r) for r in c.fetchall()]
    return get,start,finish,cutoff,rows,imported

def handle(app, handler, parsed):
    from trend_file_sales import handle as handle_file_sales
    if handle_file_sales(app,handler,parsed): return True
    q,get=context(parsed)
    channel=get('sales_channel')
    if not channel or not parsed.path.startswith('/api/') or parsed.path.startswith('/api/admin/') or parsed.path=='/api/health': return False
    if channel not in {'retail','online','wholesale'}:
        handler.send_json({'ok':False,'error':'Неизвестный канал продаж'},status=400); return True
    identity=app.CURRENT_ACCESS_USER.get() or {}
    if (not identity.get('is_admin') and 'toptop' not in identity.get('clients',[])) or app.current_client_key()!='toptop' or get('client','toptop')!='toptop':
        handler.send_json({'ok':False,'error':'Канал доступен только в контексте TOPTOP'},status=403); return True
    if not identity.get('is_admin'):
        from data_access import permits
        if not all(permits(identity.get('data_access'),SOURCE_KEY,table) for table in SOURCE_TABLES):
            handler.send_json({'ok':False,'error':'Нет доступа к таблицам источника 1С Розница'},status=403); return True
    if channel!='retail' or parsed.path not in SUPPORTED:
        handler.send_json({'ok':False,'error':'Для этого отчёта данные канала ещё не подключены. Источник маркетплейсов не используется.','sales_channel':channel},status=422); return True
    try:
        payload=build(app,parsed)
        handler.send_json(clean(payload))
    except (ValueError,psycopg2.Error):
        handler.send_json({'ok':False,'error':'Не удалось получить данные розницы для выбранного периода'},status=400)
    return True

def build(app,parsed):
    get,start,finish,cutoff,rows,imported=fetch_rows(app,parsed)
    market=get('marketplace','ozon')
    base={'client':'toptop','marketplace':market,'sales_channel':'retail','source':SOURCE,'partial':True,'notice':NOTICE}
    path=parsed.path
    selected=[r for r in rows if start<=r['period'].date()<=finish]
    sku=get('inventory_skus') or get('article')
    if sku: selected=[r for r in selected if sku.lower() in (r['article']+' '+r['product_id']+' '+r['variant_id']).lower()]
    product=get('product')
    if product: selected=[r for r in selected if r['product_name']==product]
    cats=parse_qs(parsed.query).get('categories',[])
    if cats: selected=[r for r in selected if r['store_name'] in cats]
    total=sum((r['revenue'] for r in selected),Decimal(0))
    units=sum((r['quantity'] for r in selected),Decimal(0))
    receipts=len({r['recorder_id'] for r in selected})
    if path=='/api/filters':
        return {**base,'date_from':imported['metadata']['date_from'],'date_to':cutoff,
          'category_names':list(STORES.values()),'product_names':sorted({r['product_name'] for r in selected})[:500],
          'marketplaces':[{'id':'ozon','label':'Розница'}],
          'stores':[{'id':k,'label':v} for k,v in STORES.items()],
          'months':[{'month':m+'-01','date_from':m+'-01','date_to':m+f'-{calendar.monthrange(int(m[:4]),int(m[5:]))[1]}'} for m in ['2026-08','2026-09']],
          'loaded_at':str(imported['loaded_at'])}
    if path=='/api/product-options':return {**base,'product_names':sorted({r['product_name'] for r in selected if get('q').lower() in r['product_name'].lower()})[:100]}
    if path.endswith('summary'):
        return {**base,'zakazano_rub':total,'zakazano_sht':units,'sku_count':len({(r['product_id'],r['variant_id']) for r in selected}),
                'gross_sales':total,'receipts':receipts,'average_receipt':total/receipts if receipts else None,
                'total_stock_qty':None,'net_revenue':None,'profit':None}
    if path=='/api/inventory-history':return {**base,'rows':[],'data_status':'unavailable','data_message':'Остатки требуют сверки начального сальдо и движений 1С.'}
    if path=='/api/km-trade/pl':return {**base,'available':False,'date_from':str(start),'date_to':str(finish),'totals':{},'statement':[],'model_notice':NOTICE}
    if path=='/api/planfact-funnel-matrix':
        analysis=min(finish,cutoff) if cutoff else start
        month=get('month',str(start)[:7])[:7]+'-01'
        mrows=[r for r in rows if date.fromisoformat(month)<=r['period'].date()<=analysis]
        rev=sum((r['revenue'] for r in mrows),Decimal(0)); count=len({r['recorder_id'] for r in mrows})
        values={'bought_revenue_rub':rev if mrows else None,'retail_receipts':count if mrows else None,
          'retail_units':sum(r['quantity'] for r in mrows) if mrows else None,'retail_average_receipt':rev/count if count else None}
        daily=defaultdict(Decimal)
        for r in rows:daily[r['period'].date()]+=r['revenue']
        metrics=[]
        for key,val in values.items():
            metrics.append({'id':key,'values':{'fact_mtd':val,'plan_month':None,'run_rate':None},
              'availability':{'status':'available' if val is not None else 'no_data','reason':'Продажи до возвратов по проведённым чекам'},
              'source':{'cutoff':str(analysis)},'trend_28d':[{'date':str(analysis-timedelta(days=i)), 'value':daily.get(analysis-timedelta(days=i))} for i in range(27,-1,-1)] if key=='bought_revenue_rub' else [],
              'trend_previous_28d':[{'date':str(analysis-timedelta(days=i)),'value':daily.get(analysis-timedelta(days=i))} for i in range(55,27,-1)] if key=='bought_revenue_rub' else []})
        return {**base,'month':month,'analysis_date':str(analysis),'rows':metrics,
          'percentage_scale':{'storage':'percent'},'coverage':{'contract_metric_count':8,'fact_metric_count':len(values) if mrows else 0,'null_fact_metric_count':4 if mrows else 8},
          'data_quality':{'status':'partial','incoherent_stages':[],'analysis_freshness_days':(date.today()-analysis).days}}
    if path.endswith('stats'):
        grouped=defaultdict(list); dashboard=get('dashboard','abc')
        for r in selected:
            key=r['store_name'] if dashboard=='abc' else (r['product_id'],r['variant_id']) if dashboard=='sku' else r['product_id']
            grouped[str(key)].append(r)
        result=[]
        for records in grouped.values():
            first=records[0]
            result.append({'category_name':first['store_name'] if dashboard=='abc' else first['product_name'],
             'product_name':first['product_name'],'naimenovanie':first['product_name'],'artikul_wb':first['article'],'article':first['article'],
             'zakazano_rub':sum(r['revenue'] for r in records),'zakazano_sht':sum(r['quantity'] for r in records),
             'sku_count':len({(r['product_id'],r['variant_id']) for r in records}), 'receipts':len({r['recorder_id'] for r in records}),'total_stock_qty':None})
        sort=get('sort_col','zakazano_rub'); sort=sort if sort in {'zakazano_rub','zakazano_sht','sku_count','receipts','category_name','article'} else 'zakazano_rub'
        cumulative=Decimal(0)
        for row in sorted(result,key=lambda r:r['zakazano_rub'],reverse=True):
            share=cumulative/total if total else Decimal(0)
            row['abc_combined']='A' if share<Decimal('.8') else 'B' if share<Decimal('.95') else 'C'
            cumulative+=row['zakazano_rub']
            row['orders_share_pct']=row['zakazano_rub']/total*100 if total else None
            row['avg_price_rub']=row['zakazano_rub']/row['zakazano_sht'] if row['zakazano_sht'] else None
        result.sort(key=lambda r:r.get(sort) or 0,reverse=get('sort_dir','desc')=='desc')
        limit=max(1,min(int(get('limit','50')),500)); page=max(1,int(get('page','1')))
        return {**base,'rows':result[(page-1)*limit:page*limit],'columns':[
          {'key':'category_name','label':'Точка' if dashboard=='abc' else 'Товар','type':'text'},
          {'key':'article','label':'Артикул','type':'text'},
          {'key':'zakazano_rub','label':'Продажи до возвратов, ₽','type':'number'},
          {'key':'zakazano_sht','label':'Продано, шт.','type':'number'},
          {'key':'receipts','label':'Чеков','type':'number'},{'key':'sku_count','label':'SKU','type':'number'}],
          'page':page,'page_size':limit,'total':len(result),'total_pages':max(1,(len(result)+limit-1)//limit),'sort_col':sort,'sort_dir':get('sort_dir','desc'),
          'data_status':'partial','data_message':NOTICE}
    if path=='/api/sales-order-days':
        days=[]; grouped=defaultdict(list)
        for r in selected:grouped[r['period'].date()].append(r)
        for i in range((finish-start).days+1):
            day=start+timedelta(days=i); rs=grouped.get(day,[]); qty=sum(r['quantity'] for r in rs);amount=sum(r['revenue'] for r in rs)
            days.append({'date':str(day),'orders':len({r['recorder_id'] for r in rs}) if rs else None,
             'units':qty if rs else None,'amount':amount if rs else None,'priced':len(rs) if rs else None,
             'buyer_mean':amount/qty if qty else None,'seller_mean':None,'spp_mean':None,'avg_ordered_unit':amount/qty if qty else None,'cancelled':None,'source_rows':len(rs)})
        regions=[]
        for store in STORES.values():
            rs=[r for r in selected if r['store_name']==store]
            if rs:regions.append({'region':store,'orders':len({r['recorder_id'] for r in rs}),'units':sum(r['quantity'] for r in rs),'amount':sum(r['revenue'] for r in rs)})
        detail=selected
        if get('detail_date'):detail=[r for r in detail if str(r['period'].date())==get('detail_date')]
        if get('sales_region'):detail=[r for r in detail if r['store_name']==get('sales_region')]
        if get('heat_weekday'):detail=[r for r in detail if r['period'].weekday()==int(get('heat_weekday'))]
        if get('heat_hour'):detail=[r for r in detail if r['period'].hour==int(get('heat_hour'))]
        heat=defaultdict(list)
        for r in selected:heat[(r['period'].weekday(),r['period'].hour)].append(r)
        return {**base,'status':'partial','days':days,'coverage':{'first_date':str(min(grouped)) if grouped else None,'last_date':str(max(grouped)) if grouped else None,
          'observed_days':len(grouped),'expected_days':len(days),'order_identity_available':True,'buyer_price_available':True},
          'geography':{'regions':regions,'known_orders':receipts,'unknown_orders':0,'selected_region':get('sales_region'),'top_skus':[]},
          'heatmap':{'cells':[{'weekday':k[0],'hour':k[1],'orders':len({r['recorder_id'] for r in rs}),'units':sum(r['quantity'] for r in rs)} for k,rs in heat.items()],'known_orders':receipts,'unknown_orders':0,'timezone':'Время документов 1С'},
          'details':[{'order_id':r['recorder_id'],'item_id':r['recorder_id']+':'+str(r['line_no']),'day':str(r['period'].date()),'ordered_at':str(r['period']),
            'sku':r['article'] or r['product_id'],'product':r['product_name'],'units':r['quantity'],'amount':r['revenue'],'seller_price':None,'spp':None,'cancelled':False,'region':r['store_name']} for r in detail[:300]]}
    raise ValueError('Unsupported contract')
