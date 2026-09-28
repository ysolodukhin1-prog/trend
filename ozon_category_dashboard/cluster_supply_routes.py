"""Versioned internal route conditions. No marketplace requests or shipments."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import uuid
from psycopg2.extras import Json
from km_trade_finance import connect_km

SCHEMES={'wb':{'FBW','FBS','DBS'},'ozon':{'FBO','FBS','rFBS'},
         'yandex_market':{'FBY','FBS','EXPRESS','DBS'}}
COST_FIELDS=('freight_fixed','freight_per_unit','intake_per_unit','service_per_buyout',
             'storage_per_unit_day','capital_annual_pct')


def decimal_value(value, name, *, nullable=False, negative=False):
    if value is None or value=='':
        if nullable:return None
        raise ValueError(f'{name}: укажите значение')
    if isinstance(value,bool): raise ValueError(f'{name}: требуется число')
    try: result=Decimal(str(value))
    except (InvalidOperation,ValueError): raise ValueError(f'{name}: требуется число') from None
    if not result.is_finite() or (result<0 and not negative) or abs(result)>Decimal('1000000000'):
        raise ValueError(f'{name}: недопустимое значение')
    return result


def integer(value,name,minimum=0,maximum=10000000):
    number=decimal_value(value,name)
    if number!=number.to_integral_value() or not minimum<=number<=maximum:
        raise ValueError(f'{name}: требуется целое число от {minimum} до {maximum}')
    return int(number)


def text_value(value,name,required=True,maximum=200):
    text=str(value or '').strip()
    if (required and not text) or len(text)>maximum:raise ValueError(f'{name}: проверьте значение')
    return text


def validate_route(raw):
    if not isinstance(raw,dict):raise ValueError('Некорректные условия маршрута')
    market=raw.get('marketplace'); scheme=raw.get('scheme')
    if market not in SCHEMES or scheme not in SCHEMES[market]:
        raise ValueError('Укажите совместимые площадку и модель работы')
    route={k:text_value(raw.get(k),k) for k in ('name','origin','destination','cluster','source','owner')}
    route.update(marketplace=market,scheme=scheme,currency='RUB',
        tax_basis=text_value(raw.get('tax_basis'),'Налоговый базис'),
        handover=text_value(raw.get('handover'),'Точка передачи',False),
        freight_basis=raw.get('freight_basis','units'))
    if route['freight_basis'] not in {'units','weight_kg','volume_m3'}:
        raise ValueError('База переменной доставки: единицы, кг или м³')
    if raw.get('currency','RUB')!='RUB':raise ValueError('Пока поддерживаются ставки в RUB')
    for key in COST_FIELDS:
        value=decimal_value(raw.get(key),key,nullable=True)
        route[key]=str(value) if value is not None else None
    for key in ('capacity_units','capacity_kg','capacity_m3'):
        value=decimal_value(raw.get(key),key,nullable=True)
        if key=='capacity_units' and value is not None: integer(value,key)
        route[key]=str(value) if value is not None else None
    for key,default,minimum,maximum in [('lead_days',7,0,180),('pack_size',1,1,10000),('min_units',0,0,1000000)]:
        route[key]=integer(raw.get(key,default),key,minimum,maximum)
    for key in ('valid_from','valid_to','checked_on'):
        try:route[key]=date.fromisoformat(str(raw.get(key,''))).isoformat()
        except ValueError:raise ValueError(f'{key}: укажите корректную дату') from None
    if route['valid_from']>route['valid_to']:raise ValueError('Срок тарифа задан в обратном порядке')
    if route['checked_on']>date.today().isoformat():raise ValueError('Дата проверки не может быть будущей')
    route['available']=raw.get('available',True)
    route['confirmed']=raw.get('confirmed',False)
    if not isinstance(route['available'],bool) or not isinstance(route['confirmed'],bool):
        raise ValueError('Признаки доступности и подтверждения должны быть логическими')
    # Bands are inclusive upper bounds, ascending; tariff is the total variable
    # cost at that load. fixed is charged once in addition, never once per SKU.
    bands=raw.get('freight_bands') or []
    if not isinstance(bands,list) or len(bands)>40:raise ValueError('Не более 40 тарифных ступеней')
    route['freight_bands']=[]; previous=Decimal('-1')
    for band in bands:
        upper=decimal_value(band.get('up_to'),'Верхняя граница ступени')
        cost=decimal_value(band.get('cost'),'Стоимость ступени')
        if upper<=previous:raise ValueError('Границы ступеней должны строго возрастать')
        route['freight_bands'].append({'up_to':str(upper),'cost':str(cost)});previous=upper
    regions=raw.get('buyer_regions') or []
    if not isinstance(regions,list) or len(regions)>200:raise ValueError('Проверьте список регионов')
    route['buyer_regions']=list(dict.fromkeys(text_value(r,'Регион') for r in regions))
    return route


def readiness(route, ship_date):
    day=date.fromisoformat(str(ship_date)).isoformat()
    missing=[key for key in COST_FIELDS if route.get(key) is None and not (key=='freight_per_unit' and route.get('freight_bands'))]
    if not route.get('available'):missing.append('Маршрут недоступен')
    if not route.get('confirmed'):missing.append('Условия не подтверждены владельцем')
    if not route['valid_from']<=day<=route['valid_to']:missing.append('Тариф не действует на дату отгрузки')
    return {'status':'blocked' if missing else 'ready','missing':missing}


def freight_cost(route, *, units, weight_kg, volume_m3):
    fixed=decimal_value(route.get('freight_fixed'),'Фиксированная доставка')
    load=decimal_value({'units':units,'weight_kg':weight_kg,'volume_m3':volume_m3}[route['freight_basis']],'Груз')
    if not units:return Decimal(0)
    if route.get('freight_bands'):
        for band in route['freight_bands']:
            if load<=Decimal(band['up_to']):return fixed+Decimal(band['cost'])
        raise ValueError('Груз превышает последнюю тарифную ступень')
    rate=decimal_value(route.get('freight_per_unit'),'Переменная доставка')
    return fixed+rate*load


DDL='''CREATE TABLE IF NOT EXISTS public.cluster_supply_route_versions (
    client text NOT NULL,route_id text NOT NULL,revision integer NOT NULL,
    marketplace text NOT NULL,payload jsonb NOT NULL,actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(client,route_id,revision))'''


def read_routes(config,client,market=None):
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        cur.execute('SELECT to_regclass(%s) AS relation',('public.cluster_supply_route_versions',))
        if not cur.fetchone()['relation']:return []
        cur.execute('''SELECT DISTINCT ON(route_id) route_id,revision,payload,actor,created_at
            FROM cluster_supply_route_versions WHERE client=%s
            ORDER BY route_id,revision DESC''',(client,))
        rows=[{**r['payload'],'route_id':r['route_id'],'revision':r['revision'],
               'actor':r['actor'],'saved_at':r['created_at'].isoformat()} for r in cur.fetchall()]
        return [r for r in rows if not market or r['marketplace']==market]


def save_route(config,client,payload,actor):
    route=validate_route(payload)
    route_id=text_value(payload.get('route_id') or str(uuid.uuid4()),'ID маршрута',maximum=100)
    expected=integer(payload.get('expected_revision',0),'Версия',maximum=1000000)
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))',(client,'route:'+route_id))
        cur.execute('SELECT max(revision) AS revision FROM cluster_supply_route_versions WHERE client=%s AND route_id=%s',(client,route_id))
        latest=cur.fetchone()['revision'] or 0
        if latest!=expected:raise ValueError('Условия уже изменены: загрузите последнюю версию')
        cur.execute('''INSERT INTO cluster_supply_route_versions(client,route_id,revision,marketplace,payload,actor)
            VALUES(%s,%s,%s,%s,%s,%s)''',(client,route_id,latest+1,route['marketplace'],Json(route),text_value(actor,'Автор')))
    return {**route,'route_id':route_id,'revision':latest+1}
