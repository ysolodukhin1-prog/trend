"""Deterministic daily stock/retained-sales simulation for explicit scenarios."""
from collections import defaultdict
from decimal import Decimal
from cluster_supply_routes import decimal_value, integer


def simulate(*, daily_orders, initial_stock, arrivals, buyout_probability,
             restock_probability, restock_lag_days):
    """Orders consume stock; non-retained units return after an explicit lag.

    Probabilities describe eventual retained sales and resalable returns, not
    observed same-day revenue. Expected flows may be fractional; shipments may not.
    Returns beyond the horizon remain in transit and are not free available stock.
    """
    if not isinstance(daily_orders,list) or not 1<=len(daily_orders)<=180:
        raise ValueError('Дневной спрос: от 1 до 180 дней')
    stock=decimal_value(initial_stock,'Начальный остаток')
    demand=[decimal_value(x,'Дневной спрос') for x in daily_orders]
    buyout=decimal_value(buyout_probability,'Доля выкупа')
    restock=decimal_value(restock_probability,'Доля возврата в продажу')
    if buyout>1 or restock>1:raise ValueError('Вероятности от 0 до 1')
    lag=integer(restock_lag_days,'Лаг возврата',1,180)
    planned=defaultdict(Decimal); returns=defaultdict(Decimal)
    pool_arrivals=defaultdict(Decimal);pool_returns=defaultdict(Decimal)
    pools={'existing':stock,'new':Decimal(0)};kept_by_pool=defaultdict(Decimal)
    stock_days_by_pool=defaultdict(Decimal);return_unit_days=Decimal(0);return_days_by_pool=defaultdict(Decimal)
    for a in arrivals:
        day=integer(a.get('day'),'День прибытия',0,365)
        qty=integer(a.get('quantity'),'Количество прибытия')
        if a.get('status') in {'confirmed','shipped'}:
            planned[day]+=qty
            pool_arrivals[(day,'new' if a.get('pool')=='new' else 'existing')]+=qty
    retained=fulfilled=lost=stock_days=nonretained=writeoff=Decimal(0)
    trace=[]
    for day,wanted in enumerate(demand):
        for pool in pools:pools[pool]+=pool_arrivals[(day,pool)]+pool_returns[(day,pool)]
        stock=sum(pools.values(),Decimal(0))
        served=min(stock,wanted); stock-=served
        remaining=served
        for pool in pools:
            allocated=min(pools[pool],remaining);pools[pool]-=allocated;remaining-=allocated
            kept_by_pool[pool]+=allocated*buyout
            pool_returns[(day+lag,pool)]+=allocated*(1-buyout)*restock
        kept=served*buyout; rejected=served-kept
        returns[day+lag]+=rejected*restock
        retained+=kept; nonretained+=rejected; writeoff+=rejected*(1-restock)
        fulfilled+=served; lost+=wanted-served; stock_days+=stock
        for pool in pools:stock_days_by_pool[pool]+=pools[pool]
        for (d,pool),qty in pool_returns.items():
            if d>day:return_unit_days+=qty;return_days_by_pool[pool]+=qty
        trace.append({'day':day,'orders':wanted,'fulfilled':served,'retained':kept,
            'lost':wanted-served,'arrivals':planned[day],'restocked':returns[day],'stock':stock})
    return {'orders':fulfilled,'retained_units':retained,'lost_orders':lost,
        'nonretained_units':nonretained,'writeoff_units':writeoff,'end_stock':stock,
        'return_transit':sum((q for d,q in returns.items() if d>=len(demand)),Decimal(0)),
        'confirmed_transit':sum((q for d,q in planned.items() if d>=len(demand)),Decimal(0)),
        'stock_days':stock_days,'stock_days_by_pool':dict(stock_days_by_pool),
        'return_unit_days':return_unit_days,'return_days_by_pool':dict(return_days_by_pool),
        'retained_by_pool':dict(kept_by_pool),'daily':trace}


def required_quantity(daily_orders, initial_stock, arrivals, lead_days, target_end_stock=0, pack_size=1):
    """Daily gross-order need after arrival; pre-arrival shortage is not backlog.

    Conservative sizing ignores uncertain resale returns, which are explicit
    scenarios in simulation. No-plan callers can compute forecast independently.
    """
    pack=integer(pack_size,'Кратность',1,100000000); lead=integer(lead_days,'Срок',0,365)
    stock=decimal_value(initial_stock,'Остаток'); reserve=decimal_value(target_end_stock,'Резерв')
    schedule=defaultdict(Decimal)
    for a in arrivals:
        if a.get('status') in {'confirmed','shipped'}:
            schedule[integer(a.get('day'),'День',0,365)]+=integer(a.get('quantity'),'Количество')
    required=Decimal(0)
    for day,value in enumerate(daily_orders):
        stock+=schedule[day]; wanted=decimal_value(value,'Спрос')
        if day<lead:stock=max(stock-wanted,Decimal(0))
        else:
            gap=max(wanted-stock,Decimal(0));required+=gap;stock=max(stock-wanted,Decimal(0))
    if lead>=len(daily_orders):return 0
    required+=max(reserve-stock,Decimal(0))
    return int((required/pack).to_integral_value(rounding='ROUND_CEILING'))*pack
