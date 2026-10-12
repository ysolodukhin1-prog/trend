"""Lamoda v2 unit-level order details. Amounts in stored tables are kopecks.

GET /orders returns headers; GET /orders/{id} returns unitloads in items.
Use the list's id (not grouped orderId). Never infer buyout from paidPrice.
"""
from __future__ import annotations
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from psycopg2.extras import Json, execute_values

DETAIL_PATH = re.compile(r"/orders/[A-Za-z0-9_-]{1,150}\Z")
HEADER_FIELDS = ('id','orderId','status','createdAt','updatedAt','fullSum','deliveryPrice','isConfirmed','paymentMethod')
ITEM_FIELDS = ('id','sku','externalSku','description','size','status','basePrice','salePrice','paidPrice',
               'sellerAgreedPrice','couponDiscount','loyaltyDiscount','sellerAgreedDiscount','otherDiscounts','totalDiscount','platformDiscounts')

def timestamp(value):
    if not value:
        raise ValueError('Missing order timestamp')
    value = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def money(value):
    if not isinstance(value,dict) or value.get('amount') is None:
        raise ValueError('Missing money amount')
    amount=Decimal(str(value['amount']))
    if not amount.is_finite() or amount != amount.to_integral_value() or not value.get('currency'):
        raise ValueError('Invalid minor-unit money')
    return amount,str(value['currency'])

def normalize_detail(payload, order_id):
    if not isinstance(payload,dict) or str(payload.get('id','')) != order_id:
        raise ValueError('Order identity mismatch')
    if not isinstance(payload.get('items'),list) or not payload['items']:
        raise ValueError('Missing or empty order items')
    head={k:payload[k] for k in HEADER_FIELDS if k in payload}
    timestamp(head.get('createdAt')); timestamp(head.get('updatedAt')); money(head.get('fullSum'))
    items=[]; seen=set()
    for raw in payload['items']:
        if not isinstance(raw,dict) or not raw.get('id') or not raw.get('sku') or not raw.get('status'):
            raise ValueError('Incomplete order item')
        item={k:raw[k] for k in ITEM_FIELDS if k in raw}
        identity=str(item['id'])
        if identity in seen:
            raise ValueError('Duplicate unitload in response')
        seen.add(identity)
        money(item.get('paidPrice'))
        items.append(item)
    # Intentionally exclude address, customer, comment and Data Matrix codes.
    head['items']=items
    return head

def ensure_detail_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.lamoda_orders (
          order_key text PRIMARY KEY,order_id text,external_order_id text,status text,
          created_at timestamptz,updated_at timestamptz,payload jsonb NOT NULL,
          synced_at timestamptz NOT NULL DEFAULT now());
        CREATE TABLE IF NOT EXISTS public.lamoda_order_items (
          order_key text NOT NULL REFERENCES public.lamoda_orders(order_key) ON DELETE CASCADE,
          item_key text NOT NULL,seller_sku text,lamoda_sku text,status text,
          quantity numeric,amount numeric,currency text,payload jsonb NOT NULL,
          synced_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(order_key,item_key));
        CREATE TABLE IF NOT EXISTS public.lamoda_order_detail_state (
          order_key text PRIMARY KEY REFERENCES public.lamoda_orders(order_key) ON DELETE CASCADE,
          account_id uuid NOT NULL,fulfillment text NOT NULL CHECK(fulfillment IN ('FBO','FBS')),
          source_updated_at timestamptz NOT NULL,item_keys jsonb NOT NULL,
          items_count integer NOT NULL CHECK(items_count>0),synced_at timestamptz NOT NULL DEFAULT now());
        CREATE INDEX IF NOT EXISTS idx_lamoda_details_account ON public.lamoda_order_detail_state(account_id);
        CREATE OR REPLACE VIEW public.v_lamoda_order_items_v2 AS
          SELECT s.account_id,s.fulfillment,o.order_key,o.order_id,o.external_order_id,
            o.created_at,o.created_at::date AS order_date,o.updated_at,o.status AS order_status,
            i.item_key,i.seller_sku,i.lamoda_sku,i.payload->>'description' AS product_name,
            i.payload->>'size' AS size,i.status AS item_status,i.quantity,
            i.amount AS paid_price_minor,i.amount/100 AS paid_price_rub,i.currency,
            (i.payload#>>'{basePrice,amount}')::numeric/100 AS base_price_rub,
            (i.payload#>>'{salePrice,amount}')::numeric/100 AS sale_price_rub,
            (o.payload#>>'{fullSum,amount}')::numeric/100 AS header_amount_rub,
            i.synced_at
          FROM public.lamoda_orders o JOIN public.lamoda_order_detail_state s USING(order_key)
          JOIN public.lamoda_order_items i ON i.order_key=o.order_key AND s.item_keys ? i.item_key;
        COMMENT ON VIEW public.v_lamoda_order_items_v2 IS
          'Seller API v2 current unit statuses by order creation date; not historical buyout events. Money in RUB is divided by 100. paidPrice is not proof of buyout.';
        """)
    conn.commit()

def store_detail(conn,payload,account_id,fulfillment):
    account_id=str(UUID(str(account_id)))
    if fulfillment not in {'FBO','FBS'}:
        raise ValueError('Explicit account fulfillment required')
    oid=str(payload.get('id',''))
    if not DETAIL_PATH.fullmatch('/orders/'+oid):
        raise ValueError('Invalid order id')
    payload=normalize_detail(payload,oid)
    key=account_id+':'+oid; updated=timestamp(payload['updatedAt'])
    items=payload['items']
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        cur.execute('SELECT source_updated_at FROM public.lamoda_order_detail_state WHERE order_key=%s',(key,))
        current=cur.fetchone()
        if current and current[0]>updated:
            return 0
        cur.execute("""INSERT INTO public.lamoda_orders
          (order_key,order_id,external_order_id,status,created_at,updated_at,payload)
          VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(order_key) DO UPDATE SET
          order_id=excluded.order_id,external_order_id=excluded.external_order_id,
          status=excluded.status,created_at=excluded.created_at,updated_at=excluded.updated_at,
          payload=excluded.payload,synced_at=now()""",
          (key,oid,payload.get('orderId'),payload.get('status'),timestamp(payload['createdAt']),updated,Json(payload)))
        values=[]
        for item in items:
            amount,currency=money(item['paidPrice'])
            # Each items[].id is one unitload, including repeated SKUs in the same order.
            values.append((key,str(item['id']),item.get('externalSku'),item['sku'],item['status'],1,amount,currency,Json(item)))
        execute_values(cur,"""INSERT INTO public.lamoda_order_items
          (order_key,item_key,seller_sku,lamoda_sku,status,quantity,amount,currency,payload)
          VALUES %s ON CONFLICT(order_key,item_key) DO UPDATE SET
          seller_sku=excluded.seller_sku,lamoda_sku=excluded.lamoda_sku,status=excluded.status,
          quantity=excluded.quantity,amount=excluded.amount,currency=excluded.currency,
          payload=excluded.payload,synced_at=now()""",values)
        cur.execute("""INSERT INTO public.lamoda_order_detail_state
          (order_key,account_id,fulfillment,source_updated_at,item_keys,items_count)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(order_key) DO UPDATE SET
          account_id=excluded.account_id,fulfillment=excluded.fulfillment,
          source_updated_at=excluded.source_updated_at,item_keys=excluded.item_keys,
          items_count=excluded.items_count,synced_at=now()""",
          (key,account_id,fulfillment,updated,Json([str(i['id']) for i in items]),len(items)))
    return len(items)

def cached_headers(conn,account_id,date_from,date_to):
    # Latest raw snapshot, not a possibly stale materialized view. Creation-date cohort.
    with conn.cursor() as cur:
        cur.execute("""SELECT payload FROM (SELECT DISTINCT ON(record_key) payload
          FROM public.lamoda_v2_entities WHERE dataset='orders' AND account_id=%s
          AND (payload->>'createdAt')::timestamptz::date BETWEEN %s AND %s
          ORDER BY record_key,synced_at DESC,snapshot_date DESC) q
          ORDER BY payload->>'createdAt' """,(account_id,date_from,date_to))
        return [r[0] for r in cur.fetchall()]

def sync_details(conn,api,rows,account_id,fulfillment,workers=4,progress=None):
    """Resumable ordered batches; main thread writes DB, each HTTP worker has its own token.

    At most 4 in-flight GETs/account and 10 starts/s; retries happen in LamodaClient.
    Failed orders are not checkpointed and are retried on the next run.
    """
    rows=list({str(r['id']):r for r in rows}.values())
    with conn.cursor() as cur:
        cur.execute("""SELECT s.order_key,s.source_updated_at,s.items_count,
          (SELECT count(*) FROM public.lamoda_order_items i WHERE i.order_key=s.order_key AND s.item_keys ? i.item_key)
          FROM public.lamoda_order_detail_state s WHERE account_id=%s""",(account_id,))
        done={r[0]:(r[1],r[2]==r[3]) for r in cur.fetchall()}
    pending=[]
    for r in rows:
        existing=done.get(str(account_id)+':'+str(r['id']))
        if not existing or not existing[1] or not r.get('updatedAt') or existing[0]<timestamp(r['updatedAt']):
            pending.append(r)
    conn.commit()
    result={'orders_total':len(rows),'orders_skipped':len(rows)-len(pending),'orders_loaded':0,'items_loaded':0,'errors':0,'detail_requests':0}
    print('DETAIL_START: '+json.dumps(result),flush=True)
    local=threading.local(); throttle=threading.Lock(); clock=[0.0]
    clients=[]; clients_lock=threading.Lock()
    def fetch(row):
        oid=str(row['id']); path='/orders/'+oid
        if not DETAIL_PATH.fullmatch(path):
            return oid,None,'InvalidOrderId'
        try:
            if not hasattr(local,'api'):
                local.api=type(api)(api.client_id,api.client_secret,api.seller_id)
                local.api.token=api.token
                with clients_lock: clients.append(local.api)
            with throttle:
                pause=max(0.0,clock[0]-time.monotonic())
                if pause: time.sleep(pause)
                clock[0]=time.monotonic()+0.1
            payload=normalize_detail(local.api.get(path,{'sellerId':api.seller_id}),oid)
            return oid,payload,None
        except Exception as exc:
            return oid,None,type(exc).__name__
    with ThreadPoolExecutor(max_workers=max(1,min(4,workers))) as pool:
        # Bounded batches avoid retaining 20,000 Futures and make progress durable.
        for start in range(0,len(pending),100):
            for oid,payload,error in pool.map(fetch,pending[start:start+100]):
                if error:
                    result['errors']+=1
                    print(f'DETAIL_ERROR: account={account_id} order={oid} type={error}',flush=True)
                    continue
                try:
                    result['items_loaded']+=store_detail(conn,payload,account_id,fulfillment)
                    conn.commit();result['orders_loaded']+=1
                except Exception as exc:
                    conn.rollback();result['errors']+=1
                    print(f'DETAIL_ERROR: account={account_id} order={oid} type={type(exc).__name__}',flush=True)
            result['detail_requests']=sum(c.requests_made for c in clients)
            if progress: progress(result)
            print('DETAIL_PROGRESS: '+json.dumps(result),flush=True)
    result['detail_requests']=sum(c.requests_made for c in clients)
    api.requests_made+=result['detail_requests']
    return result
