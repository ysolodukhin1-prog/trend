"""Bounded, deterministic whole-batch search over explicit quantity candidates.

Always reports candidate-search status, never a proof of global optimality.
Fixed freight is evaluated per activated route, with shared physical inventory.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import ceil
import time
from cluster_supply_model import validate_item,quantities,evaluate
from cluster_supply_routes import readiness,freight_cost,decimal_value,integer


def summarize(selection,items,routes,stock_limits,budget,check_budget=True):
    route_loads={};stock_used=defaultdict(int)
    result=sum((o['result'] for o in selection),Decimal(0))
    cash=sum((o['cash_required'] for o in selection),Decimal(0))
    for item,option in zip(items,selection):
        rid=option['route_id'];quantity=option['quantity']
        if rid=='no_shipment' or not quantity:continue
        route=routes[rid];stock_key=f"{route['origin']}::{item['inventory_id']}"
        if stock_key not in stock_limits:return None
        stock_used[stock_key]+=quantity
        if stock_used[stock_key]>stock_limits[stock_key]:return None
        load=route_loads.setdefault(rid,dict(units=0,weight_kg=Decimal(0),volume_m3=Decimal(0)))
        load['units']+=quantity;load['weight_kg']+=option['weight_kg'];load['volume_m3']+=option['volume_m3']
    for rid,load in route_loads.items():
        route=routes[rid]
        for constraint,key in [('capacity_units','units'),('capacity_kg','weight_kg'),('capacity_m3','volume_m3')]:
            if route.get(constraint) is not None and load[key]>Decimal(route[constraint]):return None
        if load['units']<route['min_units']:
            if check_budget:return None
        try:cost=freight_cost(route,**load)
        except ValueError:return None
        load['freight']=cost;result-=cost;cash+=cost
    if check_budget and cash>budget:return None
    return {'result':result,'cash_required':cash,'route_loads':route_loads,'stock_used':dict(stock_used),
        'quantity':sum(o['quantity'] for o in selection)}


def search(candidates,items,routes,stock_limits,budget,max_nodes,progress=None,label='партия'):
    empty=[next(o for o in options if o['route_id']=='no_shipment') for options in candidates]
    best_selection=empty;best=summarize(empty,items,routes,stock_limits,budget)
    visited=0;truncated=False
    def walk(index,chosen):
        nonlocal visited,truncated,best,best_selection
        if visited>=max_nodes:truncated=True;return
        visited+=1
        if progress and visited%1000==0:
            progress(f'ПРОГРЕСС: {visited}/{max_nodes} узлов лимита | {label} | лучший результат {best["result"]:.2f} ₽ | ETA не определена для ограниченного перебора')
        if index==len(items):
            current=summarize(chosen,items,routes,stock_limits,budget)
            if current is not None and (current['result']>best['result'] or
                (current['result']==best['result'] and current['cash_required']<best['cash_required'])):
                best=current;best_selection=list(chosen)
            return
        for option in candidates[index]:
            partial=chosen+[option]
            if summarize(partial,items[:index+1],routes,stock_limits,budget,False) is not None:
                walk(index+1,partial)
            if visited>=max_nodes:truncated=True;break
    walk(0,[])
    return {**best,'selection':best_selection,'visited_nodes':visited,'search_truncated':truncated}


def optimize(payload,routes,progress=print):
    started=time.monotonic()
    as_of=date.fromisoformat(str(payload.get('as_of')));ship=date.fromisoformat(str(payload.get('ship_date')))
    horizon=integer(payload.get('horizon_days',30),'Горизонт',1,180)
    offset=(ship-as_of).days
    if not 0<=offset<horizon:raise ValueError('Дата отгрузки должна попадать в горизонт')
    basis=payload.get('basis','forecast')
    if basis not in {'forecast','plan'}:raise ValueError('Выберите план или прогноз')
    raw_items=payload.get('items')
    if not isinstance(raw_items,list) or not 1<=len(raw_items)<=100:raise ValueError('В сценарии нужно от 1 до 100 строк')
    max_nodes=integer(payload.get('max_nodes',20000),'Лимит перебора',100,100000)
    budget=decimal_value(payload.get('budget'),'Бюджет')
    limits={str(k):integer(v,'Доступный запас') for k,v in (payload.get('stock_limits') or {}).items()}
    initial_limits={str(k):integer(v,'Начальный запас') for k,v in (payload.get('opening_stock_limits') or {}).items()}
    items=[validate_item(i,horizon,as_of) for i in raw_items]
    if len({i['key'] for i in items})!=len(items):raise ValueError('Строки сценария должны иметь разные ключи')
    assigned=defaultdict(int)
    for i in items:
        if i['initial_stock']:
            key=i.get('opening_stock_key')
            if not key or key not in initial_limits:raise ValueError('Подтвердите общий начальный запас и ключ распределения')
            assigned[key]+=i['initial_stock']
            if assigned[key]>initial_limits[key]:raise ValueError('Начальный запас повторно распределён в несколько строк')
    route_map={r['route_id']:r for r in routes}
    if len(route_map)!=len(routes):raise ValueError('Дублируются ID маршрутов')
    if len({r['tax_basis'] for r in routes})>1:raise ValueError('Налоговый базис сравниваемых маршрутов должен совпадать')
    if progress:progress(f'ПЛАН: партия | {len(items)} строк | до {max_nodes} узлов x 2 сравнения | кандидаты 0/25/50/75/100% потребности')
    candidates=[];missing=[];baseline_candidates=[];baseline_complete=True
    for idx,item in enumerate(items):
        options=[evaluate(item,None,0,horizon,basis=basis,include_freight=False)]
        eligible=set()
        for route in routes:
            if (route['marketplace'],route['scheme'],route['cluster'])!=(item['marketplace'],item['scheme'],item['cluster']):continue
            if route['tax_basis']!=item['tax_basis']:
                missing.append({'key':item['key'],'route_id':route['route_id'],'reasons':['Налоговый базис маршрута и товара отличается']});continue
            state=readiness(route,ship)
            if state['status']!='ready':
                missing.append({'key':item['key'],'route_id':route['route_id'],'reasons':state['missing']});continue
            stock_key=f"{route['origin']}::{item['inventory_id']}"
            if stock_key not in limits:
                missing.append({'key':item['key'],'route_id':route['route_id'],'reasons':['Нет подтверждённого общего запаса источника']});continue
            eligible.add(route['route_id'])
            qf,qp,pack=quantities(item,route,horizon,offset)
            need=qf if basis=='forecast' else qp
            if need is None:raise ValueError('Нет плана для выбранной строки')
            minimum=((route['min_units']+pack-1)//pack)*pack
            max_quantity=min(max(need,minimum) if need else 0,limits[stock_key])//pack*pack
            amounts={max_quantity}
            if max_quantity>=pack:amounts.add(pack)
            if minimum and minimum<=max_quantity:amounts.add(minimum)
            for fraction in (Decimal('0.25'),Decimal('0.5'),Decimal('0.75')):
                amounts.add(int((Decimal(max_quantity)*fraction/pack).to_integral_value(rounding='ROUND_FLOOR'))*pack)
            for qty in sorted(amounts,reverse=True):
                if not qty:continue
                option=evaluate(item,route,qty,horizon,offset,basis,False)
                option.update(forecast_qty=qf,plan_qty=qp,pack_size=pack)
                options.append(option)
        options.sort(key=lambda o:(-o['result'],o['cash_required'],o['route_id'],o['quantity']))
        candidates.append(options)
        center=item.get('baseline_route_id')
        if not center or center not in route_map:baseline_complete=False
        restricted=[o for o in options if o['route_id'] in {'no_shipment',center}]
        if center and center not in eligible:baseline_complete=False
        baseline_candidates.append(restricted)
        if progress:
            elapsed=time.monotonic()-started;eta=elapsed/(idx+1)*(len(items)-idx-1)
            progress(f'ПРОГРЕСС: {idx+1}/{len(items)} ({100*(idx+1)/len(items):.0f}%) | варианты строк | кандидатов {sum(map(len,candidates))} | ETA {eta:.1f} с')
    best=search(candidates,items,route_map,limits,budget,max_nodes,progress)
    baseline=search(baseline_candidates,items,route_map,limits,budget,max_nodes,progress,'сравнение через центр') if baseline_complete else None
    if baseline and baseline['result']>best['result']:
        # The unrestricted bounded search can miss a feasible central combination.
        # A known feasible baseline is always part of the final candidate pool.
        best={**baseline,'search_truncated':True}
    output={**best,'kind':'estimate','search_status':'best_of_candidates',
        'horizon_days':horizon,'as_of':as_of.isoformat(),'ship_date':ship.isoformat(),'basis':basis,
        'baseline':baseline,'delta_center':None if baseline is None else best['result']-baseline['result'],
        'excluded_options':missing,'items':[i['key'] for i in items],
        'limitations':['Перебор конечного набора количеств, без доказательства глобального оптимума',
                      'Прирост спроса от локализации не заложен','Плановые потоки и деньги до налогов'],
        'input_kind':'manual_scenario' if any(i['input_kind']=='manual_scenario' for i in items) else 'observed_inputs'}
    if progress:progress(f"ИТОГ: {len(items)} строк | {best['quantity']} шт. | узлов {best['visited_nodes']} | результат {best['result']:.2f} ₽ | {time.monotonic()-started:.1f} с | готово; оптимальность не доказана")
    return output
