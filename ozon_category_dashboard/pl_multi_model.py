"""Source adapters for Ozon/Yandex and an additive cross-market financial model."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
import hashlib
from km_trade_finance import connect_km
from unit_economics_workspace import checked_config,planning_catalog
from unit_planner_settings import load
from pl_financial_model import D,finalize,aggregate,model_rows,tax_profile,expense_group,update_financial_ratios
from pl_workbench import choose_cost
from pulse_financial_model import read_expenses,allocate_expense

def cost_lookup(catalog,versions):
    from unit_cost_basis import catalog_cost_resolver
    match,current=catalog_cost_resolver(catalog,versions)
    def resolve(market,sku,article):
        amount,source,_=match(market,sku,article)
        return amount,source
    return resolve,len(current)

def pack(days,arts,tax,**extra):
    rows=model_rows(arts);keys=[r['key'] for r in rows if r['unit']!='percent'];periods={}
    for grain in ('day','week','month'):
        groups=defaultdict(list)
        for r in days:
            d=date.fromisoformat(r['date']);key=str(d) if grain=='day' else str(d-timedelta(days=d.weekday())) if grain=='week' else str(d)[:7]
            groups[key].append(r)
        periods[grain]=[aggregate(rs,keys) for rs in groups.values()]
    return dict(rows=rows,periods=periods,total=aggregate(days,keys),tax_profile={k:float(v) if v is not None else None for k,v in tax.items()},tax_ready=tax['incomeTaxPct'] is not None and tax['vatPct'] is not None,basis='current_registry',**extra)

def combine_models(models):
    """Sum displayed marketplace models; rates/margins never sum. Preserve missing parts."""
    import copy
    result=copy.deepcopy(models[0]);result['cost_events']=[];result['marketplaces']=[]
    base=[r for r in result['rows'] if r.get('kind')!='article'];articles=[]
    for model in models:
        market=model['marketplace'];result['marketplaces'].append(dict(marketplace=market,total=model['total'],source_note=model.get('source_note','')))
        for row in model['rows']:
            if row.get('kind')=='article':articles.append(dict(row,key=market+'__'+row['key'],label=model['label']+' · '+row['label']))
        result['cost_events'].extend(dict(r,marketplace=market) for r in model.get('cost_events',[]))
    result['rows']=[]
    for row in base:
        result['rows'].append(row)
        result['rows'].extend(a for a in articles if a.get('group')==row['key'])
    def combined(parts):
        p=copy.deepcopy(parts[0]);p['values']={}
        for row in base:
            k=row['key'];vals=[x['values'].get(k) for x in parts]
            p['values'][k]=float(sum((D(v) for v in vals if v is not None),Decimal(0))) if any(v is not None for v in vals) else None
        update_financial_ratios(p['values'])
        for model,part in zip(models,parts):
            for row in model['rows']:
                if row.get('kind')=='article':p['values'][model['marketplace']+'__'+row['key']]=part['values'].get(row['key'])
        for k in ('missing_cost_units','sku_cost_units','finance_days','days'):p[k]=sum(x[k] for x in parts)
        return p
    result['total']=combined([m['total'] for m in models])
    result['periods']={g:[combined([m['periods'][g][i] for m in models]) for i in range(len(models[0]['periods'][g]))] for g in ('day','week','month')}
    result.pop('reconciliation',None)
    result['marketplace']='all';result['label']='Итого';result['source_note']=' · '.join(m.get('source_note','') for m in models if m.get('source_note'))
    return result

def attach_multi_model(config,payload,raw_tax=None):
    checked_config(config,payload['client']);market=payload['marketplace'];client=payload['client'];start,end=map(date.fromisoformat,(payload['date_from'],payload['date_to']))
    if market=='all':
        from marketplace_pl import selected_payload
        from pl_financial_model import attach_financial_model
        from pl_workbench import attach_workbench
        models=[];products=[]
        supported=[r['marketplace'] for r in payload.get('marketplace_totals',[])]
        from pl_monthly_budgets import read_range, MARKETS
        budgets=read_range(config,client,start,end)
        budget_markets=[m for m in MARKETS if any(r['amounts'].get(m) is not None for b in budgets for r in b['rows'])]
        requested=list(dict.fromkeys(supported+budget_markets))
        for m in requested:
            child=attach_financial_model(config,attach_workbench(config,selected_payload(config,str(start),str(end),client,m,supported),'current'),raw_tax)
            if child.get('financial_model'):
                model=child['financial_model'];model.update(marketplace=m,label={'wb':'WB','ozon':'Ozon','yandex':'Яндекс Маркет'}[m]);models.append(model)
                products.extend(dict(p,marketplace=m) for p in child.get('products',[]))
        if models:
            payload['financial_model']=combine_models(models);payload['products']=products
            payload['financial_model']['excluded_marketplaces']=[m for m in supported if m not in [x['marketplace'] for x in models]]
        return sync_summary(payload)
    if market not in ('ozon','yandex') or payload.get('available') is False:return payload
    tax=tax_profile(raw_tax if raw_tax is not None else (load(config,client).get('settings') or {}).get('global',{}))
    daily={};arts={};trace=[];source_bounds={};sku_costs=defaultdict(lambda:[Decimal(0),0]);unknown=set()
    d=start
    while d<=end:
        daily[str(d)]=dict(date=str(d),orders=None,order_amount=None,buyouts=None,buyout_amount=None,returns=None,return_amount=None,revenue=None,known_cogs=Decimal(0),missing_cost_units=0,sku_cost_units=0,finance_present=False)
        d+=timedelta(days=1)
    def mark(dt):
        r=daily[str(dt)];r['finance_present']=True
        for k in ('buyouts','buyout_amount','returns','return_amount','revenue'):r[k]=D(r[k])
        return r
    def add(dt,label,group,amount):
        r=mark(dt);key=group+'_'+hashlib.sha1(label.encode()).hexdigest()[:12]
        arts[key]=dict(key=key,label=label,group=group,unit='money',kind='article',help='Исходные начисления '+market+'; знак сохранён.')
        if amount is None:unknown.add((str(dt),key));return
        r[key]=D(r.get(key))+D(amount)
    with connect_km(config) as conn,conn.cursor() as cur:
        catalog=planning_catalog(cur,client,[])
        cur.execute('SELECT barcode,amount,valid_from,source_ref FROM unit_cogs_versions WHERE valid_from<=%s',(date.today(),))
        resolve,registry_count=cost_lookup(catalog,cur.fetchall())
        if market=='ozon':
            cur.execute("SELECT operation_date dt,sku,max(article) article,CASE WHEN amount<0 THEN -1 ELSE 1 END sign,sum(abs(quantity)) units,sum(amount) amount FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s AND line_kind='revenue' GROUP BY 1,2,4",(start,end));events=cur.fetchall()
            cur.execute("SELECT operation_date dt,line_kind,coalesce(nullif(type_name,''),line_kind) label,sum(amount) amount FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s AND line_kind<>'revenue' GROUP BY 1,2,3",(start,end))
            for r in cur.fetchall():add(r['dt'],r['label'],'promotion' if r['line_kind']=='advertising' else 'marketplace',r['amount'])
            cur.execute("SELECT report_date dt,sum(ordered_units) units,sum(ordered_amount_rub) amount FROM ozon_funnel_daily WHERE report_date BETWEEN %s AND %s GROUP BY 1",(start,end))
            orders=cur.fetchall()
            cur.execute("SELECT max(operation_date)::text latest FROM ozon_finance_lines");source_bounds['Начисления Ozon']=cur.fetchone()['latest']
        else:
            cur.execute("SELECT event_date dt,coalesce(campaign_id,'__unallocated__') cabinet,offer_id sku,offer_id article,CASE WHEN event_type='returned' THEN -1 ELSE 1 END sign,sum(units) units,CASE WHEN count(*)=count(amount) THEN sum(CASE WHEN event_type='returned' THEN -amount ELSE amount END) END amount FROM yandex_fact_realization WHERE client_key=%s AND event_date BETWEEN %s AND %s GROUP BY 1,2,3,4,5",(client,start,end));events=cur.fetchall()
            cur.execute("SELECT coalesce(service_date,act_date) dt,service_type,coalesce(nullif(service_name,''),service_type) label,CASE WHEN count(*)=count(service_amount) THEN -sum(service_amount) END amount FROM yandex_fact_services WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN %s AND %s GROUP BY 1,2,3",(client,start,end))
            from unit_yandex_services import classify
            service_names={'payment_accepting':'Приём платежа','payment_transfer':'Перевод платежа','paid_storage_after_01-06-22':'Платное хранение','business_subscription':'Бизнес-подписка','cpm-boost':'Буст показов','boost':'Буст продаж','product-banners':'Баннеры товаров','shelf':'Полка товаров'}
            for r in cur.fetchall():add(r['dt'],service_names.get(r['label'],r['label']),'promotion' if classify(r['service_type'],r['label'])[0]=='advertising_cost' else 'marketplace',r['amount'])
            cur.execute("""WITH raw AS (
             SELECT business_id,payload->>'orderId' oid,payload->>'yourSku' sku,ya_date(payload->>'compensationDate') dt,ya_number(payload->>'compensationAmount') amount,'Компенсации утрат' kind FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items'
             UNION ALL SELECT business_id,payload->>'orderId',payload->>'yourSku',ya_date(payload->>'decompensationDate'),-ya_number(payload->>'decompensationAmount'),'Сторно компенсаций' FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items'),
             events AS (SELECT business_id,oid,sku,dt,kind,CASE WHEN count(DISTINCT amount)=1 THEN min(amount) END amount FROM raw WHERE dt BETWEEN %s AND %s GROUP BY 1,2,3,4,5)
             SELECT dt,kind,CASE WHEN count(*)=count(amount) THEN sum(amount) END amount FROM events GROUP BY 1,2""",(client,client,start,end))
            for r in cur.fetchall():add(r['dt'],r['kind'],'marketplace',r['amount'])
            cur.execute("SELECT order_date dt,sum(ordered_units) units,CASE WHEN sum(lines_with_payment)=sum(item_lines) THEN sum(buyer_payment+coalesce(subsidy,0)) END amount FROM yandex_sku_orders_daily WHERE client_key=%s AND currency='RUR' AND order_date BETWEEN %s AND %s GROUP BY 1",(client,start,end));orders=cur.fetchall()
            for title,table,dt in [('Реализации ЯМ','yandex_fact_realization','event_date'),('Услуги ЯМ','yandex_fact_services','coalesce(service_date,act_date)')]:
                cur.execute(f'SELECT max({dt})::text latest FROM {table} WHERE client_key=%s',(client,));source_bounds[title]=cur.fetchone()['latest']
        for e in events:
            r=mark(e['dt']);qty=D(e['units']);returned=e['sign']<0;k='returns' if returned else 'buyouts';r[k]+=qty;r['return_amount' if returned else 'buyout_amount']+=abs(D(e['amount']));r['revenue']+=D(e['amount'])
            if e['amount'] is None:unknown.add((str(e['dt']),'revenue'))
            price,source=resolve(market,e['sku'],e['article']);sc=sku_costs[(str(e.get('cabinet') or ''),str(e['sku']))]
            if price is None:r['missing_cost_units']+=float(qty);sc[1]+=float(qty)
            else:r['known_cogs']+=price*qty*e['sign'];r['sku_cost_units']+=float(qty);sc[0]+=price*qty*e['sign']
            trace.append(dict(date=str(e['dt']),sku=e['sku'],cabinet=e.get('cabinet'),article=e['article'],barcode='',units=float(qty*e['sign']),unit_cost=float(price) if price is not None else None,cost=float(price*qty*e['sign']) if price is not None else None,source=source))
        for r in orders:daily[str(r['dt'])].update(orders=r['units'],order_amount=r['amount'])
        expenses=read_expenses(cur,market,start,end)
    payload['all_expenses']=expenses;payload['expenses']=[r for r in expenses if r['date_from']==str(start) and r['date_to']==str(end)];payload['model_read_only']=False
    for e in expenses:
        group='promotion' if expense_group(e['expense_name'],e.get('notes'))=='promotion' else 'operating'
        for dt in daily:
            amount=allocate_expense(e['source_amount'],date.fromisoformat(e['date_from']),date.fromisoformat(e['date_to']),date.fromisoformat(dt),date.fromisoformat(dt))
            if amount:add(dt,e['expense_name'],group,-amount)
    from pl_monthly_budgets import read_range,apply_budgets
    budgets=apply_budgets(daily,arts,read_range(config,client,start,end),market)
    for group,label in [('marketplace','Начисления площадки'),('promotion','Начисления за продвижение'),('operating','Внесённые операционные расходы')]:
        if not any(a['group']==group for a in arts.values()):arts[group+'_registered']=dict(key=group+'_registered',label=label,group=group,unit='money',kind='article',help='Записи за период отсутствуют; полнота внешних расходов не подтверждена.')
    for r in daily.values():
        for a in arts.values():
            if r['finance_present'] or a['group'] in ('fixed','operating'):r.setdefault(a['key'],Decimal(0))
    days=finalize(list(daily.values()),list(arts.values()),tax)
    for dt,key in unknown:
        r=daily[dt];r[key]=None
        for k in ('gross_margin','ebitda','net_profit','taxes','income_tax'):r[k]=None
        if key in arts:r[arts[key]['group']]=None
    model=pack(days,list(arts.values()),tax,monthly_budgets=budgets,registry_barcodes=registry_count,cost_events=trace,marketplace=market,label={'ozon':'Ozon','yandex':'Яндекс Маркет'}[market],source_note=' · '.join(k+': '+str(v) for k,v in source_bounds.items()),unknown_amount_rows=len(unknown))
    if unknown:
        for period in [model['total']]+[p for ps in model['periods'].values() for p in ps]:
            if any(period['from_date']<=dt<=period['to_date'] for dt,_ in unknown):
                for k in ('gross_margin','ebitda','net_profit','taxes','income_tax'):period['values'][k]=None
    for p in [model['total']]+[p for ps in model['periods'].values() for p in ps]: update_financial_ratios(p['values'])
    v=model['total']['values'];model['reconciliation']=dict(source_services=payload['totals'].get('ozon_costs'),marketplace_and_promotion=-(D(v['marketplace'])+D(v['promotion'])))
    model['reconciliation']['marketplace_and_promotion']=float(model['reconciliation']['marketplace_and_promotion'])
    payload['financial_model']=model
    for p in payload.get('products',[]):
        sc=sku_costs[(str(p.get('cabinet') or ''),str(p['sku']))];p['cogs']=float(sc[0]) if not sc[1] else None
        p['management_result']=float(D(p.get('revenue'))-D(p.get('marketplace_costs_before_cogs',p.get('ozon_costs')))-sc[0]) if not sc[1] else None
        p['margin_pct']=p['management_result']/p['revenue']*100 if p['management_result'] is not None and p.get('revenue') else None
    return sync_summary(payload)


def model_workbook(data):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from openpyxl.utils import get_column_letter
    model=data['financial_model'];wb=Workbook();wb.remove(wb.active)
    for grain,title in [('week','Финмодель по неделям'),('day','Финмодель по дням'),('month','Финмодель по месяцам')]:
        sheet=wb.create_sheet(title);periods=model['periods'][grain]
        sheet.append(['Статья','Итого']+[p['from_date']+' — '+p['to_date'] for p in periods])
        for row in model['rows']:
            sheet.append([row['label'],model['total']['values'].get(row['key'])]+[p['values'].get(row['key']) for p in periods])
            if row['kind'] in ('group','total'):
                for cell in sheet[sheet.max_row]:cell.font=Font(bold=True);cell.fill=PatternFill('solid',fgColor='EDF2F5')
        sheet.append(['Без себестоимости, шт',model['total']['missing_cost_units']]);sheet.append(['По единой цене SKU / артикула, шт',model['total']['sku_cost_units']]);sheet.append([model.get('source_note','')]);sheet.append(['Текущий справочник, сохранённые налоги юнитки; наблюдаемые суммы не подтверждают полноту источника.'])
        sheet.freeze_panes='C2';sheet.column_dimensions['A'].width=64
        for i in range(2,sheet.max_column+1):sheet.column_dimensions[get_column_letter(i)].width=25
    trace=wb.create_sheet('Себестоимость расчёта');trace.append(['Площадка','Дата','SKU','Артикул','Нетто, шт','Цена, ₽','Себестоимость, ₽','Источник'])
    for row in model.get('cost_events',[]):trace.append([row.get('marketplace',data['marketplace'])]+[row.get(k) for k in ('date','sku','article','units','unit_cost','cost','source')])
    trace.freeze_panes='A2'
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
                elif isinstance(cell.value,(int,float)):cell.number_format='#,##0.00;[Red]-#,##0.00'
    out=BytesIO();wb.save(out);return out.getvalue()


def sync_summary(payload):
    model=payload.get('financial_model')
    if not model:return payload
    v=model['total']['values'];t=payload['totals'];result=v['net_profit'] if v['net_profit'] is not None else v['ebitda']
    t.update(cogs=-v['cogs'] if v['cogs'] is not None else None,gross_profit=v['gross_margin'],management_result=result,net_profit=v['net_profit'],tax_configured=model['tax_ready'])
    values={'cogs':v['cogs'],'gross_profit':v['gross_margin'],'management_result':result,'net_profit':v['net_profit']}
    for key,field in [('vat','vat'),('vat_model','vat'),('tax','income_tax')]:
        amount=v.get(field)
        if field not in v:
            items=[value for k,value in v.items() if k.endswith('__'+field)]
            amount=sum(items) if items and all(x is not None for x in items) else None
        values[key]=amount
    for row in payload.get('statement',[]):
        if row['key'] in values:row['amount']=values[row['key']]
        if row['key']=='management_result':row['label']='Результат модели' if model['tax_ready'] else 'EBITDA'
    from pl_monthly_budgets import sync_statement
    return sync_statement(payload)
