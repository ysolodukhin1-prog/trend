"""Dated financial scenario ledger with a fixed horizon and no-shipment option."""
from datetime import date,timedelta
from decimal import Decimal
from cluster_supply_routes import decimal_value,integer,readiness,freight_cost,SCHEMES
from cluster_supply_inventory import simulate,required_quantity

MONEY_FIELDS=('price','cogs','common_cost_per_buyout','advertising_per_order','return_cost_per_order',
    'current_service_per_buyout','current_storage_per_unit_day','capital_annual_pct')


def money(value):return str(value.quantize(Decimal('0.01')))


def validate_item(raw,horizon,as_of):
    if not isinstance(raw,dict):raise ValueError('Некорректная строка сценария')
    item=dict(raw)
    for key in ('key','sku','inventory_id','marketplace','scheme','cluster','tax_basis'):
        if not isinstance(item.get(key),str) or not item[key].strip() or len(item[key])>250:
            raise ValueError(f'{key}: укажите идентификатор')
    if item['marketplace'] not in SCHEMES or item['scheme'] not in SCHEMES[item['marketplace']]:
        raise ValueError('Несовместимая схема площадки')
    item['initial_stock']=integer(item.get('initial_stock'),'Остаток')
    item['pack_size']=integer(item.get('pack_size',1),'Кратность',1,10000)
    item['target_end_stock']=integer(item.get('target_end_stock',0),'Резерв')
    for key in ('forecast_daily','plan_daily'):
        values=item.get(key)
        if values is None and key=='plan_daily':continue
        if not isinstance(values,list) or len(values)!=horizon:raise ValueError(f'{key}: требуется {horizon} дней')
        item[key]=[decimal_value(v,key) for v in values]
    for key in MONEY_FIELDS:item[key]=decimal_value(item.get(key),key)
    for key in ('buyout_probability','restock_probability'):
        item[key]=decimal_value(item.get(key),key)
        if item[key]>1:raise ValueError(f'{key}: вероятность от 0 до 1')
    for key in ('unit_weight_kg','unit_volume_m3'):
        item[key]=decimal_value(item.get(key),key)
        if item[key]<=0:raise ValueError(f'{key}: нужно положительное значение')
    item['restock_lag_days']=integer(item.get('restock_lag_days'),'Лаг возврата',1,180)
    item['arrivals']=item.get('arrivals') or []
    if not isinstance(item['arrivals'],list) or len(item['arrivals'])>200:raise ValueError('Проверьте поступления')
    seen=set()
    for arrival in item['arrivals']:
        identifier=str(arrival.get('id') or '')
        if not identifier or identifier in seen:raise ValueError('Поступления требуют уникальные ID')
        seen.add(identifier)
        if arrival.get('pool')=='new':raise ValueError('Нельзя маркировать сохранённый приход как новую поставку')
        integer(arrival.get('day'),'День прибытия',0,365);integer(arrival.get('quantity'),'Приход')
    evidence=item.get('evidence') or {}
    item['input_kind']='observed'
    for field,max_age in [('demand',3),('stock',2),('finance',31)]:
        e=evidence.get(field) or {}
        if not e.get('ref') or e.get('complete') is not True or e.get('kind') not in {'observed','manual'}:
            raise ValueError(f'{field}: нужны источник, тип и подтверждение полноты')
        try:stamp=date.fromisoformat(str(e.get('as_of','')))
        except ValueError:raise ValueError(f'{field}: укажите дату источника') from None
        if not 0<=(as_of-stamp).days<=max_age:raise ValueError(f'{field}: источник устарел или датирован будущим')
        if e['kind']=='manual':item['input_kind']='manual_scenario'
    return item


def quantities(item,route,horizon,ship_offset):
    lead=ship_offset+route['lead_days']
    pack=item['pack_size']
    # LCM ensures both product and route packing requirements are respected.
    from math import lcm
    pack=lcm(pack,route['pack_size'])
    args=(item['initial_stock'],item['arrivals'],lead,item['target_end_stock'],pack)
    forecast=required_quantity(item['forecast_daily'],*args)
    plan=None if item.get('plan_daily') is None else required_quantity(item['plan_daily'],*args)
    return forecast,plan,pack


def evaluate(item,route,quantity,horizon,ship_offset=0,basis='forecast',include_freight=True):
    quantity=integer(quantity,'Количество поставки')
    daily=item['forecast_daily'] if basis=='forecast' else item.get('plan_daily')
    if daily is None:raise ValueError('План не задан; выберите прогноз')
    arrivals=list(item['arrivals'])
    lead=(ship_offset+route['lead_days']) if route else 0
    if route and quantity:
        arrivals.append({'day':lead,'quantity':quantity,'status':'confirmed','pool':'new'})
    sim=simulate(daily_orders=daily,initial_stock=item['initial_stock'],arrivals=arrivals,
        buyout_probability=item['buyout_probability'],restock_probability=item['restock_probability'],
        restock_lag_days=item['restock_lag_days'])
    old=sim['retained_by_pool'].get('existing',Decimal(0));new=sim['retained_by_pool'].get('new',Decimal(0))
    revenue=sim['retained_units']*item['price']
    cogs=sim['retained_units']*item['cogs']
    common=sim['retained_units']*item['common_cost_per_buyout']
    ads=sim['orders']*item['advertising_per_order']
    reverse=sim['nonretained_units']*item['return_cost_per_order']
    writeoff=sim['writeoff_units']*item['cogs']
    service=old*item['current_service_per_buyout']
    storage=sim['stock_days_by_pool'].get('existing',Decimal(0))*item['current_storage_per_unit_day']
    intake=freight=Decimal(0)
    if route:
        service+=new*Decimal(route['service_per_buyout'])
        storage+=sim['stock_days_by_pool'].get('new',Decimal(0))*Decimal(route['storage_per_unit_day'])
        intake=quantity*Decimal(route['intake_per_unit'])
        if include_freight:freight=freight_cost(route,units=quantity,
            weight_kg=quantity*item['unit_weight_kg'],volume_m3=quantity*item['unit_volume_m3'])
    transit_days=max(0,min(lead,horizon)-ship_offset) if route else 0
    capital=(sim['stock_days_by_pool'].get('existing',Decimal(0))+sim['return_days_by_pool'].get('existing',Decimal(0)))*item['cogs']*item['capital_annual_pct']/36500
    if route:
        capital+=(sim['stock_days_by_pool'].get('new',Decimal(0))+sim['return_days_by_pool'].get('new',Decimal(0))+quantity*transit_days)*item['cogs']*Decimal(route['capital_annual_pct'])/36500
    costs={'cogs_sold':cogs,'common_marketplace':common,'advertising':ads,'returns':reverse,
        'writeoff':writeoff,'route_services':service,'intake':intake,'storage':storage,'capital':capital,'freight':freight}
    result=revenue-sum(costs.values(),Decimal(0))
    return {'route_id':route.get('route_id',route.get('name')) if route else 'no_shipment',
        'quantity':quantity,'result':result,'cash_required':quantity*item['cogs']+intake+freight,
        'revenue':revenue,'costs':costs,'simulation':sim,
        'weight_kg':quantity*item['unit_weight_kg'],'volume_m3':quantity*item['unit_volume_m3']}


def compare_item(raw,routes,*,as_of,ship_date,horizon_days=30,basis='forecast',baseline_route_id=None,stock_limits=None):
    horizon=integer(horizon_days,'Горизонт',1,180)
    as_of=date.fromisoformat(str(as_of));ship=date.fromisoformat(str(ship_date))
    offset=(ship-as_of).days
    if not 0<=offset<horizon:raise ValueError('Отгрузка должна попадать в горизонт сценария')
    if basis not in {'forecast','plan'}:raise ValueError('Выберите план или прогноз')
    item=validate_item(raw,horizon,as_of)
    options=[evaluate(item,None,0,horizon,basis=basis)]
    blocked=[]
    for route in routes:
        if (route['marketplace'],route['scheme'],route['cluster'])!=(item['marketplace'],item['scheme'],item['cluster']):continue
        if route['tax_basis']!=item['tax_basis']:
            blocked.append({'route_id':route.get('route_id'),'missing':['Налоговый базис маршрута и товара отличается']});continue
        state=readiness(route,ship)
        if state['status']!='ready':blocked.append({'route_id':route.get('route_id'),'missing':state['missing']});continue
        qf,qp,pack=quantities(item,route,horizon,offset)
        quantity=qf if basis=='forecast' else qp
        if quantity is None:raise ValueError('Для выбранного SKU нет плана')
        if quantity:
            quantity=max(quantity,((route['min_units']+pack-1)//pack)*pack)
        try:option=evaluate(item,route,quantity,horizon,offset,basis)
        except ValueError as exc:
            blocked.append({'route_id':route.get('route_id'),'missing':[str(exc)]});continue
        option.update(forecast_qty=qf,plan_qty=qp,pack_size=pack)
        if stock_limits is not None and quantity:
            available=stock_limits.get(f"{route['origin']}::{item['inventory_id']}")
            if available is None or quantity>integer(available,'Запас источника'):
                option['blocked_reason']='Недостаточно подтверждённого запаса источника'
        for field,load in [('capacity_units',quantity),('capacity_kg',option['weight_kg']),('capacity_m3',option['volume_m3'])]:
            if route.get(field) is not None and load>Decimal(route[field]):
                option['blocked_reason']='Превышена вместимость маршрута'
        options.append(option)
    baseline=next((o for o in options if o['route_id']==baseline_route_id and not o.get('blocked_reason')),None)
    ready=[o for o in options if not o.get('blocked_reason')]
    best=max(ready,key=lambda o:o['result'])
    for option in options:option['delta_center']=None if baseline is None else option['result']-baseline['result']
    return {'kind':'estimate','input_kind':item['input_kind'],'horizon_days':horizon,'basis':basis,
        'baseline_route_id':baseline_route_id,'best_route_id':best['route_id'],'options':options,'blocked':blocked}
