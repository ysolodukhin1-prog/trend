#!/usr/bin/env python3
"""Create and refresh read-optimized Lamoda materialized views."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2


DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DASHBOARD_ROOT), str(DASHBOARD_ROOT.parent)]


VIEW_SQL = r"""
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_orders_flat AS
WITH latest AS (
    SELECT DISTINCT ON (record_key)
        record_key, snapshot_date, payload, synced_at, account_id, fulfillment
    FROM public.lamoda_v2_entities
    WHERE account_id IS NOT NULL AND dataset = 'orders'
    ORDER BY record_key, synced_at DESC, snapshot_date DESC
)
SELECT
    record_key,
    COALESCE(payload->>'orderId', payload->>'id', record_key) AS order_id,
    NULLIF(payload->>'createdAt', '')::timestamptz AS created_at,
    NULLIF(payload->>'updatedAt', '')::timestamptz AS updated_at,
    COALESCE(NULLIF(payload->>'createdAt', '')::timestamptz::date, snapshot_date) AS order_date,
    COALESCE(payload->>'status', '') AS status,
    COALESCE((payload->>'isConfirmed')::boolean, false) AS is_confirmed,
    COALESCE(payload->>'paymentMethod', '') AS payment_method,
    COALESCE(payload#>>'{deliveryMethod,shippingMethodCode}', '') AS shipping_method_code,
    COALESCE(payload#>>'{deliveryMethod,shippingMethodName}', '') AS shipping_method_name,
    NULLIF(payload#>>'{fullSum,amount}', '')::numeric AS total_amount,
    COALESCE(payload#>>'{fullSum,currency}', '') AS currency,
    snapshot_date,
    synced_at, account_id, fulfillment
FROM latest
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_orders_flat_record
    ON public.mv_lamoda_orders_flat(record_key);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_orders_flat_date
    ON public.mv_lamoda_orders_flat(order_date);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_orders_flat_status
    ON public.mv_lamoda_orders_flat(status);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_orders_daily AS
SELECT
    order_date,
    status,
    COUNT(*)::bigint AS orders_count,
    COALESCE(SUM(total_amount), 0)::numeric AS orders_amount
FROM public.mv_lamoda_orders_flat
GROUP BY order_date, status
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_orders_daily
    ON public.mv_lamoda_orders_daily(order_date, status);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_returns_flat AS
WITH latest AS (
    SELECT DISTINCT ON (record_key)
        record_key, snapshot_date, payload, synced_at, account_id, fulfillment
    FROM public.lamoda_v2_entities
    WHERE account_id IS NOT NULL AND dataset = 'fbs_returns'
    ORDER BY record_key, synced_at DESC, snapshot_date DESC
)
SELECT
    record_key,
    COALESCE(payload->>'id', record_key) AS return_id,
    COALESCE(payload->>'orderId', '') AS order_id,
    COALESCE(payload->>'itemId', '') AS item_id,
    COALESCE(payload->>'externalSku', '') AS seller_sku,
    COALESCE(payload->>'sku', '') AS lamoda_sku,
    COALESCE(payload->>'itemName', '') AS item_name,
    COALESCE(payload->>'dimensionSymbol', '') AS size,
    COALESCE(payload->>'status', '') AS status,
    COALESCE(payload->>'returnType', '') AS return_type,
    NULLIF(payload->>'returnDate', '')::timestamptz AS returned_at,
    COALESCE(NULLIF(payload->>'returnDate', '')::timestamptz::date, snapshot_date) AS return_date,
    NULLIF(payload#>>'{price,amount}', '')::numeric AS amount,
    COALESCE(payload#>>'{price,currency}', '') AS currency,
    snapshot_date,
    synced_at, account_id, fulfillment
FROM latest
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_returns_flat_record
    ON public.mv_lamoda_returns_flat(record_key);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_returns_flat_date
    ON public.mv_lamoda_returns_flat(return_date);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_returns_flat_status
    ON public.mv_lamoda_returns_flat(status, return_type);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_returns_daily AS
SELECT
    return_date,
    status,
    return_type,
    COUNT(*)::bigint AS returns_count,
    COALESCE(SUM(amount), 0)::numeric AS returns_amount
FROM public.mv_lamoda_returns_flat
GROUP BY return_date, status, return_type
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_returns_daily
    ON public.mv_lamoda_returns_daily(return_date, status, return_type);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_catalog_flat AS
WITH latest AS (
    SELECT DISTINCT ON (record_key)
        record_key, seller_sku, lamoda_sku, status, snapshot_date, payload, synced_at, account_id, fulfillment
    FROM public.lamoda_v2_entities
    WHERE account_id IS NOT NULL AND dataset IN ('catalog', 'prices')
    ORDER BY record_key, (dataset = 'prices') DESC, synced_at DESC, snapshot_date DESC
)
SELECT
    record_key,
    COALESCE(NULLIF(seller_sku, ''), payload->>'externalSku', '') AS seller_sku,
    COALESCE(NULLIF(lamoda_sku, ''), payload->>'sku', '') AS lamoda_sku,
    COALESCE(payload->>'name', '') AS product_name,
    COALESCE(payload->>'brand', '') AS brand,
    COALESCE((
        SELECT string_agg(COALESCE(level->>'name', level->>'value', ''), ' / ' ORDER BY ordinality)
        FROM jsonb_array_elements(COALESCE(payload->'categoryLevels', '[]'::jsonb)) WITH ORDINALITY AS x(level, ordinality)
    ), '') AS category_path,
    COALESCE(NULLIF(status, ''), payload->>'status', '') AS status,
    COALESCE(NULLIF(payload->>'quantity', '')::numeric, 0) AS quantity,
    NULLIF(payload#>>'{priceInfo,0,price,amount}', '')::numeric AS price,
    NULLIF(payload#>>'{priceInfo,0,salePrice,amount}', '')::numeric AS sale_price,
    COALESCE(payload#>>'{priceInfo,0,price,currency}', payload#>>'{priceInfo,0,salePrice,currency}', '') AS currency,
    COALESCE(payload#>>'{priceInfo,0,priceStatus,code}', '') AS price_status,
    NULLIF(payload#>>'{priceInfo,0,saleStart}', '')::timestamptz AS sale_start,
    NULLIF(payload#>>'{priceInfo,0,saleEnd}', '')::timestamptz AS sale_end,
    COALESCE(payload#>>'{qcModeration,status}', payload#>>'{qcModeration,stage}', '') AS moderation_status,
    COALESCE(payload#>>'{qcModeration,comment}', '') AS moderation_comment,
    snapshot_date,
    synced_at, account_id, fulfillment
FROM latest
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_catalog_flat_record
    ON public.mv_lamoda_catalog_flat(record_key);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_catalog_flat_status
    ON public.mv_lamoda_catalog_flat(status);
CREATE INDEX IF NOT EXISTS ix_mv_lamoda_catalog_flat_sku
    ON public.mv_lamoda_catalog_flat(seller_sku, lamoda_sku);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_promotions_flat AS
WITH latest AS (
    SELECT DISTINCT ON (record_key)
        record_key, snapshot_date, payload, synced_at, account_id, fulfillment
    FROM public.lamoda_v2_entities
    WHERE account_id IS NOT NULL AND dataset = 'promotions'
    ORDER BY record_key, synced_at DESC, snapshot_date DESC
)
SELECT
    record_key,
    COALESCE(payload->>'id', record_key) AS promotion_id,
    COALESCE(payload->>'name', '') AS promotion_name,
    COALESCE(payload->>'type', '') AS promotion_type,
    COALESCE(payload->>'status', '') AS status,
    NULLIF(payload->>'activeFrom', '')::timestamptz AS active_from,
    NULLIF(payload->>'activeTo', '')::timestamptz AS active_to,
    COALESCE((payload->>'hasLamodaDiscount')::boolean, false) AS has_lamoda_discount,
    COALESCE((payload->>'isRegistrationClosed')::boolean, false) AS registration_closed,
    snapshot_date,
    synced_at, account_id, fulfillment
FROM latest
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_promotions_flat_record
    ON public.mv_lamoda_promotions_flat(record_key);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_lamoda_fbo_shipments_flat AS
WITH latest AS (
    SELECT DISTINCT ON (record_key)
        record_key, snapshot_date, payload, synced_at, account_id, fulfillment
    FROM public.lamoda_v2_entities
    WHERE account_id IS NOT NULL AND dataset = 'fbo_shipments'
    ORDER BY record_key, synced_at DESC, snapshot_date DESC
)
SELECT
    record_key,
    COALESCE(payload->>'id', record_key) AS shipment_id,
    COALESCE(payload->>'externalShipmentId', '') AS external_shipment_id,
    COALESCE(payload->>'status', '') AS status,
    NULLIF(payload->>'plannedDate', '')::timestamptz AS planned_at,
    COALESCE((payload#>>'{statistics,plannedItems}')::integer, 0) AS planned_items,
    COALESCE((payload#>>'{statistics,acceptedItems}')::integer, 0) AS accepted_items,
    COALESCE((payload#>>'{statistics,receivedItems}')::integer, 0) AS received_items,
    COALESCE((payload#>>'{statistics,damagedItems}')::integer, 0) AS damaged_items,
    COALESCE((payload#>>'{statistics,missingItems}')::integer, 0) AS missing_items,
    snapshot_date,
    synced_at, account_id, fulfillment
FROM latest
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_lamoda_fbo_shipments_flat_record
    ON public.mv_lamoda_fbo_shipments_flat(record_key);
"""


DROP_SQL = """
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_returns_daily;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_orders_daily;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_fbo_shipments_flat;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_promotions_flat;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_catalog_flat;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_returns_flat;
DROP MATERIALIZED VIEW IF EXISTS public.mv_lamoda_orders_flat;
"""


REFRESH_ORDER = (
    "mv_lamoda_orders_flat",
    "mv_lamoda_orders_daily",
    "mv_lamoda_returns_flat",
    "mv_lamoda_returns_daily",
    "mv_lamoda_catalog_flat",
    "mv_lamoda_promotions_flat",
    "mv_lamoda_fbo_shipments_flat",
)


def _grant_reader(cur) -> None:
    for name in REFRESH_ORDER:
        cur.execute(f"GRANT SELECT ON public.{name} TO pulse_reader")


def db_config(client: str) -> dict:
    import pulse_vps_admin as runtime

    runtime.configure_scope()
    runtime._USE_WRITER_CONFIG.set(True)
    runtime.app.CURRENT_CLIENT.set(client)
    os.environ.update(DASHBOARD_CLIENT=client, KM_DB_NAME=client)
    config = dict(runtime.app.read_db_config(client))
    config["database"] = client
    return config


def rebuild(client: str, recreate: bool = False) -> None:
    with psycopg2.connect(**db_config(client)) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"lamoda-marts:{client}",))
            try:
                if recreate:
                    cur.execute(DROP_SQL)
                cur.execute(VIEW_SQL)
                for index, name in enumerate(REFRESH_ORDER, start=1):
                    print(f"PROGRESS: LAMODA views {index}/{len(REFRESH_ORDER)} {name}", flush=True)
                    cur.execute(f"REFRESH MATERIALIZED VIEW public.{name}")
                _grant_reader(cur)
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"lamoda-marts:{client}",))
    print(f"RESULT: LAMODA views client={client} count={len(REFRESH_ORDER)}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("toptop", "lera_nena"), required=True)
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args()
    rebuild(args.client, args.recreate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
