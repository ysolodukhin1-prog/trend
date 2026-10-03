"""WB management P&L: source amounts, current catalogue costs and unit tax inputs."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import re
from km_trade_finance import connect_km
from pl_workbench import EXPRESSIONS, ARTICLE_LABELS, SIGN, choose_cost
from pulse_financial_model import allocate_expense

D = lambda v: Decimal(str(v or 0))
TAX_KEYS = ('incomeTaxPct','vatPct','taxMode','minimumTaxPct','inputVat','inputVatVariablePct','cogsVatPct','taxNondeductible')


def tax_profile(raw):
    if not isinstance(raw, dict): raise ValueError('Некорректные параметры налогов юнитки')
    result = {}
    for key in TAX_KEYS:
        value = raw.get(key)
        if value is None: result[key] = None; continue
        if isinstance(value, bool): raise ValueError('Некорректная ставка налога')
        try: value = D(value)
        except InvalidOperation: raise ValueError("Некорректная ставка налога")
        if not value.is_finite() or value < 0 or ('Pct' in key and value > 100): raise ValueError('Некорректная ставка налога')
        result[key] = value
    if result['taxMode'] not in (None, 0, 1): raise ValueError('Неизвестная база налога')
    return result


def short_article(text):
    return re.sub(r',?\s*документ\s*№.*$', '', str(text or ''), flags=re.I).strip()


def expense_group(label, notes=''):
    if str(notes).startswith('[pl:promotion]'): return 'promotion'
    if str(notes).startswith('[pl:operating]'): return 'operating'
    return 'promotion' if re.search(r'продвижен|реклам|wibes', label, re.I) else 'marketplace'


def finalize(day_rows, articles, tax):
    """Compute one selected-period tax, allocate it so every matrix grain reconciles."""
    for r in day_rows:
        present = r['finance_present']
        r['cogs'] = -r['known_cogs'] if present else None
        r['gross_margin'] = D(r['revenue']) + D(r['cogs']) if present else None
        for group in ('marketplace','promotion','fixed','operating'):
            r[group] = sum((D(r.get(a['key'])) for a in articles if a['group'] == group), Decimal(0)) if present or group in ('fixed','operating') or any(r.get(a['key']) is not None for a in articles if a['group']==group) else None
        r['ebitda'] = D(r['gross_margin'])+D(r['marketplace'])+D(r['promotion'])+D(r['operating'])+D(r['fixed']) if present or r['operating'] or r['promotion'] or r['fixed'] else None
        rate = tax.get('vatPct')
        units = D(r.get('buyouts'))-D(r.get('returns'))
        r['vat'] = -(D(r['revenue'])*rate/(100+rate)-D(tax.get('inputVat'))*units-D(r['revenue'])*D(tax.get('inputVatVariablePct'))/100) if rate is not None and present else None
        r['cogs_vat'] = -r['known_cogs']*D(tax.get('cogsVatPct'))/100 if present else None
    # Round money once and retain cent reconciliation at every time grain.
    for field in ('vat','cogs_vat'):
        present_rows=[r for r in day_rows if r[field] is not None]
        exact=sum((r[field] for r in present_rows),Decimal(0)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        for r in present_rows:r[field]=r[field].quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        if present_rows:present_rows[-1][field]+=exact-sum((r[field] for r in present_rows),Decimal(0))
    revenue = sum((D(r['revenue']) for r in day_rows),Decimal(0))
    vat = sum((D(r['vat']) for r in day_rows),Decimal(0))
    cogs_vat = sum((D(r['cogs_vat']) for r in day_rows),Decimal(0))
    ebitda = sum((D(r['ebitda']) for r in day_rows),Decimal(0))
    known = tax.get('incomeTaxPct') is not None and tax.get('vatPct') is not None
    net_units = sum((D(r.get('buyouts'))-D(r.get('returns')) for r in day_rows),Decimal(0))
    output_vat = revenue*D(tax.get('vatPct'))/(100+D(tax.get('vatPct')))
    income_base = revenue-output_vat
    taxable = max(Decimal(0),ebitda+vat+cogs_vat+D(tax.get('taxNondeductible'))*net_units) if tax.get('taxMode') == 1 else max(Decimal(0),income_base)
    amount = max(taxable*D(tax.get('incomeTaxPct'))/100,max(Decimal(0),income_base)*D(tax.get('minimumTaxPct'))/100) if known else None
    if amount is not None: amount=amount.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    allocated=Decimal(0)
    weights=[max(Decimal(0),D(r['revenue'])) for r in day_rows];denom=sum(weights)
    for r,weight in zip(day_rows,weights):
        r['income_tax'] = (-amount*weight/denom).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if known and denom else Decimal(0) if known else None
        allocated+=D(r['income_tax'])
        if weight and weight==sum(weights[day_rows.index(r):]) and known:r['income_tax']+=-amount-allocated
        if not r['finance_present'] and not r['operating'] and not r['promotion'] and not r['fixed']:
            r['income_tax']=None;r['taxes']=None;r['net_profit']=None;continue
        r['taxes'] = D(r['vat'])+D(r['cogs_vat'])+D(r['income_tax']) if known else None
        r['net_profit'] = D(r['ebitda'])+r['taxes'] if r['ebitda'] is not None and known else None
    return day_rows


RATIO_SPECS = {
    'buyout_pct': ('buyouts', 'orders', 1),
    'cogs_pct': ('cogs', 'revenue', -1),
    'margin_pct': ('gross_margin', 'revenue', 1),
    'operating_profit_pct': ('operating_profit', 'revenue', 1),
    'ebitda_pct': ('ebitda', 'revenue', 1),
    'net_profit_pct': ('net_profit', 'revenue', 1),
}


def update_financial_ratios(values):
    """Ratios of aggregate flows, never sums/averages of daily percentages."""
    margin, marketplace = values.get('gross_margin'), values.get('marketplace')
    values['operating_profit'] = float(D(margin) + D(marketplace)) if margin is not None and marketplace is not None else None
    for key, (numerator, denominator, sign) in RATIO_SPECS.items():
        a, b = values.get(numerator), values.get(denominator)
        values[key] = float(D(a) * sign / D(b) * 100) if a is not None and b is not None and D(b) > 0 else None
    return values


def aggregate(rows, keys):
    values = {key:sum((D(r.get(key)) for r in rows if r.get(key) is not None),Decimal(0)) if any(r.get(key) is not None for r in rows) else None for key in keys}
    update_financial_ratios(values)
    return dict(from_date=rows[0]['date'],to_date=rows[-1]['date'],values={k:float(v) if v is not None else None for k,v in values.items()},
        missing_cost_units=float(sum((D(r['missing_cost_units']) for r in rows),Decimal(0))),
        sku_cost_units=float(sum((D(r['sku_cost_units']) for r in rows),Decimal(0))),
        finance_days=sum(r['finance_present'] for r in rows),days=len(rows))


def model_rows(arts):
    definitions=[('orders','Заказы, шт','number'),('order_amount','Заказы, ₽','money'),('buyouts','Выкупы, шт','number'),('buyout_amount','Выкупы, ₽','money'),('buyout_pct','Выкуп, %','percent'),('returns','Возвраты, шт','number'),('return_amount','Возвраты, ₽','money'),('revenue','Выручка','money'),('cogs','Себестоимость','money'),('cogs_pct','Себестоимость, % выручки','percent'),('gross_margin','Валовая маржа','money'),('margin_pct','Валовая маржа, %','percent')]
    rows=[dict(key=k,label=l,unit=u,kind='total' if k in ('revenue','gross_margin') else 'metric',help='Заказы: воронка площадки по дате заказа; выкупы и возвраты: финансовый отчёт по дате операции. Это потоки периода, не одна когорта.' if k in ('orders','order_amount','buyouts','buyout_amount','returns','return_amount') else 'Текущая себестоимость справочника применена к продажам и возвратам выбранного периода. При отсутствии EAN единая цена того же SKU используется отдельно помеченным расчётом.' if k=='cogs' else '') for k,l,u in definitions]
    for row in rows:
        if row['key'] == 'buyout_pct': row['help'] = 'Выкупленные штуки ÷ заказанные штуки × 100%. Потоки выбранного периода по разным датам, не когортный выкуп; может превышать 100%.'
        elif row['key'] == 'cogs_pct': row['help'] = 'Себестоимость со знаком расхода, взятым обратно, ÷ выручка × 100%. При неположительной выручке — прочерк.'
        elif row['key'] == 'margin_pct': row['help'] = 'Валовая маржа ÷ выручка × 100%. При неположительной выручке — прочерк.'
    for group,label in [('marketplace','Расходы маркетплейса'),('promotion','Расходы на продвижение'),('fixed','Постоянные расходы'),('operating','Операционные расходы')]:
        rows.append(dict(key=group,label=label,unit='money',kind='group',help='Месячный бюджет ÷ все календарные дни месяца × дни выбранного периода. Раскрыть статьи бюджета.' if group=='fixed' else 'Все статьи доступного источника. Раскрыть группу для детализации.'))
        rows.extend(a for a in arts if a['group']==group)
        if group == 'marketplace':
            rows.extend([dict(key='operating_profit',label='Операционная прибыль',unit='money',kind='total',help='Валовая маржа − расходы маркетплейса. До продвижения, постоянных и операционных расходов.'),dict(key='operating_profit_pct',label='Операционная прибыль, % выручки',unit='percent',kind='metric',help='Операционная прибыль ÷ выручка × 100%. При неположительной выручке — прочерк.')])
    rows.extend([dict(key='ebitda',label='EBITDA',unit='money',kind='total',help='Выручка − себестоимость − услуги площадки − продвижение − постоянные расходы − внесённые операционные расходы.'),dict(key='taxes',label='Налоги',unit='money',kind='group',help='Параметры из юнит-экономики. Налог на доход/прибыль за выбранный период распределён по положительной выручке дней; группы согласуются с итогом.'),dict(key='vat',label='НДС с учётом входящего',group='taxes',unit='money',kind='article'),dict(key='cogs_vat',label='НДС себестоимости',group='taxes',unit='money',kind='article'),dict(key='income_tax',label='Налог на доход / прибыль',group='taxes',unit='money',kind='article'),dict(key='net_profit',label='Чистая прибыль',unit='money',kind='total',help='EBITDA − налоги. При неполной себестоимости показан расчёт по сопоставленной части; прочерк — налоговая ставка не задана.')])
    for key,label,after in [('ebitda_pct','EBITDA, % выручки','ebitda'),('net_profit_pct','Чистая прибыль (NI), % выручки','net_profit')]:
        index=next(i for i,r in enumerate(rows) if r['key']==after)+1
        rows.insert(index,dict(key=key,label=label,unit='percent',kind='metric',help=('EBITDA' if after=='ebitda' else 'Чистая прибыль')+' ÷ выручка × 100%. При неизвестной прибыли или неположительной выручке — прочерк.'))
    return rows


def attach_financial_model(config, payload, raw_tax=None):
    if payload.get('financial_model'):
        return payload
    if payload.get('available') is False and payload.get('marketplace') != 'all':
        from pl_monthly_budgets import budget_only
        return budget_only(config, payload)
    if payload.get('marketplace') != 'wb':
        from pl_multi_model import attach_multi_model
        return attach_multi_model(config,payload,raw_tax)
    if config.get('database') != payload['client']: raise ValueError('Клиент и база не совпадают')
    start,end=map(date.fromisoformat,(payload['date_from'],payload['date_to']))
    if end<start or (end-start).days>366: raise ValueError('Период до 367 дней')
    if raw_tax is None:
        from unit_planner_settings import load
        saved=load(config,payload['client'])
        raw_tax=(saved.get('settings') or {}).get('global',{})
    tax=tax_profile(raw_tax or {})
    registry=defaultdict(list);daily={};articles={};missing=[];sku_costs=defaultdict(lambda:dict(cost=Decimal(0),missing=0,estimated=0));cost_events=[]
    day=start
    while day<=end:
        daily[str(day)]=dict(date=str(day),orders=None,order_amount=None,buyouts=None,buyout_amount=None,returns=None,return_amount=None,revenue=None,known_cogs=Decimal(0),missing_cost_units=Decimal(0),sku_cost_units=Decimal(0),finance_present=False)
        day+=timedelta(days=1)
    def add(day,key,label,group,amount,source):
        articles.setdefault(key,dict(key=key,label=label,group=group,unit='money',kind='article',help=source))
        daily[str(day)][key]=D(daily[str(day)].get(key))+D(amount)
    with connect_km(config) as conn,conn.cursor() as cur:
        cur.execute('SELECT barcode,amount,valid_from,source_ref FROM unit_cogs_versions WHERE valid_from<=%s ORDER BY valid_from,created_at',(date.today(),))
        for r in cur.fetchall(): registry[str(r['barcode']).strip()].append(dict(r))
        from unit_cost_basis import catalog_cost_resolver,wb_cost_catalog
        resolve_wb_cost,_=catalog_cost_resolver(wb_cost_catalog(cur),[v for versions in registry.values() for v in versions])
        cur.execute(f"""SELECT operation_date::text AS day,nm_id::text sku,trim(sku) barcode,max(vendor_code) article,
           ({SIGN}) sign,sum(quantity) units,sum(retail_amount*({SIGN})) revenue
           FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s AND (retail_amount<>0 OR for_pay<>0)
           GROUP BY operation_date,nm_id,trim(sku),({SIGN})""",(start,end))
        events=cur.fetchall()
        for event in events:
            r=daily[event['day']];r['finance_present']=True
            returned=event['sign']<0;units=D(event['units']);revenue=abs(D(event['revenue']))
            qty_key,amount_key=('returns','return_amount') if returned else ('buyouts','buyout_amount')
            r[qty_key]=D(r[qty_key])+units;r[amount_key]=D(r[amount_key])+revenue
            version,_=choose_cost(registry.get(event['barcode'],[]),date.today(),'current')
            cost=D(version['amount']) if version else None
            via_sku=False
            if cost is None and not event['barcode']:
                fallback,_,_=resolve_wb_cost('wb',event['sku'],event['article'])
                if fallback is not None:cost=fallback;r['sku_cost_units']+=units;via_sku=True
            if cost is None:
                r['missing_cost_units']+=units;missing.append(dict(barcode=event['barcode'],article=event['article'],sku=event['sku'],units=float(units),date=event['day']))
            else:r['known_cogs']+=cost*units*event['sign']
            sc=sku_costs[event['sku']]
            sc['cost']+=D(cost)*units*event['sign'];sc['missing']+=float(units) if cost is None else 0;sc['estimated']+=float(units) if via_sku else 0
            cost_events.append(dict(date=event['day'],sku=event['sku'],article=event['article'],barcode=event['barcode'],units=float(units*event['sign']),unit_cost=float(cost) if cost is not None else None,cost=float(cost*units*event['sign']) if cost is not None else None,source='Единая цена того же SKU' if via_sku else str(version.get('source_ref','')) if version else 'Нет цены'))
        columns=','.join(f'sum({expr}) AS {key}' for key,expr in EXPRESSIONS.items())
        cur.execute(f'SELECT operation_date::text AS day,{columns} FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s GROUP BY operation_date',(start,end))
        for r in cur.fetchall():
            day=r['day'];target=daily[day];target['finance_present']=True;target['revenue']=D(r['revenue'])
            for key in ('buyouts','buyout_amount','returns','return_amount'):target[key]=D(target[key])
            for key in EXPRESSIONS:
                if key in ('revenue','deduction','penalty','rebill_logistics'):continue
                add(day,'mp_'+key,ARTICLE_LABELS[key],'marketplace',r[key],'Финансовый отчёт WB: '+ARTICLE_LABELS[key])
        for field,label in [('deduction','Удержания'),('penalty','Штрафы'),('rebill_logistic_cost','Возмещение логистики')]:
            cur.execute(f"""SELECT operation_date::text AS day,coalesce(nullif(raw_payload->>'bonusTypeName',''),nullif(raw_payload->>'bonus_type_name',''),seller_oper_name) label,sum({field}) amount
               FROM wb_finance_lines WHERE operation_date BETWEEN %s AND %s AND {field}<>0 GROUP BY operation_date,2""",(start,end))
            for r in cur.fetchall():
                title=short_article(r['label']) or label;group=expense_group(title)
                key=group+'_'+field+'_'+hashlib.sha1(title.encode()).hexdigest()[:10]
                add(r['day'],key,title,group,-D(r['amount']),'Строки WB: '+field+'. Уже исключены из остальных групп; повторно не прибавляются.')
        cur.execute("""SELECT report_date::text AS day,CASE WHEN count(*)=count(DISTINCT wb_nmid) AND count(*)=count(ordered_units) THEN sum(ordered_units) END orders,
           CASE WHEN count(*)=count(DISTINCT wb_nmid) AND count(*)=count(ordered_amount_rub) THEN sum(ordered_amount_rub) END amount
           FROM wb_funnel_daily WHERE report_date BETWEEN %s AND %s GROUP BY report_date""",(start,end))
        for r in cur.fetchall():daily[r['day']].update(orders=r['orders'],order_amount=r['amount'])
    from pl_monthly_budgets import read_range, apply_budgets
    budgets=apply_budgets(daily,articles,read_range(config,payload['client'],start,end),'wb')
    for expense in payload.get('all_expenses',[]):
        group='promotion' if expense_group(expense['expense_name'],expense.get('notes'))=='promotion' else 'operating'
        key='own_'+str(expense['expense_id'])
        for day in daily:
            amount=allocate_expense(expense['source_amount'],date.fromisoformat(expense['date_from']),date.fromisoformat(expense['date_to']),date.fromisoformat(day),date.fromisoformat(day))
            add(day,key,expense['expense_name'],group,-amount,'Внесённый расход; распределение по дням периода записи.')
    arts=list(articles.values())
    if not any(a['group']=='promotion' for a in arts):articles['promotion_registered']=dict(key='promotion_registered',label='Начисления за продвижение',group='promotion',unit='money',kind='article',help='В финансовом отчёте за период начисления не найдены.')
    if not any(a['group'] in ('fixed','operating') for a in arts):articles['operating_registered']=dict(key='operating_registered',label='Внесённые операционные расходы',group='operating',unit='money',kind='article',help='Ранее внесённые операционные расходы. Сейчас записи отсутствуют; полнота операционных расходов не подтверждена.')
    arts=list(articles.values())
    for r in daily.values():
        for a in arts:
            if a['group'] in ('fixed','operating') or r['finance_present']:r.setdefault(a['key'],Decimal(0))
    days=finalize(list(daily.values()),arts,tax)
    rows=model_rows(arts)
    keys=[r['key'] for r in rows if r['unit']!='percent'];periods={}
    for kind in ('day','week','month'):
        groups=defaultdict(list)
        for r in days:
            d=date.fromisoformat(r['date']);key=str(d) if kind=='day' else str(d-timedelta(days=d.weekday())) if kind=='week' else str(d)[:7]
            groups[key].append(r)
        periods[kind]=[aggregate(rs,keys) for rs in groups.values()]
    total=aggregate(days,keys)
    for product in payload.get('products',[]):
        sc=sku_costs[str(product['sku'])];cost=float(sc['cost']) if not sc['missing'] else None
        value=float(D(product['revenue'])-D(product['marketplace_costs_before_cogs'])-D(cost)) if cost is not None else None
        product.update(cogs=cost,cogs_source='Единая цена SKU' if sc['estimated'] else 'Справочник',management_result=value,profit_before_common_costs=value,margin_pct=value/float(product['revenue'])*100 if value is not None and product['revenue'] else None)
    # Keep the existing article and export views consistent with the new matrix.
    v=total['values'];t=payload['totals'];result=v['net_profit'] if v['net_profit'] is not None else v['ebitda']
    t.update(cogs=-v['cogs'] if v['cogs'] is not None else None,gross_profit=v['gross_margin'],management_result=result,net_profit=v['net_profit'],tax_configured=tax.get('incomeTaxPct') is not None and tax.get('vatPct') is not None,vat=-v['vat'] if v['vat'] is not None else None,tax=-v['income_tax'] if v['income_tax'] is not None else None,management_margin_pct=result/v['revenue']*100 if result is not None and v['revenue'] else None)
    mapped={'cogs':v['cogs'],'gross_profit':v['gross_margin'],'vat_model':v['vat'],'tax':v['income_tax'],'net_profit':v['net_profit'],'management_result':result}
    for row in payload.get('statement',[]):
        if row['key'] in mapped:
            row['amount']=mapped[row['key']];row['revenue_pct']=row['amount']/v['revenue']*100 if row['amount'] is not None and v['revenue'] else None
        if row['key']=='management_result':row['label']='Результат модели' if t['tax_configured'] else 'EBITDA'
    for month in payload.get('monthly',[]):
        matched=next((p for p in periods['month'] if p['from_date'][:7]==str(month['month'])[:7]),None)
        if matched:
            mv=matched['values'];month.update(cogs=-mv['cogs'] if mv['cogs'] is not None else None,gross_profit=mv['gross_margin'],management_result=mv['net_profit'] if mv['net_profit'] is not None else mv['ebitda'],net_profit=mv['net_profit'])
    payload['financial_model']=dict(monthly_budgets=budgets,cost_events=cost_events,rows=rows,periods=periods,total=total,missing_costs=missing,tax_profile={k:float(v) if v is not None else None for k,v in tax.items()},tax_source='Юнит-экономика · параметры текущего аккаунта',registry_barcodes=len(registry),basis='current_registry',tax_ready=tax.get('incomeTaxPct') is not None and tax.get('vatPct') is not None,
        reconciliation=dict(marketplace_and_promotion=float(-(D(total['values']['marketplace'])+D(total['values']['promotion']))),source_services=payload['totals']['ozon_costs']))
    from pl_monthly_budgets import sync_statement
    return sync_statement(payload)
