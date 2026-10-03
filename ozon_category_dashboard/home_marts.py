#!/usr/bin/env python3
"""Materialized source layer and fast readers for the PULSE Home report.

The refresh is intentionally one PostgreSQL transaction.  If any refresh or
validation fails, PostgreSQL rolls the whole transaction back and readers keep
the previous complete generation.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


VERSION = 2
MARTS = (
    "mv_pulse_home_sales_price_daily_v1",
    "mv_pulse_home_inventory_sku_v1",
    "mv_pulse_home_stock_valuation_daily_v1",
    "mv_pulse_home_inventory_daily_v1",
    "mv_pulse_home_category_daily_v1",
    "mv_pulse_home_category_stock_v1",
    "mv_pulse_home_finance_daily_v2",
    "mv_pulse_yandex_orders_daily_v1",
    "mv_pulse_yandex_services_daily_v1",
    "mv_pulse_yandex_transactions_daily_v1",
    "mv_pulse_yandex_sku_orders_daily_v1",
    "mv_pulse_yandex_returns_v1",
    "mv_pulse_yandex_stocks_v1",
    "mv_pulse_yandex_realization_v1",
    "mv_pulse_yandex_services_fact_v1",
    "mv_pulse_yandex_marketing_daily_v1",
    "mv_pulse_yandex_boost_sales_v1",
    "mv_pulse_yandex_data_coverage_v1",
    "mv_pulse_yandex_lost_items_v1",
    "mv_pulse_home_yandex_finance_daily_v1",
)


DDL = r"""
CREATE TABLE IF NOT EXISTS public.pulse_home_mart_state (
  mart_version integer PRIMARY KEY,
  generation bigint NOT NULL,
  refreshed_at timestamptz NOT NULL,
  source_max_at timestamptz,
  row_counts jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('ready'))
);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_sales_price_daily_v1 AS
WITH wb AS (
  SELECT 'wb'::text AS marketplace, operation_date AS report_date,
         nm_id::text AS sku,
         max(nullif(subject_name, '')) AS category_name,
         sum(retail_amount * CASE
           WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
             OR lower(coalesce(doc_type_name, '')) LIKE '%return%'
           THEN -1 ELSE 1 END)::numeric AS revenue,
         sum(CASE WHEN retail_amount <> 0 OR for_pay <> 0 THEN quantity * CASE
           WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
             OR lower(coalesce(doc_type_name, '')) LIKE '%return%'
           THEN -1 ELSE 1 END ELSE 0 END)::numeric AS units
  FROM public.wb_finance_lines
  WHERE nm_id IS NOT NULL
  GROUP BY operation_date, nm_id
), ozon AS (
  SELECT 'ozon'::text AS marketplace, operation_date AS report_date, sku,
         NULL::text AS category_name,
         sum(amount)::numeric AS revenue,
         sum(quantity)::numeric AS units
  FROM public.ozon_finance_lines
  WHERE line_kind = 'revenue' AND nullif(sku, '') IS NOT NULL
  GROUP BY operation_date, sku
)
SELECT * FROM wb UNION ALL SELECT * FROM ozon
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_sales_price_daily_v1
  ON public.mv_pulse_home_sales_price_daily_v1(marketplace, report_date, sku);
CREATE INDEX IF NOT EXISTS ix_pulse_home_sales_price_sku_v1
  ON public.mv_pulse_home_sales_price_daily_v1(marketplace, sku, report_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_inventory_sku_v1 AS
WITH preferred AS (
  SELECT i.*
  FROM public.inventory_history_daily i
  WHERE i.marketplace <> 'wb'
     OR i.source_type = 'wb_stock_history_daily_csv'
     OR NOT EXISTS (
       SELECT 1 FROM public.inventory_history_daily selected
       WHERE selected.marketplace = 'wb'
         AND selected.snapshot_date = i.snapshot_date
         AND selected.source_type = 'wb_stock_history_daily_csv'
     )
)
SELECT marketplace, snapshot_date, sku,
       max(nullif(seller_article, '')) AS seller_article,
       max(nullif(product_name, '')) AS product_name,
       max(nullif(category_name, '')) AS category_name,
       sum(stock_available_qty)::numeric AS stock_available_qty,
       sum(stock_preparing_qty)::numeric AS stock_preparing_qty,
       sum(stock_reserved_qty)::numeric AS stock_reserved_qty,
       sum(stock_total_qty)::numeric AS stock_total_qty,
       sum(to_customer_qty)::numeric AS to_customer_qty,
       sum(from_customer_qty)::numeric AS from_customer_qty,
       sum(stock_value_rub)::numeric AS source_stock_value_rub,
       avg(turnover_days)::numeric AS turnover_days,
       avg(days_to_stockout)::numeric AS days_to_stockout,
       sum(lost_orders_qty)::numeric AS lost_orders_qty,
       sum(lost_orders_rub)::numeric AS lost_orders_rub,
       sum(in_supply_orders_qty)::numeric AS in_supply_orders_qty,
       sum(in_transit_supply_qty)::numeric AS in_transit_supply_qty,
       sum(returning_from_customers_qty)::numeric AS returning_from_customers_qty,
       sum(checking_qty)::numeric AS checking_qty,
       sum(defective_qty)::numeric AS defective_qty,
       sum(expiring_qty)::numeric AS expiring_qty,
       sum(preparing_to_remove_qty)::numeric AS preparing_to_remove_qty,
       sum(marked_qty)::numeric AS marked_qty
FROM preferred
WHERE nullif(sku, '') IS NOT NULL
GROUP BY marketplace, snapshot_date, sku
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_inventory_sku_v1
  ON public.mv_pulse_home_inventory_sku_v1(marketplace, snapshot_date, sku);
CREATE INDEX IF NOT EXISTS ix_pulse_home_inventory_sku_date_v1
  ON public.mv_pulse_home_inventory_sku_v1(marketplace, sku, snapshot_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_stock_valuation_daily_v1 AS
WITH latest_ozon_price AS (
  SELECT DISTINCT ON (sku) sku, snapshot_date,
         coalesce(nullif(marketing_seller_price, 0), nullif(price, 0))::numeric AS price
  FROM public.ozon_product_price_snapshots
  WHERE nullif(sku, '') IS NOT NULL
  ORDER BY sku, snapshot_date DESC
), rolling AS (
  SELECT s.marketplace, s.snapshot_date, s.sku, s.category_name,
         coalesce(s.stock_available_qty, 0)::numeric AS stock_available_qty,
         o.snapshot_date AS price_snapshot_date,
         CASE
           WHEN s.marketplace = 'ozon' AND o.price > 0 THEN o.price
           WHEN abs(sum(p.units)) > 0 THEN abs(sum(p.revenue)) / abs(sum(p.units))
         END::numeric AS direct_price,
         CASE
           WHEN s.marketplace = 'ozon' AND o.price > 0 THEN 'current'
           WHEN abs(sum(p.units)) > 0 THEN 'historical'
         END::text AS price_source
  FROM public.mv_pulse_home_inventory_sku_v1 s
  LEFT JOIN public.mv_pulse_home_sales_price_daily_v1 p
    ON p.marketplace = s.marketplace AND p.sku = s.sku
   AND p.report_date BETWEEN s.snapshot_date - 29 AND s.snapshot_date
  LEFT JOIN latest_ozon_price o
    ON s.marketplace = 'ozon' AND o.sku = s.sku
  GROUP BY s.marketplace, s.snapshot_date, s.sku, s.category_name,
           s.stock_available_qty, o.snapshot_date, o.price
), category_prices AS (
  SELECT marketplace, snapshot_date, coalesce(category_name, '') AS category_key,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY direct_price)::numeric AS median_price
  FROM rolling WHERE direct_price > 0
  GROUP BY marketplace, snapshot_date, coalesce(category_name, '')
), all_prices AS (
  SELECT marketplace, snapshot_date,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY direct_price)::numeric AS median_price
  FROM rolling WHERE direct_price > 0
  GROUP BY marketplace, snapshot_date
), resolved AS (
  SELECT r.*,
         coalesce(r.direct_price, c.median_price, a.median_price)::numeric AS resolved_price,
         CASE WHEN r.direct_price IS NOT NULL THEN r.price_source
              WHEN c.median_price IS NOT NULL THEN 'category_median'
              WHEN a.median_price IS NOT NULL THEN 'assortment_median'
         END AS resolved_source
  FROM rolling r
  LEFT JOIN category_prices c
    ON c.marketplace = r.marketplace
   AND c.snapshot_date = r.snapshot_date
   AND c.category_key = coalesce(r.category_name, '')
  LEFT JOIN all_prices a
    ON a.marketplace = r.marketplace
   AND a.snapshot_date = r.snapshot_date
)
SELECT marketplace, snapshot_date,
       sum(stock_available_qty)::numeric AS stock_available_qty,
       CASE WHEN count(*) FILTER (
              WHERE stock_available_qty <> 0 AND resolved_price IS NULL
            ) > 0 THEN NULL
            ELSE sum(stock_available_qty * resolved_price)::numeric END AS stock_value_rub,
       sum(stock_available_qty) FILTER (WHERE resolved_source IN ('current', 'historical'))::numeric AS matched_units,
       sum(stock_available_qty) FILTER (WHERE resolved_source = 'historical')::numeric AS historical_units,
       sum(stock_available_qty) FILTER (WHERE resolved_source = 'category_median')::numeric AS category_units,
       sum(stock_available_qty) FILTER (WHERE resolved_source = 'assortment_median')::numeric AS median_units,
       count(*) FILTER (WHERE resolved_price IS NOT NULL)::bigint AS priced_sku_count,
       count(*)::bigint AS stock_sku_count,
       min(price_snapshot_date) FILTER (WHERE resolved_source = 'current') AS price_date_from,
       max(price_snapshot_date) FILTER (WHERE resolved_source = 'current') AS price_date_to
FROM resolved
GROUP BY marketplace, snapshot_date
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_stock_valuation_daily_v1
  ON public.mv_pulse_home_stock_valuation_daily_v1(marketplace, snapshot_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_inventory_daily_v1 AS
SELECT s.marketplace, s.snapshot_date AS report_date,
       count(*)::bigint AS stock_sku_count,
       count(*) FILTER (WHERE coalesce(s.stock_available_qty, 0) > 0)::bigint AS in_stock_sku_count,
       sum(s.stock_available_qty)::numeric AS stock_available_qty,
       sum(s.stock_preparing_qty)::numeric AS stock_preparing_qty,
       sum(s.stock_reserved_qty)::numeric AS stock_reserved_qty,
       sum(s.stock_total_qty)::numeric AS stock_total_qty,
       sum(s.to_customer_qty)::numeric AS to_customer_qty,
       sum(s.from_customer_qty)::numeric AS from_customer_qty,
       coalesce(v.stock_value_rub, sum(s.source_stock_value_rub))::numeric AS stock_value_rub,
       avg(s.turnover_days)::numeric AS turnover_days,
       avg(s.days_to_stockout)::numeric AS days_to_stockout,
       sum(s.lost_orders_qty)::numeric AS lost_orders_qty,
       sum(s.lost_orders_rub)::numeric AS lost_orders_rub,
       sum(s.in_supply_orders_qty)::numeric AS in_supply_orders_qty,
       sum(s.in_transit_supply_qty)::numeric AS in_transit_supply_qty,
       sum(s.returning_from_customers_qty)::numeric AS returning_from_customers_qty,
       sum(s.checking_qty)::numeric AS checking_qty,
       sum(s.defective_qty)::numeric AS defective_qty,
       sum(s.expiring_qty)::numeric AS expiring_qty,
       sum(s.preparing_to_remove_qty)::numeric AS preparing_to_remove_qty,
       sum(s.marked_qty)::numeric AS marked_qty,
       v.matched_units, v.historical_units, v.category_units, v.median_units,
       v.priced_sku_count, v.price_date_from, v.price_date_to
FROM public.mv_pulse_home_inventory_sku_v1 s
LEFT JOIN public.mv_pulse_home_stock_valuation_daily_v1 v
  ON v.marketplace = s.marketplace AND v.snapshot_date = s.snapshot_date
GROUP BY s.marketplace, s.snapshot_date, v.stock_value_rub, v.matched_units,
         v.historical_units, v.category_units, v.median_units,
         v.priced_sku_count, v.price_date_from, v.price_date_to
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_inventory_daily_v1
  ON public.mv_pulse_home_inventory_daily_v1(marketplace, report_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_category_daily_v1 AS
SELECT 'wb'::text AS marketplace, report_date,
       coalesce(nullif(category_name, ''), 'Без категории') AS category_name,
       sum(ordered_amount_rub)::numeric AS zakazano_rub,
       sum(ordered_units)::numeric AS zakazano_sht
FROM public.mv_wb_funnel_daily_by_article_category
GROUP BY report_date, coalesce(nullif(category_name, ''), 'Без категории')
UNION ALL
SELECT 'ozon'::text, report_date,
       coalesce(nullif(category_name, ''), 'Без категории'),
       sum(ordered_amount_rub)::numeric, sum(ordered_units)::numeric
FROM public.mv_ozon_funnel_daily_by_article_category
GROUP BY report_date, coalesce(nullif(category_name, ''), 'Без категории')
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_category_daily_v1
  ON public.mv_pulse_home_category_daily_v1(marketplace, report_date, category_name);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_category_stock_v1 AS
WITH latest AS (
  SELECT marketplace, max(snapshot_date) AS snapshot_date
  FROM public.mv_pulse_home_inventory_sku_v1 GROUP BY marketplace
)
SELECT s.marketplace, s.snapshot_date,
       coalesce(nullif(s.category_name, ''), 'Без категории') AS category_name,
       count(*)::bigint AS sku_count,
       sum(s.stock_available_qty)::numeric AS total_stock_qty
FROM public.mv_pulse_home_inventory_sku_v1 s
JOIN latest l USING (marketplace, snapshot_date)
GROUP BY s.marketplace, s.snapshot_date,
         coalesce(nullif(s.category_name, ''), 'Без категории')
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_category_stock_v1
  ON public.mv_pulse_home_category_stock_v1(marketplace, category_name);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_finance_daily_v2 AS
WITH wb AS (
  SELECT operation_date AS report_date,
         count(*)::bigint AS source_rows,
         sum(retail_amount * CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
              OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END)::numeric AS revenue,
         sum(CASE WHEN retail_amount <> 0 OR for_pay <> 0 THEN quantity * CASE
              WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
                OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END ELSE 0 END)::numeric AS units,
         sum(for_pay * CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
              OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END
             - delivery_service - paid_storage - paid_acceptance - deduction - penalty
             + additional_payment + cashback_amount - rebill_logistic_cost)::numeric AS marketplace_net,
         sum((retail_amount - for_pay - acquiring_fee) * CASE
              WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
                OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END)::numeric AS commission,
         sum(acquiring_fee * CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
              OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END)::numeric AS acquiring,
         sum(delivery_service + rebill_logistic_cost)::numeric AS logistics,
         sum(paid_storage)::numeric AS storage,
         sum(paid_acceptance)::numeric AS acceptance,
         sum(deduction)::numeric AS deduction, sum(penalty)::numeric AS penalty,
         sum(additional_payment)::numeric AS additional_payment,
         sum(cashback_amount)::numeric AS cashback,
         sum(rebill_logistic_cost)::numeric AS rebill_logistics,
         sum(retail_amount * CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%возврат%'
              OR lower(coalesce(doc_type_name, '')) LIKE '%return%' THEN -1 ELSE 1 END) / 3::numeric AS cogs,
         0::numeric AS seller_unit_costs,
         count(DISTINCT nm_id) FILTER (WHERE nm_id IS NOT NULL)::bigint AS estimated_sku_count
  FROM public.wb_finance_lines GROUP BY operation_date
), ozon_lines AS (
  SELECT l.operation_date AS report_date, count(*)::bigint AS source_rows,
         sum(l.amount) FILTER (WHERE l.line_kind = 'revenue')::numeric AS revenue,
         sum(l.quantity) FILTER (WHERE l.line_kind = 'revenue')::numeric AS units,
         sum(l.amount) FILTER (WHERE l.line_kind = 'commission')::numeric AS commission,
         sum(l.amount) FILTER (WHERE l.line_kind = 'acquiring')::numeric AS acquiring,
         sum(l.amount) FILTER (WHERE l.line_kind IN ('logistics','last_mile','reverse_logistics','fulfillment_ozon'))::numeric AS logistics,
         sum(l.amount) FILTER (WHERE l.line_kind = 'storage')::numeric AS storage,
         sum(CASE WHEN l.line_kind = 'revenue' THEN
             CASE WHEN s.cogs_per_unit IS NOT NULL THEN l.quantity * s.cogs_per_unit
                  ELSE l.amount / 3 END ELSE 0 END)::numeric AS cogs,
         sum(CASE WHEN l.line_kind = 'revenue' THEN l.quantity *
             (coalesce(s.fulfillment_per_unit,0)+coalesce(s.inbound_per_unit,0)+
              coalesce(s.crossdock_per_unit,0)+coalesce(s.acceptance_per_unit,0)+
              coalesce(s.other_per_unit,0)) ELSE 0 END)::numeric AS seller_unit_costs,
         count(DISTINCT l.sku) FILTER (
           WHERE l.line_kind='revenue' AND nullif(l.sku,'') IS NOT NULL AND s.cogs_per_unit IS NULL
         )::bigint AS estimated_sku_count
  FROM public.ozon_finance_lines l
  LEFT JOIN public.ozon_unit_product_settings s ON s.sku=l.sku
  GROUP BY l.operation_date
), ozon_events AS (
  SELECT operation_date AS report_date, count(*)::bigint AS source_rows,
         sum(total_amount)::numeric AS marketplace_net
  FROM public.ozon_finance_events GROUP BY operation_date
)
SELECT 'wb'::text AS marketplace, report_date, source_rows, revenue, units,
       marketplace_net, commission, acquiring, logistics, storage, acceptance,
       deduction, penalty, additional_payment, cashback, rebill_logistics,
       cogs, seller_unit_costs, estimated_sku_count
FROM wb
UNION ALL
SELECT 'ozon'::text, coalesce(l.report_date,e.report_date),
       coalesce(l.source_rows,0)+coalesce(e.source_rows,0), coalesce(l.revenue,0),
       coalesce(l.units,0), coalesce(e.marketplace_net,0), coalesce(l.commission,0),
       coalesce(l.acquiring,0), coalesce(l.logistics,0), coalesce(l.storage,0),
       0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric, 0::numeric,
       coalesce(l.cogs,0), coalesce(l.seller_unit_costs,0), l.estimated_sku_count
FROM ozon_lines l FULL OUTER JOIN ozon_events e USING(report_date)
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_finance_daily_v2
  ON public.mv_pulse_home_finance_daily_v2(marketplace, report_date);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_orders_daily_v1 AS
  SELECT * FROM public.yandex_orders_daily WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_orders_daily_v1
  ON public.mv_pulse_yandex_orders_daily_v1(client_key, order_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_services_daily_v1 AS
  SELECT * FROM public.yandex_services_daily WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_services_daily_v1
  ON public.mv_pulse_yandex_services_daily_v1(client_key, service_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_transactions_daily_v1 AS
  SELECT * FROM public.yandex_transactions_daily WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_transactions_daily_v1
  ON public.mv_pulse_yandex_transactions_daily_v1(client_key, transaction_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_sku_orders_daily_v1 AS
  SELECT * FROM public.yandex_sku_orders_daily WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_sku_orders_daily_v1
  ON public.mv_pulse_yandex_sku_orders_daily_v1(client_key, order_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_returns_v1 AS
  SELECT * FROM public.yandex_fact_returns WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_returns_v1
  ON public.mv_pulse_yandex_returns_v1(client_key, return_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_stocks_v1 AS
  SELECT * FROM public.yandex_fact_stocks WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_stocks_v1
  ON public.mv_pulse_yandex_stocks_v1(client_key, snapshot_date, campaign_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_realization_v1 AS
  SELECT * FROM public.yandex_fact_realization WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_realization_v1
  ON public.mv_pulse_yandex_realization_v1(client_key, event_date, campaign_id, offer_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_services_fact_v1 AS
  SELECT * FROM public.yandex_fact_services WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_services_fact_v1
  ON public.mv_pulse_yandex_services_fact_v1(client_key, service_date, campaign_id, offer_id);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_marketing_daily_v1 AS
  SELECT * FROM public.yandex_marketing_daily WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_marketing_daily_v1
  ON public.mv_pulse_yandex_marketing_daily_v1(client_key, metric_date);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_boost_sales_v1 AS
  SELECT * FROM public.yandex_fact_boost_sales_period WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_boost_sales_v1
  ON public.mv_pulse_yandex_boost_sales_v1(client_key, date_from, date_to);
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_data_coverage_v1 AS
  SELECT * FROM public.yandex_data_coverage WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_data_coverage_v1
  ON public.mv_pulse_yandex_data_coverage_v1(client_key, source_key, campaign_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_yandex_lost_items_v1 AS
WITH raw AS (
  SELECT client_key,business_id,payload->>'orderId' order_id,payload->>'yourSku' offer_id,
         ya_date(payload->>'compensationDate') event_date,
         ya_number(payload->>'compensationAmount') amount
  FROM public.yandex_report_rows
  WHERE source_key='realization' AND sheet='lost_items'
  UNION ALL
  SELECT client_key,business_id,payload->>'orderId',payload->>'yourSku',
         ya_date(payload->>'decompensationDate'),-ya_number(payload->>'decompensationAmount')
  FROM public.yandex_report_rows
  WHERE source_key='realization' AND sheet='lost_items'
), events AS (
  SELECT client_key,business_id,order_id,offer_id,event_date,
         CASE WHEN count(DISTINCT amount)=1 THEN min(amount) END amount,
         count(DISTINCT amount)>1 AS conflict
  FROM raw WHERE event_date IS NOT NULL
  GROUP BY 1,2,3,4,5
), attribution AS (
  SELECT client_key,business_id,order_id,offer_id,
         CASE WHEN count(DISTINCT campaign_id)=1 THEN min(campaign_id) END campaign_id
  FROM public.yandex_fact_realization GROUP BY 1,2,3,4
)
SELECT e.client_key,e.business_id,a.campaign_id,e.order_id,e.offer_id,e.event_date,
       e.amount,e.conflict
FROM events e LEFT JOIN attribution a USING(client_key,business_id,order_id,offer_id)
WITH NO DATA;
CREATE INDEX IF NOT EXISTS ix_pulse_yandex_lost_items_v1
  ON public.mv_pulse_yandex_lost_items_v1(client_key, event_date, campaign_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_pulse_home_yandex_finance_daily_v1 AS
WITH realization AS (
  SELECT client_key,event_date AS report_date,
         sum(CASE WHEN event_type='returned' THEN -amount ELSE amount END)::numeric AS revenue,
         sum(CASE WHEN event_type='returned' THEN -units ELSE units END)::numeric AS units,
         sum(CASE WHEN event_type='returned' THEN -amount ELSE amount END)::numeric AS net,
         count(*) FILTER (WHERE amount IS NULL)::bigint AS missing_amount,
         count(*)::bigint AS source_rows
  FROM public.mv_pulse_yandex_realization_v1
  WHERE event_date IS NOT NULL GROUP BY client_key,event_date
), services AS (
  SELECT client_key,coalesce(service_date,act_date) AS report_date,
         -sum(service_amount)::numeric AS net,
         count(*) FILTER (WHERE service_amount IS NULL)::bigint AS missing_amount,
         count(*)::bigint AS source_rows
  FROM public.mv_pulse_yandex_services_fact_v1
  WHERE coalesce(service_date,act_date) IS NOT NULL
  GROUP BY client_key,coalesce(service_date,act_date)
), lost AS (
  SELECT client_key,event_date AS report_date,sum(amount)::numeric AS net,
         count(*) FILTER (WHERE amount IS NULL OR conflict)::bigint AS missing_amount,
         count(*)::bigint AS source_rows
  FROM public.mv_pulse_yandex_lost_items_v1 GROUP BY client_key,event_date
), combined AS (
  SELECT client_key,report_date,revenue,units,net,missing_amount,source_rows FROM realization
  UNION ALL SELECT client_key,report_date,NULL,NULL,net,missing_amount,source_rows FROM services
  UNION ALL SELECT client_key,report_date,NULL,NULL,net,missing_amount,source_rows FROM lost
)
SELECT client_key,report_date,sum(revenue)::numeric AS revenue,sum(units)::numeric AS units,
       sum(net)::numeric AS marketplace_net,sum(missing_amount)::bigint AS missing_amount,
       sum(source_rows)::bigint AS source_rows
FROM combined GROUP BY client_key,report_date
WITH NO DATA;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pulse_home_yandex_finance_daily_v1
  ON public.mv_pulse_home_yandex_finance_daily_v1(client_key, report_date);
"""


def _n(value: Any) -> float | None:
    if value is None:
        return None
    number = Decimal(str(value))
    return float(number) if number.is_finite() else None


def relation_exists(conn: Any, relation: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) AS relation", (f"public.{relation}",))
        row = cursor.fetchone() or {}
    return bool(row.get("relation") if isinstance(row, dict) else row[0])


def install(conn: Any) -> None:
    with conn.cursor() as cursor:
        cursor.execute(DDL)


def refresh(conn: Any, *, log=print) -> dict[str, int]:
    started = time.monotonic()
    counts: dict[str, int] = {}
    log(f"ПЛАН: витрины Главной v{VERSION} | объектов={len(MARTS)} | одна транзакция | предыдущая версия сохраняется при ошибке", flush=True)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='10s'; SET LOCAL statement_timeout='15min'")
            for index, mart in enumerate(MARTS, 1):
                step_started = time.monotonic()
                cursor.execute(f"REFRESH MATERIALIZED VIEW public.{mart}")
                cursor.execute(f"SELECT count(*) AS rows FROM public.{mart}")
                row = cursor.fetchone()
                count = int((row.get("rows") if isinstance(row, dict) else row[0]) or 0)
                counts[mart] = count
                elapsed = time.monotonic() - started
                remaining = elapsed / index * (len(MARTS) - index)
                log(
                    f"ПРОГРЕСС: {index}/{len(MARTS)} ({index/len(MARTS):.0%}) | {mart} | "
                    f"rows={count} step={time.monotonic()-step_started:.1f}s elapsed={elapsed:.1f}s ETA={remaining:.1f}s",
                    flush=True,
                )
            cursor.execute(
                """
                SELECT marketplace,report_date,stock_available_qty,stock_value_rub
                FROM public.mv_pulse_home_inventory_daily_v1 d
                WHERE report_date=(SELECT max(report_date) FROM public.mv_pulse_home_inventory_daily_v1 x
                                   WHERE x.marketplace=d.marketplace)
                ORDER BY marketplace
                """
            )
            latest = [dict(row) for row in cursor.fetchall()]
            if not latest:
                raise RuntimeError("Home inventory mart has no rows")
            broken = [row for row in latest if _n(row["stock_available_qty"]) not in (None, 0) and _n(row["stock_value_rub"]) is None]
            if broken:
                raise RuntimeError(f"Home stock valuation is incomplete: {[row['marketplace'] for row in broken]}")
            cursor.execute(
                """
                SELECT greatest(
                  coalesce((SELECT max(imported_at) FROM public.inventory_history_daily),'-infinity'::timestamptz),
                  coalesce((SELECT max(captured_at) FROM public.wb_finance_lines),'-infinity'::timestamptz),
                  coalesce((SELECT max(imported_at) AT TIME ZONE 'UTC' FROM public.ozon_finance_lines),'-infinity'::timestamptz)
                ) AS source_max_at
                """
            )
            source_max_at = (cursor.fetchone() or {}).get("source_max_at")
            cursor.execute(
                """
                INSERT INTO public.pulse_home_mart_state
                  (mart_version,generation,refreshed_at,source_max_at,row_counts,status)
                VALUES (%s,coalesce((SELECT generation+1 FROM public.pulse_home_mart_state WHERE mart_version=%s),1),now(),%s,%s::jsonb,'ready')
                ON CONFLICT(mart_version) DO UPDATE SET
                  generation=EXCLUDED.generation,refreshed_at=EXCLUDED.refreshed_at,
                  source_max_at=EXCLUDED.source_max_at,row_counts=EXCLUDED.row_counts,status='ready'
                """,
                (VERSION, VERSION, source_max_at, __import__("json").dumps(counts)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log(f"ИТОГ: витрины Главной v{VERSION} готовы | rows={sum(counts.values())} | errors=0 | elapsed={time.monotonic()-started:.1f}s", flush=True)
    return counts


def inventory_daily_rows(conn: Any, marketplace: str, date_from: str | None, date_to: str | None) -> list[dict[str, Any]] | None:
    if not relation_exists(conn, "mv_pulse_home_inventory_daily_v1"):
        return None
    clauses = ["marketplace=%s"]
    values: list[Any] = [marketplace]
    if date_from:
        clauses.append("report_date >= %s"); values.append(date_from)
    if date_to:
        clauses.append("report_date <= %s"); values.append(date_to)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            WITH selected AS (
              SELECT * FROM public.mv_pulse_home_inventory_daily_v1
              WHERE {' AND '.join(clauses)}
            ), movements AS (
              SELECT selected.*,
                report_date-lag(report_date) OVER(ORDER BY report_date) AS snapshot_gap_days,
                stock_available_qty-lag(stock_available_qty) OVER(ORDER BY report_date) AS stock_change_qty
              FROM selected
            )
            SELECT movements.*,greatest(stock_change_qty,0) AS stock_inflow_qty,
                   greatest(-stock_change_qty,0) AS stock_outflow_qty
            FROM movements ORDER BY report_date
            """,
            values,
        )
        return [dict(row) for row in cursor.fetchall()]


def category_payload(conn: Any, marketplace: str, start: str, end: str, limit: int = 5) -> dict[str, Any] | None:
    if not relation_exists(conn, "mv_pulse_home_category_daily_v1"):
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.category_name,sum(d.zakazano_rub)::numeric AS zakazano_rub,
                   sum(d.zakazano_sht)::numeric AS zakazano_sht,
                   s.sku_count,s.total_stock_qty,s.snapshot_date AS stock_snapshot_date
            FROM public.mv_pulse_home_category_daily_v1 d
            LEFT JOIN public.mv_pulse_home_category_stock_v1 s
              ON s.marketplace=d.marketplace AND s.category_name=d.category_name
            WHERE d.marketplace=%s AND d.report_date BETWEEN %s AND %s
            GROUP BY d.category_name,s.sku_count,s.total_stock_qty,s.snapshot_date
            ORDER BY sum(d.zakazano_rub) DESC NULLS LAST,d.category_name
            LIMIT %s
            """,
            (marketplace, start, end, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    columns = ["category_name", "total_stock_qty", "sku_count", "zakazano_sht", "zakazano_rub"]
    return {
        "rows": rows, "columns": [{"key": key, "label": key, "type": "text" if key == "category_name" else "number"} for key in columns],
        "page": 1, "page_size": limit, "total": len(rows), "total_pages": 1,
        "sort_col": "zakazano_rub", "sort_dir": "desc", "source": "mv_pulse_home_category_daily_v1",
    }


def _statement(totals: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    revenue = _n(totals.get("revenue"))
    rows = [
        ("revenue", "Продажи и возвраты", revenue, "total"),
        ("commission", "Комиссия площадки", -abs(_n(components.get("commission")) or 0), "expense"),
        ("acquiring", "Эквайринг", -abs(_n(components.get("acquiring")) or 0), "expense"),
        ("logistics", "Логистика и возвраты", -abs(_n(components.get("logistics")) or 0), "expense"),
        ("storage", "Хранение", -abs(_n(components.get("storage")) or 0), "expense"),
        ("cogs", "Себестоимость (факт / цена ÷ 3)", -abs(_n(totals.get("cogs")) or 0), "expense"),
        ("manual_expenses", "Дополнительные расходы", -abs(_n(totals.get("manual_expenses")) or 0), "expense"),
        ("vat_model", "НДС модели", -abs(_n(totals.get("vat")) or 0), "expense"),
        ("tax", "Налог модели", -abs(_n(totals.get("tax")) or 0) if totals.get("tax") is not None else None, "expense"),
        ("management_result", "Результат модели", _n(totals.get("management_result")), "total"),
    ]
    return [
        {"key": key, "label": label, "amount": amount, "kind": kind,
         "revenue_pct": amount / revenue * 100 if amount is not None and revenue else None,
         **({"is_estimated": True} if key == "cogs" and (totals.get("estimated_sku_count") or 0) else {})}
        for key, label, amount, kind in rows
    ]


def home_pl_payload(config: dict[str, Any], start: str, end: str, client: str, marketplace: str) -> dict[str, Any] | None:
    from km_trade_finance import connect_km, get_global_settings
    from pulse_financial_model import finance_model_schema_available, read_expenses

    with connect_km(config) as conn, conn.cursor() as cursor:
        if marketplace == "yandex":
            relation = "mv_pulse_home_yandex_finance_daily_v1"
            if not relation_exists(conn, relation): return None
            cursor.execute(
                f"""SELECT min(report_date) available_from,max(report_date) available_to,
                    sum(revenue) revenue,sum(units) units,sum(marketplace_net) marketplace_net,
                    sum(missing_amount) missing_amount,sum(source_rows) source_rows
                    FROM public.{relation} WHERE client_key=%s AND report_date BETWEEN %s AND %s""",
                (client, start, end),
            )
            row = dict(cursor.fetchone() or {})
            unknown = int(row.get("missing_amount") or 0) > 0
            revenue = None if unknown else _n(row.get("revenue"))
            net = None if unknown else _n(row.get("marketplace_net"))
            totals = {"revenue": revenue, "seller_revenue": revenue, "units": _n(row.get("units")),
                      "marketplace_net": net, "ozon_costs": revenue-net if revenue is not None and net is not None else None,
                      "cogs": None, "gross_profit": None, "management_result": None, "net_profit": None,
                      "profit_ready": False, "tax_configured": False}
            return {"client": client, "marketplace": "yandex", "available": bool(row.get("source_rows")),
                    "partial": unknown, "date_from": start, "date_to": end,
                    "available_date_from": str(row.get("available_from") or ""),
                    "available_date_to": str(row.get("available_to") or ""), "totals": totals,
                    "statement": _statement(totals, {}), "products": [], "monthly": [], "months": [],
                    "model_read_only": True, "home_mart": True,
                    "model_notice": "Яндекс Маркет: реализации, услуги, компенсации и сторно. Себестоимость и налоги не подтверждены."}
        if marketplace not in {"wb", "ozon"} or not relation_exists(conn, "mv_pulse_home_finance_daily_v2"):
            return None
        cursor.execute(
            """
            SELECT min(report_date) available_from,max(report_date) available_to,
                   sum(source_rows) source_rows,sum(revenue) revenue,sum(units) units,
                   sum(marketplace_net) marketplace_net,sum(commission) commission,
                   sum(acquiring) acquiring,sum(logistics) logistics,sum(storage) storage,
                   sum(acceptance) acceptance,sum(deduction) deduction,sum(penalty) penalty,
                   sum(additional_payment) additional_payment,sum(cashback) cashback,
                   sum(rebill_logistics) rebill_logistics,sum(cogs) cogs,
                   sum(seller_unit_costs) seller_unit_costs,
                   max(estimated_sku_count) estimated_sku_count
            FROM public.mv_pulse_home_finance_daily_v2
            WHERE marketplace=%s AND report_date BETWEEN %s AND %s
            """,
            (marketplace, start, end),
        )
        row = dict(cursor.fetchone() or {})
        if not row.get("source_rows"):
            return None
        revenue = Decimal(str(row.get("revenue") or 0)); net = Decimal(str(row.get("marketplace_net") or 0))
        cogs = Decimal(str(row.get("cogs") or 0)); seller = Decimal(str(row.get("seller_unit_costs") or 0))
        settings: dict[str, Any] = {}; expenses: list[dict[str, Any]] = []
        if finance_model_schema_available(cursor):
            settings = get_global_settings(cursor)
            expenses = read_expenses(cursor, marketplace, date.fromisoformat(start), date.fromisoformat(end))
        manual = sum((Decimal(str(item["amount"])) for item in expenses), Decimal(0))
        tax_pct = settings.get("tax_pct"); vat_pct = Decimal(str(settings.get("vat_pct") or 0))
        vat = revenue * vat_pct / 100
        tax = revenue * Decimal(str(tax_pct)) / 100 if tax_pct is not None else None
        before = net - cogs - seller - manual - vat
        result = before - tax if tax is not None else before
        totals = {
            "revenue": _n(revenue), "seller_revenue": _n(revenue), "units": _n(row.get("units")),
            "marketplace_net": _n(net), "ozon_costs": _n(revenue-net), "cogs": _n(cogs),
            "gross_profit": _n(revenue-cogs), "seller_unit_costs": _n(seller),
            "manual_expenses": _n(manual), "vat": _n(vat), "tax": _n(tax),
            "profit_before_tax": _n(before), "management_result": _n(result),
            "management_margin_pct": _n(result/revenue*100) if revenue else None,
            "net_profit": _n(result) if tax is not None else None,
            "margin_pct": _n(result/revenue*100) if revenue and tax is not None else None,
            "profit_ready": tax is not None, "tax_configured": tax is not None,
            "estimated_sku_count": int(row.get("estimated_sku_count") or 0),
            "result_basis": "after_configured_taxes" if tax is not None else "before_income_tax",
        }
        available_from = str(row.get("available_from") or ""); available_to = str(row.get("available_to") or "")
        return {"ok": True, "available": True, "partial": available_from > start or available_to < end,
                "client": client, "marketplace": marketplace, "date_from": start, "date_to": end,
                "available_date_from": available_from, "available_date_to": available_to,
                "period": {"date_from": start, "date_to": end, "available_from": available_from, "available_to": available_to},
                "totals": totals, "statement": _statement(totals, row), "products": [], "monthly": [], "months": [],
                "expenses": [item for item in expenses if item["date_from"] == start and item["date_to"] == end],
                "all_expenses": expenses, "model_read_only": True, "home_mart": True,
                "model_notice": "Расчётная себестоимость = цена / 3, если фактическая не задана. Это управленческая оценка."}


def writer_connection(database: str):
    password = Path(os.environ["PULSE_WRITER_PASSWORD_FILE"]).read_text(encoding="utf-8").strip()
    return psycopg2.connect(host="pulse_postgres", port=5432, database=database,
                            user="pulse_writer", password=password,
                            cursor_factory=RealDictCursor, connect_timeout=10)


def bump_cache_generation() -> None:
    cache_dir = Path(os.environ.get("PULSE_REPORT_CACHE_DIR", "/var/lib/pulse/report_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "home-marts-generation"
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(str(time.time_ns()), encoding="utf-8")
    os.replace(temporary, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", nargs="+", required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not (args.install or args.refresh):
        parser.error("Specify --install and/or --refresh")
    overall = time.monotonic()
    print(f"ПЛАН: клиенты={len(args.clients)} | install={int(args.install)} refresh={int(args.refresh)} | marts={len(MARTS)}", flush=True)
    for index, client in enumerate(args.clients, 1):
        with writer_connection(client) as conn:
            if args.install:
                install(conn); conn.commit()
            if args.refresh:
                refresh(conn)
        print(f"[{index}/{len(args.clients)}] {client}: витрины Главной готовы | errors=0", flush=True)
    bump_cache_generation()
    print(f"ИТОГ: clients={len(args.clients)} errors=0 elapsed={time.monotonic()-overall:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
