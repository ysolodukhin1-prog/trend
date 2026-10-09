"""Read-only, client-local product matrix. Missing data never becomes zero."""
import math, hashlib, threading, time
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs
import psycopg2
from psycopg2.extras import RealDictCursor

CHANNELS={'wb':'WB','ozon':'Ozon','yandex_market':'Яндекс Маркет','lamoda':'Lamoda','online':'Интернет-магазин','retail':'Розница','corners':'Корнеры','wholesale':'Опт','networks':'Сети'}
SCOPES={'all':list(CHANNELS),'ecom':['wb','ozon','yandex_market','lamoda','online'],'marketplaces':['wb','ozon','yandex_market','lamoda'],'online':['online'],'offline':['retail','corners','wholesale','networks'],'retail':['retail'],'corners':['corners'],'wholesale':['wholesale'],'networks':['networks']}
REPORTS={'home','planfact','orderFeed','assortmentProducts','assortmentABC','assortmentXYZ','profitLoss','unitEconomics','inventoryHistory'}
LIMITATIONS=['Итоги — только подтверждённые значения. Прочерк означает отсутствие данных, а не нулевые продажи или остатки.','WB/Ozon/ЯМ — реализация минус возвраты по дате финансовой операции; офлайн — учтённая выручка источника. Базы НДС каналов могут отличаться.','Связь вариантов — по существующему справочнику или уникальному валидному GTIN. Артикул и название не объединяют каналы.','Lamoda: продажи по SKU не подтверждены. Интернет-магазин, опт и часть офлайна имеют устаревшее покрытие. Проверяйте даты источников.','WB: текущий остаток без разбивки по складам; не распределяется на размеры. Lamoda FBO/FBS — остаток каталога по схеме. Центральный склад и магазины пока без подключённых остатков.','ABC — предварительный, по положительной учтённой выручке за период. XYZ не рассчитан: нет подтверждённого полного ряда продаж всех каналов.','ЮНИТ/PL — по учтённым финансовым операциям и подтверждённой себестоимости, до налогов и внешних расходов. Полный общий PL и прибыль пока не подтверждены.']
_cache={};_matrix={};_lock=threading.RLock()

def total(values):
    values=[Decimal(str(v)) for v in values if v is not None]
    return sum(values) if values else None

def gtin(value):
    value=str(value or '').strip()
    if len(value) not in (8,12,13,14) or not value.isascii() or not value.isdigit() or not value.strip('0'):return None
    if (sum(int(x)*(3 if i%2==0 else 1) for i,x in enumerate(value[-2::-1]))+int(value[-1]))%10:return None
    return value.zfill(14)

def access_flags(app,identity,client,report):
    from data_access import permits,report_permitted
    from one_c_import import access
    admin=bool(identity.get('is_admin'))
    required='assortmentProducts' if report=='home' else 'funnel' if report=='orderFeed' else report
    if not admin and (client not in identity.get('clients',[]) or required not in identity.get('reports',[]) or not report_permitted(identity,client,required)):
        raise ValueError('Нет доступа к отчёту')
    catalog=client=='toptop' and (admin or access(app,identity,'catalog'))
    retail=client=='toptop' and (admin or access(app,identity,'sales'))
    files=client=='toptop' and (admin or ('funnel' in identity.get('reports',[]) and permits(identity.get('data_access'),'marketplace:toptop','funnel')))
    ut=files and (admin or all(permits(identity.get('data_access'),'1c',t) for t in ('dbo._AccumRg10016','dbo._Document236','dbo._Reference88','dbo._Reference104X1','dbo._Reference92','dbo._Reference157')))
    sales=admin or ('profitLoss' in identity.get('reports',[]) and report_permitted(identity,client,'profitLoss'))
    prices=client=='toptop' and (admin or access(app,identity,'prices'))
    pricing=admin or ('assortmentPrices' in identity.get('reports',[]) and report_permitted(identity,client,'assortmentPrices'))
    return catalog,retail,files,ut,sales,prices,pricing

def read_data(app,client,start,end,flags):
    catalog,retail,files,ut,finance,retail_prices,pricing=flags
    rows=[];sales={};stocks={};dims={};coverage={};warehouses={};warnings=[]
    with psycopg2.connect(**app.read_db_config(client),cursor_factory=RealDictCursor) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as c:
            c.execute("SET LOCAL statement_timeout='15s'")
            allowed=['wb','ozon','yandex_market','lamoda']+(['retail'] if catalog else [])
            c.execute("SELECT master_id::text,jsonb_agg(jsonb_build_object('channel',channel,'key',source_key,'status',status,'data',payload) ORDER BY channel) links FROM assortment_master.links WHERE channel=ANY(%s) GROUP BY master_id",(allowed,))
            masters=[dict(r) for r in c.fetchall()]
            # Conflicting cross-channel masters stay separate until reviewed. Exact source identities
            # still retain their own sales; excluding them would silently lose most business revenue.
            for row in masters:
                if any(l['status']=='conflict' for l in row['links']):
                    rows.extend({'master_id':row['master_id']+':'+l['channel']+':'+l['key'],'links':[l]} for l in row['links'])
                else:rows.append(row)
            index={};codes=defaultdict(set);wb_parents=defaultdict(set)
            for row in rows:
                row['status']='conflict' if any(l['status']=='conflict' for l in row['links']) else 'matched' if len(row['links'])>1 else 'unmatched'
                for l in row['links']:
                    index[l['channel'],l['key']]=row
                    if l['channel']=='wb':wb_parents[str(l['data']['parent'])].add(row['master_id'])
                    for code in l['data'].get('valid_barcodes',[]):codes[code].add(row['master_id'])
            by_id={r['master_id']:r for r in rows}
            # Finance operations supply actual sales and returns, never funnel orders.
            if finance:
                c.execute("""SELECT nm_id::text parent,sku barcode,sum(quantity*CASE WHEN lower(seller_oper_name) IN ('возврат','return') THEN -1 ELSE 1 END) units,
                  sum(retail_amount*CASE WHEN lower(seller_oper_name) IN ('возврат','return') THEN -1 ELSE 1 END) rub,max(operation_date) stamp
                  FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s AND lower(seller_oper_name) IN ('продажа','sale','возврат','return') GROUP BY 1,2""",(start,end))
                for x in c.fetchall():sales['wb',x['parent'],gtin(x['barcode']) or str(x['barcode'])]=dict(x)
                c.execute("SELECT sku,sum(amount) rub,CASE WHEN bool_or(quantity<>trunc(quantity)) THEN NULL ELSE sum(CASE WHEN amount<0 THEN -abs(quantity) ELSE quantity END) END units,max(operation_date) stamp FROM ozon_finance_lines WHERE line_kind='revenue' AND currency IN ('RUB','RUR') AND operation_date BETWEEN %s AND %s GROUP BY sku",(start,end))
                for x in c.fetchall():sales['ozon',str(x['sku'])]=dict(x)
                c.execute("SELECT offer_id sku,sum(CASE WHEN event_type='returned' THEN -amount ELSE amount END) rub,sum(CASE WHEN event_type='returned' THEN -units ELSE units END) units,max(event_date) stamp FROM yandex_fact_realization WHERE client_key=%s AND event_date BETWEEN %s AND %s GROUP BY offer_id",(client,start,end))
                for x in c.fetchall():sales['yandex_market',str(x['sku'])]=dict(x)
                for ch,table,column,predicate in [('wb','wb_finance_lines','operation_date',"lower(seller_oper_name) IN ('продажа','sale','возврат','return')"),('ozon','ozon_finance_lines','operation_date',"line_kind='revenue'"),('yandex_market','yandex_fact_realization','event_date','TRUE')]:
                    c.execute(f'SELECT min({column}) first,max({column}) last FROM {table} WHERE '+predicate)
                    coverage[ch]=dict(c.fetchone(),basis='Реализация и возвраты по дате финансовой операции')
                owners=defaultdict(set)
                for row in rows:
                    for l in row['links']:
                        if l['channel']=='wb':
                            for code in l['data'].get('barcodes',[]):owners['wb',str(l['data']['parent']),gtin(code) or str(code)].add(row['master_id'])
                        else:owners[l['channel'],l['key']].add(row['master_id'])
                for k,metric in list(sales.items()):
                    if len(owners[k])==1:
                        metric['owner_master_id']=next(iter(owners[k]));continue
                    ch,key=k[:2];code=k[2] if ch=='wb' else None
                    source_key=key+':finance:'+str(code or 'unknown') if ch=='wb' else key
                    row={'master_id':'finance:'+':'.join(str(v) for v in k),'status':'unmatched','links':[{'channel':ch,'key':source_key,'status':'unmatched','data':{'name':'Товар '+CHANNELS[ch]+' · '+key,'article':key,'parent':key,'barcodes':[code] if code else [],'valid_barcodes':[code] if code else []}}]}
                    row['links'][0]['data']['financial_key']=list(k)
                    rows.append(row);index[ch,source_key]=row;by_id[row['master_id']]=row
                    metric['owner_master_id']=row['master_id']
                    if ch=='wb':wb_parents[key].add(row['master_id'])
            else:warnings.append('Реализация маркетплейсов скрыта: нужны права на финансовые данные PL.')
            coverage['lamoda']={'first':None,'last':None,'basis':'Нет подтверждённого состава реализованных заказов по SKU'}
            # Exact retail IDs; files match only unique valid GTIN. Article/name are not cross-channel keys.
            offline=(["retail"] if retail else [])+(["online","wholesale"] if files else [])+(["corners","networks"] if ut else [])
            if offline:
                c.execute("""SELECT s.channel,CASE WHEN r.product_id IS NOT NULL THEN r.product_id||':'||r.variant_id END source_key,
                  s.article,s.variant,s.barcode,max(s.product) name,
                  sum(s.quantity) FILTER(WHERE s.sale_date BETWEEN %s AND %s) units,
                  sum(s.revenue) FILTER(WHERE s.sale_date BETWEEN %s AND %s) rub,
                  max(s.sale_date) FILTER(WHERE s.sale_date BETWEEN %s AND %s) stamp,max(s.sale_date) catalog_stamp,
                  string_agg(DISTINCT s.revenue_basis,', ') basis
                  FROM channel_sales.sales s LEFT JOIN retail_1c.sales r ON s.source_id='1c-retail:'||r.recorder_id AND s.source_row=r.line_no AND s.channel='retail'
                  WHERE s.channel=ANY(%s) GROUP BY 1,2,3,4,5""",(start,end,start,end,start,end,offline))
                for x in c.fetchall():
                    row=index.get(('retail',x['source_key'])) if x['source_key'] else None
                    code=gtin(x['barcode'])
                    if row is None and code and len(codes[code])==1:row=by_id[next(iter(codes[code]))]
                    if row is None:
                        key=x['source_key'] or hashlib.sha256(str((x['channel'],x['article'],x['variant'],x['barcode'])).encode()).hexdigest()[:24]
                        row=index.get((x['channel'],key))
                        if row is None:
                            row={'master_id':'source:'+x['channel']+':'+key,'status':'unmatched','links':[{'channel':x['channel'],'key':key,'status':'unmatched','data':{'name':x['name'],'article':x['article'],'variant':x['variant'],'barcodes':[x['barcode']] if x['barcode'] else [],'valid_barcodes':[code] if code else []}}]}
                            rows.append(row);index[x['channel'],key]=row
                    k=('offline',row['master_id'],x['channel'])
                    if k in sales:
                        old=sales[k];old['units']=total([old['units'],x['units']]);old['rub']=total([old['rub'],x['rub']]);old['stamp']=max((v for v in [old['stamp'],x['stamp']] if v),default=None);old['basis']+=', '+x['basis']
                    else:sales[k]=dict(x)
                c.execute('SELECT channel,min(sale_date) first,max(sale_date) last,string_agg(DISTINCT revenue_basis,\', \') basis FROM channel_sales.sales WHERE channel=ANY(%s) GROUP BY channel',(offline,))
                for x in c.fetchall():coverage[x['channel']]=dict(x)
            # Current imported warehouse snapshot. Schemes/unspecified warehouses are labelled explicitly.
            c.execute("SELECT sku::text sku,warehouse_name warehouse,sum(available_to_sell) units,max(imported_at)::date stamp FROM ozon_stock_product_warehouses GROUP BY 1,2")
            for x in c.fetchall():
                wh='ozon:'+str(x['warehouse'] or 'Без разбивки');warehouses[wh]={'label':'Ozon · '+str(x['warehouse'] or 'без разбивки'),'date':str(x['stamp'])};stocks['ozon',x['sku'],wh]=dict(x)
            c.execute("SELECT offer_id sku,warehouse,sum(available_for_order) units,max(snapshot_date) stamp FROM yandex_inventory_current WHERE client_key=%s GROUP BY 1,2",(client,))
            for x in c.fetchall():
                wh='yandex_market:'+str(x['warehouse']);warehouses[wh]={'label':'ЯМ · '+str(x['warehouse'] or 'без разбивки'),'date':str(x['stamp'])};stocks['yandex_market',x['sku'],wh]=dict(x)
            c.execute("SELECT wb_nmid::text sku,stock_qty units,snapshot_date stamp FROM wb_stock_api_current")
            for x in c.fetchall():
                wh='wb:unallocated';warehouses[wh]={'label':'WB · без разбивки по складам','date':str(x['stamp'])};stocks['wb',x['sku'],wh]=dict(x)
            c.execute("""WITH latest AS (SELECT DISTINCT ON(account_id,fulfillment,lamoda_sku) lamoda_sku sku,account_id,fulfillment,nullif(payload->>'quantity','')::numeric units,snapshot_date stamp FROM lamoda_v2_entities WHERE dataset='catalog' AND account_id IS NOT NULL AND coalesce(lamoda_sku,'')<>'' ORDER BY account_id,fulfillment,lamoda_sku,synced_at DESC,snapshot_date DESC,record_key)
              SELECT sku,fulfillment,sum(units) units,max(stamp) stamp FROM latest GROUP BY 1,2""")
            for x in c.fetchall():
                wh='lamoda:'+str(x['fulfillment']);warehouses[wh]={'label':'Lamoda · '+str(x['fulfillment'] or 'без схемы')+' (каталог)','date':str(x['stamp'])};stocks['lamoda',x['sku'],wh]=dict(x)
            c.execute("SELECT sku::text sku,dlina_upakovki_mm length_mm,shirina_upakovki_mm width_mm,vysota_upakovki_mm height_mm,ves_v_upakovke_g weight_g FROM ozon_cat_products WHERE sku IS NOT NULL")
            for x in c.fetchall():dims['ozon',x['sku']]=dict(x,source='Ozon · упаковка')
            c.execute("SELECT DISTINCT ON(payload->>'nmID') payload->>'nmID' sku,payload->'dimensions' dimensions,captured_at FROM wb_api_entities WHERE source_key='content.cards' ORDER BY payload->>'nmID',captured_at DESC")
            for x in c.fetchall():
                d=x['dimensions'] or {};values={}
                for field,key,factor in [('length_mm','length',10),('width_mm','width',10),('height_mm','height',10),('weight_g','weightBrutto',1000)]:
                    try:v=Decimal(str(d.get(key)));values[field]=v*factor if v.is_finite() and v>0 else None
                    except Exception:values[field]=None
                dims['wb',x['sku']]=dict(values,source='WB · упаковка',warning='Габариты требуют проверки WB' if d.get('isValid') is False else '')
            # Keep each channel's price type/date. No averaging incompatible prices.
            from assortment_api import attach_prices
            price_rows=[{'status':r['status'],'links':[l for l in r['links'] if l['channel'] in allowed and (l['channel']!='retail' or (retail_prices and l['key'].count(':')==1))] if pricing else []} for r in rows]
            if pricing:attach_prices(c,price_rows)
            for row,price_row in zip(rows,price_rows):
                prices={l['channel']:l.get('price') for l in price_row['links'] if l.get('price')}
                row['prices']=prices
    return rows,sales,stocks,dims,coverage,warehouses,wb_parents,warnings

def cached_data(app,client,start,end,flags):
    key=(client,str(start),str(end),flags)
    with _lock:
        hit=_cache.get(key)
        if hit and hit[0]>time.monotonic():return hit[1]
        value=read_data(app,client,start,end,flags)
        if len(_cache)>=4:_cache.pop(next(iter(_cache)))
        _cache[key]=(time.monotonic()+300,value)
        return value

def cached_matrix(raw,active):
    key=(id(raw),tuple(active))
    with _lock:
        if key in _matrix:return _matrix[key][1]
        value=materialize(raw,active)
        if len(_matrix)>=4:_matrix.pop(next(iter(_matrix)))
        _matrix[key]=(raw,value)
        return value

def materialize(raw,active):
    rows,sales,stocks,dims,coverage,warehouses,wb_parents,warnings=raw
    out=[]
    for source in rows:
        links=source['links'];metrics={};stock={};notes=[]
        for ch in active:
            matched=[l for l in links if l['channel']==ch]
            off=sales.get(('offline',source['master_id'],ch))
            if off:metrics[ch]=off
            elif len(matched)==1:
                l=matched[0];key=str(l['data'].get('parent') if ch=='wb' else l['key'])
                if ch=='wb':
                    codes=set(l['data'].get('valid_barcodes',[]))|{gtin(code) or str(code) for code in l['data'].get('barcodes',[])}
                    candidates=[sales.get((ch,key,code)) for code in codes]
                    if l['data'].get('financial_key'):candidates=[sales.get(tuple(l['data']['financial_key']))]
                    candidates=[v for v in candidates if v and v.get('owner_master_id')==source['master_id']]
                    if candidates:metrics[ch]={'units':total(v['units'] for v in candidates),'rub':total(v['rub'] for v in candidates),'stamp':max(v['stamp'] for v in candidates)}
                elif (ch,key) in sales and sales[ch,key].get('owner_master_id')==source['master_id']:metrics[ch]=sales[ch,key]
                for wh in warehouses:
                    if not wh.startswith(ch+':'):continue
                    if ch=='wb' and len(wb_parents[key])!=1:continue
                    if (ch,key,wh) in stocks:stock[wh]=stocks[ch,key,wh]
            elif matched:notes.append(CHANNELS[ch]+': конфликт связи')
        relevant=any(l['channel'] in active for l in links) or bool(metrics)
        if not relevant:continue
        d=next((dims.get((l['channel'],str(l['data'].get('parent') if l['channel']=='wb' else l['key']))) for ch in ['ozon','wb'] for l in links if l['channel']==ch and dims.get((ch,str(l['data'].get('parent') if ch=='wb' else l['key'])))),{})
        dimension=dict(d or {})
        if all(dimension.get(k) and Decimal(str(dimension[k]))>0 for k in ['length_mm','width_mm','height_mm']):dimension['litres']=Decimal(str(dimension['length_mm']))*Decimal(str(dimension['width_mm']))*Decimal(str(dimension['height_mm']))/1000000
        sales_total=total(m['rub'] for m in metrics.values());units=total(m['units'] for m in metrics.values());stock_total=total(m['units'] for m in stock.values())
        out.append(dict(source,metrics=metrics,stocks=stock,dimensions=dimension,sales_total=sales_total,units_total=units,stock_total=stock_total,notes=notes,
          partial=bool(notes) or any(ch not in metrics for ch in active),stock_partial=any(not any(wh.startswith(ch+':') for wh in stock) for ch in active)))
    out.sort(key=lambda r:(r['sales_total'] is None,-(r['sales_total'] or 0),r['master_id']))
    return out

def all_brands_directory(app, parsed, identity, start, end, get):
    """One sorted directory across permitted accounts, preserving source identities."""
    labels={'toptop':'TOPTOP','lera_nena':'LERA NENA'}
    active=SCOPES['all']; rows=[]; warehouses={}; coverage={}; coverage_labels={}; warnings=[]; brands=[]
    for client,label in labels.items():
        try:flags=access_flags(app,identity,client,'assortmentProducts')
        except ValueError:continue
        raw=cached_data(app,client,start,end,flags)
        brands.append(client)
        for row in cached_matrix(raw,active):
            rows.append(dict(row,master_id=client+':'+row['master_id'],source_master_id=row['master_id'],source_client=client,
                             stocks={client+':'+key:value for key,value in row['stocks'].items()}))
        for key,info in raw[5].items():
            if key.split(':')[0] in active:warehouses[client+':'+key]=dict(info,label=label+' · '+info['label'])
        for channel in active:
            if channel in raw[4]:
                key=client+':'+channel;coverage[key]=raw[4][channel];coverage_labels[key]=label+' · '+CHANNELS[channel]
        warnings.extend(label+': '+warning for warning in raw[7])
    if not brands:raise ValueError('Нет доступа к справочнику товаров')
    rows.sort(key=lambda row:(row['sales_total'] is None,-(row['sales_total'] or 0),row['master_id']))
    search=get('q').strip().casefold()[:120];status=get('status')
    selected=[row for row in rows if (not status or row['status']==status) and (not search or any(search in str(value or '').casefold() for link in row['links'] for value in [link['key'],link['data'].get('name'),link['data'].get('article'),link['data'].get('variant'),*link['data'].get('barcodes',[])]))]
    count=len(selected);pages=max(1,math.ceil(count/50));page=min(max(1,int(get('page','1'))),pages)
    summary={'revenue':total(row['sales_total'] for row in rows),'units':total(row['units_total'] for row in rows),'stock':total(row['stock_total'] for row in rows),'products':len(rows),'partial':True}
    return {'ok':True,'client':app.current_client_key(),'brands':brands,'all_brands':True,'report':'assortmentProducts','scope':'all',
            'channels':{key:CHANNELS[key] for key in active},'rows':selected[(page-1)*50:page*50],'total':count,'page':page,'pages':pages,
            'counts':dict(Counter(row['status'] for row in rows)),'summary':summary,'financial':None,
            'warehouses':warehouses,'coverage':coverage,'coverage_labels':coverage_labels,'period':{'from':str(start),'to':str(end)},
            'limitations':warnings+LIMITATIONS}


def build(app,parsed,identity):
    q=parse_qs(parsed.query);get=lambda k,d='':q.get(k,[d])[0]
    client=app.current_client_key();report=get('dashboard','assortmentProducts');scope=get('scope','all')
    if report not in REPORTS or scope not in SCOPES or client not in ('toptop','lera_nena'):raise ValueError('Неизвестный отчёт')
    flags=access_flags(app,identity,client,report)
    end=date.fromisoformat(get('to')) if get('to') else datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)
    start=date.fromisoformat(get('from')) if get('from') else end-timedelta(days=28)
    if start>end or (end-start).days>366:raise ValueError('Выберите период не длиннее года')
    if report=='assortmentProducts' and scope=='all' and get('brands')=='all':
        return all_brands_directory(app,parsed,identity,start,end,get)
    if report=='orderFeed':return order_feed(app,client,start,end,SCOPES[scope],flags,get)
    page=max(1,int(get('page','1')));search=get('q').strip().casefold()[:120];status=get('status');active=SCOPES[scope]
    raw=cached_data(app,client,start,end,flags);allrows=cached_matrix(raw,active)
    # ABC is based on observed positive net revenue; coverage limitations remain explicit.
    positive=sum((r['sales_total'] or 0) for r in allrows if (r['sales_total'] or 0)>0);cumulative=Decimal(0)
    for r in allrows:
        v=r['sales_total'];r['abc']=None
        if v is not None and v>0 and positive:
            r['abc']='A' if cumulative/positive<Decimal('.8') else 'B' if cumulative/positive<Decimal('.95') else 'C';cumulative+=v
        elif v is not None:r['abc']='C'
        r['xyz']=None
    counts=Counter(r['status'] for r in allrows)
    selected=[r for r in allrows if (not status or r['status']==status) and (not search or any(search in str(v or '').casefold() for l in r['links'] for v in [l['key'],l['data'].get('name'),l['data'].get('article'),l['data'].get('variant'),*l['data'].get('barcodes',[])]))]
    total_rows=len(selected);page=min(page,max(1,math.ceil(total_rows/50)));visible=[dict(r) for r in selected[(page-1)*50:page*50]]
    if report=='unitEconomics':
        from assortment_margins import attach_margins
        for row in visible:row['totals']={}
        if flags[4]:
            attach_margins(app,visible,(end-start).days+1,client,'actual',period_start=start,period_end=end)
            for row in visible:
                known=[v for ch,v in row['margins']['channels'].items() if ch in active and v.get('rub') is not None]
                revenue=total(v.get('revenue') for v in known);rub=total(v.get('rub') for v in known)
                units=total(v.get('units') for v in known)
                row['margins']['total']={'rub':rub,'unit_profit':rub/units if rub is not None and units and units>0 else None,'pct':rub/revenue*100 if rub is not None and revenue and revenue>0 else None,'partial':True,'detail':'По подтверждённой себестоимости выбранных каналов; полный результат не подтверждён'}
    summary={'revenue':total(r['sales_total'] for r in allrows),'units':total(r['units_total'] for r in allrows),'stock':total(r['stock_total'] for r in allrows),'products':len(allrows),'partial':True}
    financial=None
    if report in {'profitLoss','unitEconomics'} and flags[4]:
        from assortment_margins import facts
        ws=facts(app.read_db_config(client),client,start,end);products=ws.get('allocated_products',[])
        by_channel={}
        for ch in active:
            group=[p for p in products if ('yandex_market' if p.get('marketplace')=='yandex' else p.get('marketplace'))==ch]
            if group:
                known=[p for p in group if p.get('contribution_actual') is not None]
                by_channel[ch]={'revenue':total(p.get('revenue') for p in group),'net':total(p.get('net') for p in group),'contribution':total(p.get('contribution_actual') for p in known),'partial':len(known)!=len(group),'products':len(group),'known_cost_products':len(known)}
            else:by_channel[ch]={'revenue':total(r['metrics'].get(ch,{}).get('rub') for r in allrows),'net':None,'contribution':None,'partial':True,'products':0,'known_cost_products':0}
        financial={'channels':by_channel,'revenue':total(v['revenue'] for v in by_channel.values()),'contribution':total(v['contribution'] for v in by_channel.values()),'partial':True}
    warehouses={k:v for k,v in raw[5].items() if k.split(':')[0] in active}
    coverage={k:raw[4].get(k,{'first':None,'last':None,'basis':'Источник недоступен или не подключён'}) for k in active}
    return {'ok':True,'client':client,'report':report,'scope':scope,'channels':{k:CHANNELS[k] for k in active},'rows':visible,'total':total_rows,'page':page,'pages':max(1,math.ceil(total_rows/50)),'counts':dict(counts),'summary':summary,'financial':financial,'warehouses':warehouses,'coverage':coverage,'period':{'from':str(start),'to':str(end)},
      'limitations':raw[7]+LIMITATIONS}

def order_feed(app,client,start,end,active,flags,get):
    """Merge only order-level facts; daily aggregates/monthly sales are not orders."""
    page=max(1,int(get('page','1')));search=get('q').strip()[:120];parts=[];args=[];coverage={}
    with psycopg2.connect(**app.read_db_config(client),cursor_factory=RealDictCursor) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as c:
            c.execute("SET LOCAL statement_timeout='10s'")
            if 'wb' in active:
                parts.append("""SELECT 'wb'::text channel,entity_key::text key,coalesce(nullif(payload->>'srid',''),entity_key)::text order_id,
                  CASE WHEN payload->>'date' ~ '^\\d{4}-\\d{2}-\\d{2}T' THEN (payload->>'date')::timestamp AT TIME ZONE 'Europe/Moscow' ELSE record_date::timestamp AT TIME ZONE 'Europe/Moscow' END ordered_at,
                  payload->>'supplierArticle' article,payload->>'subject' product,1::numeric units,
                  CASE WHEN payload->>'finishedPrice' ~ '^\\d+(\\.\\d+)?$' THEN (payload->>'finishedPrice')::numeric END amount,
                  CASE WHEN lower(payload->>'isCancel')='true' THEN 'Отменён' ELSE 'Заказ' END status,true time_known
                  FROM wb_api_entities WHERE source_key='statistics.orders' AND record_date BETWEEN %s AND %s""")
                args.extend([start,end]);coverage['wb']='Позиции заказов WB; московское время источника'
            if 'yandex_market' in active:
                parts.append("""SELECT 'yandex_market',campaign_id||':'||order_id::text||':'||item_id::text,order_id::text,
                  order_date::timestamp AT TIME ZONE 'Europe/Moscow',offer_id,offer_name,units,
                  CASE WHEN currency IN ('RUB','RUR') THEN buyer_payment+coalesce(subsidy,0) END,status,false
                  FROM yandex_fact_order_items WHERE client_key=%s AND NOT is_test AND order_date BETWEEN %s AND %s""")
                args.extend([client,start,end]);coverage['yandex_market']='Позиции заказов ЯМ; источник содержит только дату, время не известно'
            if 'lamoda' in active:
                parts.append(r"""SELECT 'lamoda',account_id::text||':'||fulfillment||':'||record_key,coalesce(payload->>'orderId',payload->>'id',record_key),
                  CASE WHEN payload->>'createdAt' ~ '^\d{4}-\d{2}-\d{2}T' THEN (payload->>'createdAt')::timestamptz END, NULL::text,'Запись заказа Lamoda · '||fulfillment,NULL::numeric,
                  CASE WHEN currency IN ('RUB','RUR') THEN amount/100 END,status,true
                  FROM lamoda_v2_entities WHERE dataset='orders' AND account_id IS NOT NULL AND (payload->>'createdAt')::timestamptz AT TIME ZONE 'Europe/Moscow' >= %s::date AND (payload->>'createdAt')::timestamptz AT TIME ZONE 'Europe/Moscow' < %s::date+1""")
                args.extend([start,end]);coverage['lamoda']='Заголовки заказов Lamoda FBO/FBS; товарный состав не загружен'
            if 'retail' in active and flags[1]:
                parts.append("""SELECT 'retail',recorder_id,recorder_id, min(period) AT TIME ZONE 'Europe/Moscow',NULL::text,
                  'Чек · '||max(store_name),sum(quantity),sum(revenue),'Проведённый чек',true
                  FROM retail_1c.sales WHERE channel='retail' AND period::date BETWEEN %s AND %s GROUP BY recorder_id""")
                args.extend([start,end]);coverage['retail']='Проведённые чеки розницы; одна строка на чек'
            for ch in active:
                coverage.setdefault(ch,'Нет загруженной ленты заказов. Агрегаты продаж не подставляются вместо заказов.')
            if not parts:return {'ok':True,'rows':[],'total':0,'page':1,'pages':1,'coverage':coverage,'channels':{k:CHANNELS[k] for k in active},'period':{'from':str(start),'to':str(end)},'order_feed':True}
            base='WITH feed AS ('+' UNION ALL '.join(parts)+') '
            where="WHERE concat_ws(' ',order_id,article,product) ILIKE %s" if search else ''
            if search:args.append('%'+search+'%')
            c.execute(base+'SELECT count(*) n FROM feed '+where,args);n=c.fetchone()['n'];page=min(page,max(1,math.ceil(n/50)))
            c.execute(base+'SELECT * FROM feed '+where+' ORDER BY ordered_at DESC NULLS LAST,channel,key LIMIT 50 OFFSET %s',args+[(page-1)*50]);rows=[dict(x) for x in c.fetchall()]
    return {'ok':True,'order_feed':True,'rows':rows,'total':n,'page':page,'pages':max(1,math.ceil(n/50)),'coverage':coverage,'channels':{k:CHANNELS[k] for k in active},'period':{'from':str(start),'to':str(end)}}
