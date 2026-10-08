"""Read-only signed expense ledger and daily aggregates for the P&L workbench."""
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import hashlib

from km_trade_finance import connect_km
from pl_workbench import EXPRESSIONS, ARTICLE_LABELS, SIGN
from pl_financial_model import expense_group, short_article
from pl_monthly_budgets import read_range, allocated_days
from pulse_financial_model import read_expenses, allocate_expense

MARKETS={'wb':'WB','ozon':'Ozon','yandex':'Яндекс Маркет'}
YANDEX_NAMES={'payment_accepting':'Приём платежа','payment_transfer':'Перевод платежа','paid_storage_after_01-06-22':'Платное хранение','business_subscription':'Бизнес-подписка','cpm-boost':'Буст показов','boost':'Буст продаж','product-banners':'Баннеры товаров','shelf':'Полка товаров'}
GROUPS={'marketplace':'Площадка','promotion':'Продвижение','fixed':'Постоянные расходы','operating':'Операционные расходы'}
D=lambda x:Decimal(str(x or 0))


def article_key(market, group, label):
    return market+'_'+group+'_'+hashlib.sha1(label.strip().casefold().encode()).hexdigest()[:16]


def bounds(start,end):
    start,end=map(date.fromisoformat,(start,end))
    if end<start or (end-start).days>366:raise ValueError('Выберите период до 367 дней')
    return start,end


def ledger(config,client,market,start,end):
    start,end=bounds(start,end)
    if market not in {*MARKETS,'all'}:raise ValueError('Неизвестная площадка')
    # The HTTP handler supplies the authorized client database; no arbitrary DB input.
    operations=[];articles={};daily={};status=[]
    for i in range((end-start).days+1):
        dt=str(start+timedelta(days=i));daily[dt]=dict(date=dt,revenue=None,buyouts=None,markets=[],unknown_revenue=False)
    def add(m,dt,label,group,impact,identity,**extra):
        if str(dt) not in daily:return
        key=article_key(m,group,label)
        source_label=label
        if m=='ozon' and extra.get('source_kind')=='Начисление':
            from ozon_article_labels import article_label
            label=article_label(label)
        articles.setdefault(key,dict(key=key,label=label,group=group,marketplace=m,source_code=source_label))
        operations.append(dict(id=m+':'+str(identity),date=str(dt),key=key,marketplace=m,group=group,
            label=label,impact=None if impact is None else D(impact),**extra))
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        for m in (MARKETS if market=='all' else [market]):
            table={'wb':'wb_finance_lines','ozon':'ozon_finance_lines','yandex':'yandex_fact_services'}[m]
            cur.execute('SELECT to_regclass(%s) relation',('public.'+table,))
            exists=bool(cur.fetchone()['relation']);status.append(dict(marketplace=m,available=exists))
            if exists and m=='wb':
                vals=','.join("('%s',(%s))"%(k,v) for k,v in EXPRESSIONS.items() if k!='revenue')
                cur.execute(f'''SELECT f.operation_date::text date,rrd_id::text id,report_id::text source_ref,
                    nm_id::text sku, f.sku barcode,vendor_code article,v.key,v.impact,
                    coalesce(nullif(raw_payload->>'bonusTypeName',''),nullif(raw_payload->>'bonus_type_name',''),seller_oper_name) label
                    FROM wb_finance_lines f CROSS JOIN LATERAL (VALUES {vals}) v(key,impact)
                    WHERE operation_date BETWEEN %s AND %s AND (v.impact<>0 OR v.impact IS NULL)''',(start,end))
                for r in cur.fetchall():
                    label=short_article(r['label']) or ARTICLE_LABELS[r['key']] if r['key'] in ('deduction','penalty','rebill_logistics') else ARTICLE_LABELS[r['key']]
                    group=expense_group(label) if r['key'] in ('deduction','penalty','rebill_logistics') else 'marketplace'
                    add(m,r['date'],label,group,r['impact'],r['id']+':'+r['key'],sku=r['sku'],barcode=r['barcode'],article=r['article'],source_ref='Отчёт WB '+str(r['source_ref']),source_kind='Начисление')
                cur.execute(f'''SELECT operation_date::text date,sum({EXPRESSIONS['revenue']}) revenue,
                    sum(CASE WHEN retail_amount<>0 OR for_pay<>0 THEN CASE WHEN ({SIGN})>0 THEN quantity ELSE 0 END ELSE 0 END) buyouts
                    FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s GROUP BY 1''',(start,end))
                source_days=cur.fetchall()
            elif exists and m=='ozon':
                cur.execute("SELECT line_key,operation_date::text date,posting_number,sku,article,line_kind,coalesce(nullif(type_name,''),line_kind) label,amount FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s AND line_kind<>'revenue'",(start,end))
                for r in cur.fetchall():add(m,r['date'],r['label'],'promotion' if r['line_kind']=='advertising' else 'marketplace',r['amount'],r['line_key'],sku=r['sku'],article=r['article'],source_ref=r['posting_number'] or r['line_key'],source_kind='Начисление')
                cur.execute("SELECT operation_date::text date,sum(CASE WHEN line_kind='revenue' THEN amount ELSE 0 END) revenue,sum(CASE WHEN line_kind='revenue' AND amount>=0 THEN abs(quantity) ELSE 0 END) buyouts FROM ozon_finance_lines WHERE operation_date BETWEEN %s AND %s GROUP BY 1",(start,end));source_days=cur.fetchall()
            elif exists and m=='yandex':
                from unit_yandex_services import classify
                cur.execute('''SELECT coalesce(service_date,act_date)::text date,service_type,coalesce(nullif(service_name,''),service_type) label,
                    -service_amount impact,offer_id sku,order_id,job_key,sheet,row_no FROM yandex_fact_services
                    WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN %s AND %s''',(client,start,end))
                for r in cur.fetchall():add(m,r['date'],YANDEX_NAMES.get(r['label'],r['label']),'promotion' if classify(r['service_type'],r['label'])[0]=='advertising_cost' else 'marketplace',r['impact'],str(r['job_key'])+':'+str(r['sheet'])+':'+str(r['row_no']),sku=r['sku'],source_ref=r['order_id'] or str(r['job_key']),source_kind='Начисление')
                # Same de-duplication and sign contract as the existing Yandex financial model.
                cur.execute("""WITH raw AS (
                 SELECT business_id,payload->>'orderId' oid,payload->>'yourSku' sku,ya_date(payload->>'compensationDate') dt,ya_number(payload->>'compensationAmount') amount,'Компенсации утрат' kind FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items'
                 UNION ALL SELECT business_id,payload->>'orderId',payload->>'yourSku',ya_date(payload->>'decompensationDate'),-ya_number(payload->>'decompensationAmount'),'Сторно компенсаций' FROM yandex_report_rows WHERE client_key=%s AND source_key='realization' AND sheet='lost_items')
                 SELECT business_id,oid,sku,dt,kind,CASE WHEN count(DISTINCT amount)=1 THEN min(amount) END amount FROM raw WHERE dt BETWEEN %s AND %s GROUP BY 1,2,3,4,5""",(client,client,start,end))
                for r in cur.fetchall():add(m,r['dt'],r['kind'],'marketplace',r['amount'],':'.join(str(r[k]) for k in ('business_id','oid','sku','dt','kind')),sku=r['sku'],source_ref=r['oid'],source_kind='Компенсация')
                cur.execute('''WITH events AS (
                    SELECT event_date date,CASE WHEN event_type='returned' THEN -amount ELSE amount END revenue,CASE WHEN event_type='returned' THEN 0 ELSE units END buyouts
                    FROM yandex_fact_realization WHERE client_key=%s AND event_date BETWEEN %s AND %s
                    UNION ALL SELECT coalesce(service_date,act_date),0,0 FROM yandex_fact_services WHERE client_key=%s AND coalesce(service_date,act_date) BETWEEN %s AND %s)
                    SELECT date::text,sum(revenue) revenue,sum(buyouts) buyouts,bool_or(revenue IS NULL) unknown FROM events GROUP BY 1''',(client,start,end,client,start,end));source_days=cur.fetchall()
            else:source_days=[]
            for r in source_days:
                target=daily[r['date']];target['revenue']=D(target['revenue'])+D(r['revenue']);target['buyouts']=D(target['buyouts'])+D(r['buyouts']);target['markets'].append(m)
                target['unknown_revenue'] |= bool(r.get('unknown'))
            for e in read_expenses(cur,m,start,end):
                group='promotion' if expense_group(e['expense_name'],e.get('notes'))=='promotion' else 'operating'
                for dt in daily:
                    amount=allocate_expense(e['source_amount'],date.fromisoformat(e['date_from']),date.fromisoformat(e['date_to']),date.fromisoformat(dt),date.fromisoformat(dt))
                    if amount:add(m,dt,e['expense_name'],group,-amount,str(e['expense_id'])+':'+dt,source_ref=f"Расход {e['expense_id']} · {e['date_from']} — {e['date_to']}",source_kind='Внесённый расход')
    for b in read_range(config,client,start,end):
        for row in b['rows']:
            for m in (MARKETS if market=='all' else [market]):
                if row['amounts'].get(m) is None:continue
                for dt,amount in allocated_days(row['amounts'][m],b['month'],b['allocation_mode']):
                    if dt in daily:add(m,dt,row['expense_name'],'fixed',-amount,row['id']+':'+dt,source_ref=f"Бюджет {str(b['month'])[:7]} · версия {b['revision']}",source_kind='Месячный бюджет')
    return dict(operations=operations,articles=list(articles.values()),days=list(daily.values()),sources=status,date_from=str(start),date_to=str(end),client=client,marketplace=market)


def summarize(data):
    cells=defaultdict(lambda:dict(charge=Decimal(0),credit=Decimal(0),net=Decimal(0),count=0,unknown=0))
    for op in data['operations']:
        c=cells[(op['date'],op['key'])];c['count']+=1
        if op['impact'] is None:c['unknown']+=1;continue
        c['charge']+=max(-op['impact'],0);c['credit']+=max(op['impact'],0);c['net']-=op['impact']
    result={k:v for k,v in data.items() if k!='operations'}
    result['days']=[dict(d,values={key:{k:float(v) if isinstance(v,Decimal) else v for k,v in c.items()} for (dt,key),c in cells.items() if dt==d['date']},
        revenue=None if d['unknown_revenue'] else float(d['revenue']) if d['revenue'] is not None else None,buyouts=float(d['buyouts']) if d['buyouts'] is not None else None) for d in data['days']]
    return result


def operation_page(data,key,query='',page=1,limit=100):
    page=max(1,int(page));limit=max(1,min(200,int(limit)));query=query.strip().casefold()[:200]
    rows=[r for r in data['operations'] if r['key']==key and (not query or query in ' '.join(str(r.get(k) or '') for k in ('id','date','label','sku','barcode','article','source_ref')).casefold())]
    rows.sort(key=lambda r:(r['date'],r['id']),reverse=True)
    total=sum((r['impact'] for r in rows if r['impact'] is not None),Decimal(0))
    return dict(rows=[dict(r,impact=float(r['impact']) if r['impact'] is not None else None) for r in rows[(page-1)*limit:page*limit]],total=len(rows),impact=float(total),unknown=sum(r['impact'] is None for r in rows),page=page,limit=limit)


def operation_csv(data,key,query=''):
    import csv
    from io import StringIO
    query=query.strip().casefold()[:200];out=StringIO();writer=csv.writer(out,delimiter=';')
    writer.writerow(['Дата','Площадка','Статья','Воздействие, ₽','Тип','SKU','EAN','Артикул','Источник','ID'])
    for r in data['operations']:
        if r['key']!=key or query and query not in ' '.join(str(r.get(k) or '') for k in ('id','date','label','sku','barcode','article','source_ref')).casefold():continue
        values=[r.get(k) for k in ('date','marketplace','label','impact','source_kind','sku','barcode','article','source_ref','id')]
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in values])
    return ('\ufeff'+out.getvalue()).encode('utf-8')
