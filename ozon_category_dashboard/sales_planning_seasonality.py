"""Seasonality from complete calendar years, with explicit source provenance."""
from collections import defaultdict
import math

def seasonal_indices(observations, years=(2023, 2024, 2025)):
    by_year=defaultdict(dict)
    for row in observations:
        date=str(row.get('date',''))
        if len(date)<10 or date[8:10]!='01':continue
        year,month=int(date[:4]),int(date[5:7])
        if year not in years or not 1<=month<=12:continue
        value=row.get('sales')
        if value is None or not math.isfinite(float(value)) or float(value)<0:continue
        if month in by_year[year]:raise ValueError('Duplicate month in source')
        by_year[year][month]=float(value)
    complete={y:values for y,values in by_year.items() if len(values)==12 and sum(values.values())>0}
    if set(complete)!=set(years):return [],'incomplete_history'
    indices={m:sum(values[m]/(sum(values.values())/12) for values in complete.values())/len(complete) for m in range(1,13)}
    if any(v<=0 for v in indices.values()):return [],'zero_seasonal_base'
    return [{'month':m,'seasonality_index':indices[m],'transition_coefficient':indices[m]/indices[12 if m==1 else m-1], 'training_observations':len(complete)} for m in range(1,13)],'research_estimate'
