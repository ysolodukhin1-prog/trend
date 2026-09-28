"""Derived product allocation; source ledger is never modified."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR
from unit_result_contract import result_contract

FIELDS = ('commission_cost','acquiring_cost','logistics_cost','reverse_cost','fulfillment_cost','storage_cost','advertising_cost')
SUM_FIELDS = ('revenue','net','units',*FIELDS,'source_rows','missing_amount','unclassified_service_rows')
D = lambda v: Decimal(str(v))

def compact_articles(articles):
    groups={}
    for a in articles:
        key=tuple(a.get(k) for k in ('name','component','source_field','service_type','service_name'))
        if key not in groups:groups[key]=dict(a)
        else:
            r=groups[key];r['amount']=None if r.get('amount') is None or a.get('amount') is None else float(D(r['amount'])+D(a['amount']))
    return list(groups.values())


def split_money(amount, weights):
    weights = [D(w) for w in weights]
    if not weights or any(not w.is_finite() or w < 0 for w in weights) or not sum(weights):
        raise ValueError('Invalid allocation base')
    value = D(amount)
    if not value.is_finite(): raise ValueError('Invalid allocation amount')
    cents = int((abs(value)*100).quantize(Decimal(1),rounding=ROUND_HALF_UP))
    denominator = sum(weights)
    quotas = [cents*w/denominator for w in weights]
    parts = [int(q.to_integral_value(rounding=ROUND_FLOOR)) for q in quotas]
    order = sorted(range(len(parts)),key=lambda i:(-(quotas[i]-parts[i]),i))
    for i in order[:cents-sum(parts)]: parts[i] += 1
    return [float(Decimal(p)*(-1 if value < 0 else 1)/100) for p in parts]


def consolidate(rows):
    groups = {}
    for r in rows:
        barcodes = r.get('barcodes') or []
        identity = 'barcode:'+str(barcodes[0]) if len(barcodes)==1 else 'sku:'+str(r['sku'])+':'+str(r.get('variant') or '')
        key = '|'.join((r['marketplace'],str(r['cabinet']),identity))
        groups.setdefault(key,[]).append(r)
    result=[]
    for key,members in groups.items():
        first=next((r for r in members if r['sku']!='__unallocated__' and r.get('revenue') is not None),members[0])
        r=deepcopy(first);r.update(key=key,source_members=members)
        if r['sku']=='__unallocated__' and len(r.get('barcodes',[]))==1:
            r['sku']='barcode:'+str(r['barcodes'][0]);r['identity_source']='exact_barcode_without_marketplace_sku'
        schemes=list(dict.fromkeys(m.get('scheme') for m in members))
        r['schemes']=schemes;r['scheme']=schemes[0] if len(schemes)==1 else 'Все схемы ('+', '.join(schemes)+')'
        for field in SUM_FIELDS:
            values=[D(m[field]) for m in members if m.get(field) is not None]
            r[field]=float(sum(values)) if values else None
        r['known_units']=r['units']
        if any(m.get('fractional_quantity_rows') or (m.get('revenue') and m.get('units') is None) for m in members):r['units']=None
        if any(m.get('net') is None for m in members):r['net']=None
        costs=[(m.get('cogs') or {}).get('amount') for m in members]
        r['cogs']=first.get('cogs') if costs and all(c is not None and c==costs[0] for c in costs) else None
        r['historical_cogs_covered']=bool(r['cogs']) and all(m.get('historical_cogs_covered') for m in members)
        for field in ('report_articles','service_components'):r[field]=[deepcopy(a) for m in members for a in m.get(field,[])]
        r['source_warnings']=list(dict.fromkeys(w for m in members for w in m.get('source_warnings',[])))
        result.append(r)
    return result


def allocated_products(source):
    rows=consolidate(source)
    for r in rows:
        r['direct_marketplace_cost']=float(D(r['revenue'])-D(r['net'])) if r.get('revenue') is not None and r.get('net') is not None else None
        r['allocated_marketplace_cost']=0;r['allocation_entries']=[]
    removed=set()
    for pool in (r for r in rows if r['sku']=='__unallocated__'):
        articles=pool.get('report_articles') or pool.get('service_components') or []
        expense_only=bool(articles) and all(a.get('amount') is not None for a in articles) and pool.get('net') is not None and abs(sum(D(a['amount']) for a in articles)+D(pool['net']))<D('.005')
        if any(pool.get(f)!=0 and not (pool.get(f) is None and expense_only) for f in ('revenue','units')) or pool.get('net') is None or pool.get('missing_amount'):
            pool['allocation_status']='unresolved_source';continue
        recipients=[r for r in rows if r['sku']!='__unallocated__' and r['marketplace']==pool['marketplace'] and
                    (pool['cabinet']=='__unallocated__' or r['cabinet']==pool['cabinet']) and r.get('net') is not None and not r.get('missing_amount')]
        if not recipients:pool['allocation_status']='no_recipients';continue
        units=[max(0,r.get('units') or 0) for r in recipients];revenue=[max(0,r.get('revenue') or 0) for r in recipients]
        basis='positive_retained_units' if any(units) else 'positive_revenue' if any(revenue) else 'equal_products'
        weights=units if any(units) else revenue if any(revenue) else [1]*len(recipients)
        costs={f:D(pool.get(f) or 0) for f in FIELDS};costs['other']=-D(pool['net'])-sum(costs.values())
        shares={f:split_money(v,weights) for f,v in costs.items()}
        article_shares={f:[(a,split_money(a['amount'],weights) if a.get('amount') is not None else None) for a in compact_articles(pool.get(f,[]))] for f in ('report_articles','service_components')}
        for i,r in enumerate(recipients):
            amount=sum(D(v[i]) for v in shares.values())
            for f in FIELDS:r[f]=float(D(r.get(f) or 0)+D(shares[f][i]))
            r['net']=float((D(r['net'])-amount).quantize(D('.01'),rounding=ROUND_HALF_UP))
            r['allocated_marketplace_cost']=float(D(r['allocated_marketplace_cost'])+amount)
            r['allocation_entries'].append(dict(source_key=pool['key'],source_cabinet=pool['cabinet'],basis=basis,weight=weights[i],total_weight=sum(weights),amount=float(amount),components={f:v[i] for f,v in shares.items()}))
            for f,entries in article_shares.items():
                r[f].extend(dict(a,amount=values[i] if values is not None else None,allocation_source=pool['key'],allocation_basis=basis) for a,values in entries)
            r['allocation_status']='allocated'
        removed.add(pool['key'])
    result=[r for r in rows if r['key'] not in removed]
    for r in result:
        covered=r.get('historical_cogs_covered') and not r.get('missing_amount') and r.get('net') is not None and r.get('units') is not None and r.get('cogs')
        r['contribution_actual']=float(D(r['net'])-D(r['units'])*D(r['cogs']['amount'])) if covered else None
        current_cost=D(r['units'])*D(r['cogs']['amount']) if r.get('units') is not None and r.get('cogs') else Decimal(0)
        r['known_cost_profit_current']=float(D(r['net'])-current_cost) if r.get('net') is not None else None
        r['known_cost_profit_dated']=float(D(r['net'])-(current_cost if r.get('historical_cogs_covered') else 0)) if r.get('net') is not None else None
        missing=[]
        if not r.get('cogs'):missing.append('себестоимость')
        if r.get('missing_amount'):missing.append('суммы части операций')
        if r.get('net') is None:missing.append('начисления')
        r['calculation_status']=result_contract('period_before_taxes_and_external_costs',missing,['распределение общих расходов'] if r['allocation_entries'] else [])
    return result


def allocation_summary(source, products):
    result=[]
    for marketplace in ('wb','ozon','yandex'):
        src=[r for r in source if r['marketplace']==marketplace];dst=[r for r in products if r['marketplace']==marketplace]
        total=lambda rows,f:sum((D(r.get(f) or 0) for r in rows),Decimal(0))
        differences={f:float(total(dst,f)-total(src,f)) for f in ('net','revenue','units',*FIELDS)}
        quantity_partial=any(r.get('units') is None and r.get('revenue') for r in src+dst)
        known_units_difference=float(total(dst,'known_units')-total(src,'units'))
        residual=[r for r in dst if r['sku']=='__unallocated__']
        result.append(dict(marketplace=marketplace,allocated_cost=float(total(dst,'allocated_marketplace_cost')),remaining_rows=len(residual),
          remaining_net=float(total(residual,'net')),differences=differences,reconciled=all(abs(v)<.011 for v in differences.values()),
          known_amounts_reconciled=all(abs(v)<.011 for k,v in differences.items() if k!='units'),known_units_difference=known_units_difference,
          quantity_status='partial' if quantity_partial else 'reconciled' if abs(known_units_difference)<.011 else 'mismatch',
          allocation_status='complete' if not residual else 'partial',source_completeness='unverified'))
    return result
