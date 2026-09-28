"""Immutable internal plans: exact input/route versions, replay and idempotency."""
from contextlib import closing
from datetime import date,datetime
from decimal import Decimal
import hashlib,json,uuid
from psycopg2.extras import Json
from km_trade_finance import connect_km
from cluster_supply_routes import read_routes,text_value
from cluster_supply_optimizer import optimize
from cluster_supply_model import compare_item

ITEM_KEYS={'key','sku','size','product_name','inventory_id','marketplace','scheme','cluster','initial_stock',
 'opening_stock_key','forecast_daily','plan_daily','pack_size','target_end_stock','price','cogs',
 'common_cost_per_buyout','advertising_per_order','return_cost_per_order','current_service_per_buyout',
 'current_storage_per_unit_day','capital_annual_pct','buyout_probability','restock_probability',
 'restock_lag_days','unit_weight_kg','unit_volume_m3','arrivals','evidence','baseline_route_id','tax_basis'}


def jsonable(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,dict):return {k:jsonable(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [jsonable(v) for v in value]
    return value


def sanitize_input(raw):
    keys=('as_of','ship_date','horizon_days','basis','budget','stock_limits','opening_stock_limits','max_nodes')
    clean={k:raw[k] for k in keys if k in raw}
    clean['items']=[]
    if not isinstance(raw.get('items'),list):raise ValueError('Укажите строки сценария')
    for item in raw['items']:
        if not isinstance(item,dict):raise ValueError('Некорректная строка сценария')
        row={k:v for k,v in item.items() if k in ITEM_KEYS}
        # Public API entries are user-owned scenarios. Only a future trusted
        # source assembler may label validated DB observations as observed.
        row['evidence']={}
        for key in ('demand','stock','finance'):
            source=(item.get('evidence') or {}).get(key) or {}
            row['evidence'][key]={'kind':'manual','ref':text_value(source.get('ref'),f'Источник {key}',maximum=400),
                'as_of':source.get('as_of'),'complete':source.get('complete') is True}
        row['arrivals']=[{k:a.get(k) for k in ('id','day','quantity','status')} for a in item.get('arrivals') or []]
        clean['items'].append(row)
    return clean


def calculate(config,client,payload,progress=print):
    clean=sanitize_input(payload)
    routes=read_routes(config,client)
    chosen=payload.get('route_ids')
    if not isinstance(chosen,list) or not chosen or len(chosen)>100 or not all(isinstance(rid,str) for rid in chosen) or len(set(chosen))!=len(chosen):
        raise ValueError('Выберите от 1 до 100 уникальных маршрутов')
    known={r['route_id']:r for r in routes}
    if any(rid not in known for rid in chosen):raise ValueError('Маршрут не найден в выбранном клиенте')
    routes=[known[rid] for rid in sorted(chosen)]
    result=optimize(clean,routes,progress)
    comparisons={}
    for item in clean['items']:
        comparisons[item['key']]=compare_item(item,routes,as_of=clean['as_of'],ship_date=clean['ship_date'],
            horizon_days=clean.get('horizon_days',30),basis=clean.get('basis','forecast'),
            baseline_route_id=item.get('baseline_route_id'),stock_limits=clean.get('stock_limits',{}))
    snapshot=jsonable({'client':client,'input':clean,'routes':routes,'model_version':'cluster_supply_v1_candidates'})
    fingerprint=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'ok':True,'fingerprint':fingerprint,'snapshot':snapshot,'result':jsonable(result),'comparisons':jsonable(comparisons)}


DDL='''CREATE TABLE IF NOT EXISTS public.cluster_supply_plan_versions (
 client text NOT NULL,plan_id text NOT NULL,revision integer NOT NULL DEFAULT 1,
 idempotency_key text NOT NULL,fingerprint text NOT NULL,payload jsonb NOT NULL,
 actor text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(client,plan_id,revision),UNIQUE(client,idempotency_key))'''


def save_plan(config,client,payload,actor):
    idempotency=text_value(payload.get('idempotency_key'),'Ключ сохранения',maximum=100)
    # Replay a completed save before reading newer tariffs. A transport retry
    # must retain the old accepted snapshot instead of creating a different plan.
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        cur.execute('SELECT to_regclass(%s) AS relation',('public.cluster_supply_plan_versions',))
        if cur.fetchone()['relation']:
            cur.execute('SELECT plan_id,revision,fingerprint,payload FROM cluster_supply_plan_versions WHERE client=%s AND idempotency_key=%s',(client,idempotency))
            saved=cur.fetchone()
            if saved:
                old=saved['payload']['snapshot']
                same=jsonable(sanitize_input(payload))==old['input'] and sorted(payload.get('route_ids') or [])==sorted(r['route_id'] for r in old['routes'])
                if not same or payload.get('expected_fingerprint')!=saved['fingerprint']:
                    raise ValueError('Ключ сохранения уже использован для другого сценария')
                return {'ok':True,'plan_id':saved['plan_id'],'revision':saved['revision'],'replayed':True}
    calculated=calculate(config,client,payload)
    if payload.get('expected_fingerprint')!=calculated['fingerprint']:
        raise ValueError('Сценарий или тарифы изменились: пересчитайте перед сохранением')
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))',(client,'plan:'+idempotency))
        cur.execute('SELECT plan_id,revision,fingerprint FROM cluster_supply_plan_versions WHERE client=%s AND idempotency_key=%s',(client,idempotency))
        existing=cur.fetchone()
        if existing:
            if existing['fingerprint']!=calculated['fingerprint']:raise ValueError('Ключ сохранения уже использован для другого сценария')
            return {'ok':True,'plan_id':existing['plan_id'],'revision':existing['revision'],'replayed':True}
        for route in calculated['snapshot']['routes']:
            rid=route['route_id']
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))',(client,'route:'+rid))
            cur.execute('SELECT max(revision) AS revision FROM cluster_supply_route_versions WHERE client=%s AND route_id=%s',(client,rid))
            if cur.fetchone()['revision']!=route['revision']:raise ValueError('Тариф изменился во время расчёта: повторите пересчёт')
        identifier=str(uuid.uuid4())
        cur.execute('''INSERT INTO cluster_supply_plan_versions(client,plan_id,idempotency_key,fingerprint,payload,actor)
            VALUES(%s,%s,%s,%s,%s,%s)''',(client,identifier,idempotency,calculated['fingerprint'],Json(calculated),text_value(actor,'Автор')))
    return {'ok':True,'plan_id':identifier,'revision':1,'replayed':False}


def read_plans(config,client,plan_id=None):
    with closing(connect_km(config)) as conn,conn,conn.cursor() as cur:
        cur.execute('SELECT to_regclass(%s) AS relation',('public.cluster_supply_plan_versions',))
        if not cur.fetchone()['relation']:return None if plan_id else []
        if plan_id:
            cur.execute('''SELECT plan_id,revision,payload,actor,created_at FROM cluster_supply_plan_versions
                WHERE client=%s AND plan_id=%s ORDER BY revision DESC LIMIT 1''',(client,plan_id))
            r=cur.fetchone()
            return None if not r else jsonable(dict(r))
        cur.execute('''SELECT plan_id,revision,actor,created_at,fingerprint,
            payload#>>'{result,quantity}' AS quantity,payload#>>'{result,result}' AS result_before_tax
            FROM cluster_supply_plan_versions WHERE client=%s ORDER BY created_at DESC LIMIT 100''',(client,))
        return jsonable([dict(r) for r in cur.fetchall()])
