"""Dated replenishment scenarios and manager-owned supply register."""
from __future__ import annotations
from datetime import date, timedelta
import math
import uuid
from urllib.parse import parse_qs

DEFAULTS={'lead_days':7,'review_days':14,'safety_days':7,'pack_size':1,'horizon_days':56}
STATUSES={'draft','confirmed','shipped','received','cancelled'}


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_supply_policy (
          marketplace text PRIMARY KEY, lead_days integer NOT NULL DEFAULT 7,
          review_days integer NOT NULL DEFAULT 14, safety_days integer NOT NULL DEFAULT 7,
          pack_size integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_supply_orders (
          supply_id text PRIMARY KEY, marketplace text NOT NULL, sku text NOT NULL,
          warehouse text NOT NULL DEFAULT '', quantity numeric NOT NULL CHECK(quantity>0),
          eta date NOT NULL, status text NOT NULL, owner_name text NOT NULL DEFAULT '',
          note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS public.pulse_supply_events (
          event_id bigserial PRIMARY KEY, supply_id text NOT NULL, status text NOT NULL,
          quantity numeric NOT NULL, eta date NOT NULL, note text NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT now())""")


def read_register(conn,marketplace):
    # Report reads also run inside Health Check's read-only transaction.
    # Schema creation belongs to save(), not to a SELECT path.
    policy = dict(DEFAULTS)
    supplies = []
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.pulse_supply_policy') AS policy, to_regclass('public.pulse_supply_orders') AS orders")
        tables = cur.fetchone()
        if tables['policy']:
            cur.execute('SELECT * FROM public.pulse_supply_policy WHERE marketplace=%s',(marketplace,))
            policy.update(dict(cur.fetchone() or {}))
            policy.pop('updated_at',None)
        if tables['orders']:
            cur.execute("""SELECT supply_id,marketplace,sku,warehouse,quantity,eta,status,owner_name,note
              FROM public.pulse_supply_orders WHERE marketplace=%s ORDER BY eta,supply_id""",(marketplace,))
            supplies=[{**dict(row),'quantity':float(row['quantity']),'eta':str(row['eta'])} for row in cur.fetchall()]
    return policy,supplies


def save(config,payload):
    from km_trade_finance import connect_km
    market=str(payload.get('marketplace') or '')
    if market not in {'ozon','wb'}: raise ValueError('Укажите Ozon или WB')
    with connect_km(config) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if payload.get('action')=='policy':
                policy={key:int(payload.get(key,default)) for key,default in DEFAULTS.items() if key!='horizon_days'}
                if not (0<=policy['lead_days']<=180 and 1<=policy['review_days']<=180 and 0<=policy['safety_days']<=180 and 1<=policy['pack_size']<=10000):
                    raise ValueError('Проверьте сроки 0–180 дней и кратность 1–10000')
                cur.execute("""INSERT INTO public.pulse_supply_policy(marketplace,lead_days,review_days,safety_days,pack_size)
                  VALUES(%s,%s,%s,%s,%s) ON CONFLICT(marketplace) DO UPDATE SET
                  lead_days=EXCLUDED.lead_days,review_days=EXCLUDED.review_days,safety_days=EXCLUDED.safety_days,
                  pack_size=EXCLUDED.pack_size,updated_at=now()""",(market,policy['lead_days'],policy['review_days'],policy['safety_days'],policy['pack_size']))
                return {'ok':True,'policy':policy}
            sku=str(payload.get('sku') or '').strip(); qty=float(payload.get('quantity') or 0)
            eta=date.fromisoformat(str(payload.get('eta') or '')); status=str(payload.get('status') or 'draft')
            if not sku or not math.isfinite(qty) or not 0<qty<=10000000 or status not in STATUSES:
                raise ValueError('Проверьте SKU, количество и статус поставки')
            identifier=str(payload.get('supply_id') or uuid.uuid4())
            if len(identifier)>100 or len(sku)>150: raise ValueError('Слишком длинный идентификатор')
            cur.execute('SELECT marketplace,sku FROM public.pulse_supply_orders WHERE supply_id=%s FOR UPDATE',(identifier,))
            existing=cur.fetchone()
            if existing and (existing['marketplace']!=market or existing['sku']!=sku):
                raise ValueError('Нельзя менять площадку или SKU существующей поставки')
            note=str(payload.get('note') or '')[:1000]
            cur.execute("""INSERT INTO public.pulse_supply_orders
              (supply_id,marketplace,sku,warehouse,quantity,eta,status,owner_name,note)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(supply_id) DO UPDATE SET
              warehouse=EXCLUDED.warehouse,quantity=EXCLUDED.quantity,eta=EXCLUDED.eta,status=EXCLUDED.status,
              owner_name=EXCLUDED.owner_name,note=EXCLUDED.note,updated_at=now()""",
              (identifier,market,sku,str(payload.get('warehouse') or '')[:200],qty,eta,status,str(payload.get('owner_name') or '')[:150],note))
            cur.execute('INSERT INTO public.pulse_supply_events(supply_id,status,quantity,eta,note) VALUES(%s,%s,%s,%s,%s)',(identifier,status,qty,eta,note))
            return {'ok':True,'supply_id':identifier,'status':status}


def recommend(row,policy,supplies,as_of):
    daily=max(float(row.get('avg_daily_units') or 0),0)
    stock=max(float(row.get('current_qty') or 0),0)
    lead=int(policy['lead_days']); review=int(policy['review_days']); safety=int(policy['safety_days']); pack=int(policy['pack_size'])
    actual_snapshot=date.fromisoformat(row['snapshot_date']) if row.get('snapshot_date') else None
    quality='ready'
    if not row.get('current_snapshot_present') or not row.get('demand_window_days'): quality='missing_data'
    elif actual_snapshot and (as_of-actual_snapshot).days>2: quality='stale_snapshot'
    pending=[x for x in supplies if x['sku']==row['sku'] and x['status'] in {'confirmed','shipped'}]
    overdue=[x for x in pending if date.fromisoformat(x['eta'])<=as_of]
    arrivals={}
    for item in pending:
        offset=(date.fromisoformat(item['eta'])-as_of).days
        if offset>0: arrivals[offset]=arrivals.get(offset,0)+float(item['quantity'])
    horizon=lead+review
    incoming=sum(q for d,q in arrivals.items() if d<=horizon)
    needed=max(daily*(horizon+safety)-stock-incoming,0)
    qty=math.ceil(needed/pack)*pack if quality=='ready' and daily>0 else None
    available=stock; shortfall=0; risk_date=None; before_eta_gap=False; projection=[]
    for offset in range(0,max(56,horizon+safety)+1):
        if offset:
            available+=arrivals.get(offset,0)
            shortfall+=max(daily-available,0)
            available=max(available-daily,0)
        if risk_date is None and available<daily and daily>0:
            risk_date=(as_of+timedelta(days=offset)).isoformat()
        if offset<=lead and shortfall>0: before_eta_gap=True
        if offset<=56: projection.append({'date':str(as_of+timedelta(days=offset)),'stock':round(available,2),'shortfall':round(shortfall,2)})
    physical=stock/daily if daily else None
    deadline=as_of+timedelta(days=max(math.floor(physical)-lead-safety,0)) if physical is not None else None
    action='Проверить данные' if quality!='ready' else 'Нет спроса' if not daily else 'Ускорить поставку' if before_eta_gap else 'Пополнить' if qty else 'Достаточно'
    if overdue: action='Проверить просроченный приход'
    return {'physical_days_cover':round(physical,1) if physical is not None and quality!='missing_data' else None,
        'depletion_date':risk_date if quality=='ready' else None,'ship_by':str(deadline) if deadline and quality=='ready' else None,
        'recommended_supply_qty':qty,'confirmed_inbound_qty':sum(float(x['quantity']) for x in pending),
        'next_eta':min((x['eta'] for x in pending if date.fromisoformat(x['eta'])>as_of),default=None),
        'supply_quality':quality,'supply_action':action,'gap_before_arrival':before_eta_gap if quality=='ready' else False,
        'overdue_supplies':len(overdue),'projection':projection if quality=='ready' else [],
        'recommendation_reason':f'{daily:g} шт/день × ({lead} дней до продажи + {review} цикл + {safety} страховой запас) − {stock:g} доступно − {incoming:g} подтверждено в горизонте; кратность {pack}. Неподтверждённый транзит API исключён.'}


def enrich(payload,parsed,get_conn):
    market=payload['marketplace']
    if market not in {'ozon','wb'}: return payload
    with get_conn() as conn:
        policy,supplies=read_register(conn,market)
        with conn.cursor() as cur:
            cur.execute("""SELECT sku,warehouse_name,cluster_name,sum(stock_available_qty) AS qty
              FROM public.inventory_history_daily WHERE marketplace=%s AND snapshot_date=%s
              GROUP BY sku,warehouse_name,cluster_name""",(market,payload.get('summary',{}).get('snapshot_date') or date.today()))
            warehouses={}
            for row in cur.fetchall(): warehouses.setdefault(row['sku'],[]).append({'warehouse':row['warehouse_name'],'cluster':row['cluster_name'],'quantity':float(row['qty']) if row['qty'] is not None else None})
    snapshot=payload.get('summary',{}).get('snapshot_date')
    as_of=date.today()
    for row in payload['rows']:
        model=recommend(row,policy,supplies,as_of)
        # Projection is retrieved only for a selected product to keep large catalogs compact.
        requested=(parse_qs(parsed.query).get('supply_sku') or [''])[0]
        if requested!=row['sku']: model.pop('projection',None)
        row.update(model,warehouse_breakdown=warehouses.get(row['sku'],[]))
    payload['supply_planning']={'policy':policy,'supplies':supplies,'as_of':str(as_of),
        'snapshot_date':snapshot,'historical_view':bool(snapshot and snapshot!=str(as_of)),
        'notice':'Потребность по SKU без распределения спроса между складами. ETA — дата доступности к продаже. Только подтверждённые поставки реестра учитываются в прогнозе; отгрузка на площадку автоматически не создаётся.',
        'recommended_total':sum(x['recommended_supply_qty'] or 0 for x in payload['rows']),
        'risk_sku_count':sum(x['gap_before_arrival'] for x in payload['rows']),
        'requires_check_count':sum(x['supply_quality']!='ready' for x in payload['rows'])}
    payload['client']=(parse_qs(parsed.query).get('client') or [''])[0]
    return payload
