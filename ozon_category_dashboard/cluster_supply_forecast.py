"""Transparent forecast baseline with explicit coverage and time-cutoff backtest."""
from datetime import date,timedelta
from decimal import Decimal
from statistics import mean
from cluster_supply_routes import decimal_value,integer


def forecast(observations,as_of,horizon_days=30,*,coverage_complete=False):
    as_of=date.fromisoformat(str(as_of));horizon=integer(horizon_days,'Горизонт',1,180)
    clean={}
    for row in observations:
        day=date.fromisoformat(str(row['date']))
        if day>=as_of:continue  # future and current open day cannot leak into training
        if day in clean:raise ValueError('Дублируется день спроса')
        clean[day]=None if row.get('orders') is None else decimal_value(row['orders'],'Заказы')
    wanted=[as_of-timedelta(days=i) for i in range(30,0,-1)]
    values=[clean.get(day) for day in wanted]
    observed=sum(v is not None for v in values)
    if not coverage_complete or observed!=30:
        return {'status':'blocked','reason':'Для прогноза нужны 30 полных закрытых дней',
            'observed_days':observed,'required_days':30,'daily':None,'method':'mean_30d'}
    average=sum(values,Decimal(0))/30
    # Conservative, inspectable first baseline. Bounds are sensitivities, not
    # calibrated prediction quantiles; seasonal models require stronger history.
    return {'status':'estimate','method':'mean_30d','observed_days':30,'required_days':30,
        'training_from':wanted[0].isoformat(),'training_to':wanted[-1].isoformat(),
        'daily':[{'date':(as_of+timedelta(days=i)).isoformat(),'orders':float(average)} for i in range(horizon)],
        'sensitivity':{'kind':'scenario_bounds','low_multiplier':0.8,'high_multiplier':1.2},
        'localization_uplift':0}


def backtest(observations,cutoffs,horizon_days=7):
    """Evaluate closed holdouts only; missing truth is not scored as zero."""
    rows={str(r['date']):r.get('orders') for r in observations};result=[]
    for cutoff in cutoffs:
        prediction=forecast(observations,cutoff,horizon_days,coverage_complete=True)
        if prediction['daily'] is None:continue
        pairs=[]
        for row in prediction['daily']:
            actual=rows.get(row['date'])
            if actual is None:break
            pairs.append((float(decimal_value(actual,'Факт')),row['orders']))
        if len(pairs)!=horizon_days:continue
        error=sum(abs(actual-estimate) for actual,estimate in pairs)
        total=sum(actual for actual,_ in pairs)
        result.append({'cutoff':str(cutoff),'days':len(pairs),'mae':error/len(pairs),
            'bias':sum(estimate-actual for actual,estimate in pairs)/len(pairs),
            'wape':error/total if total>0 else None,'actual_units':total})
    return {'method':'mean_30d','windows':result,'windows_scored':len(result),
        'notice':'Оффлайн ошибка прогноза; не доказательство эффекта локализации'}
