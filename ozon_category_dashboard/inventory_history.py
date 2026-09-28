#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared inventory-history storage and dashboard payloads.

The table intentionally stores end-of-day/API snapshots.  Positive and
negative movements are derived from consecutive snapshots and are not claimed
to be an accounting movement ledger.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode

from psycopg2 import sql
from psycopg2.extras import execute_values


TABLE_NAME = "inventory_history_daily"

NUMERIC_FIELDS = (
    "stock_available_qty",
    "stock_preparing_qty",
    "stock_reserved_qty",
    "stock_total_qty",
    "to_customer_qty",
    "from_customer_qty",
    "stock_value_rub",
    "turnover_days",
    "days_to_stockout",
    "lost_orders_qty",
    "lost_orders_rub",
    "in_supply_orders_qty",
    "in_transit_supply_qty",
    "returning_from_customers_qty",
    "checking_qty",
    "defective_qty",
    "expiring_qty",
    "preparing_to_remove_qty",
    "marked_qty",
)


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS public.inventory_snapshot_runs (
          marketplace text NOT NULL, snapshot_date date NOT NULL, source_ref text NOT NULL,
          endpoint text NOT NULL, row_count integer NOT NULL, sku_count integer NOT NULL,
          captured_at timestamptz NOT NULL DEFAULT now(), status text NOT NULL DEFAULT 'complete',
          PRIMARY KEY (marketplace,snapshot_date,endpoint))""")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.inventory_history_daily (
              marketplace text NOT NULL,
              snapshot_date date NOT NULL,
              sku text NOT NULL,
              seller_article text,
              product_name text,
              category_name text,
              warehouse_name text NOT NULL DEFAULT '',
              cluster_name text NOT NULL DEFAULT '',
              stock_available_qty numeric,
              stock_preparing_qty numeric,
              stock_reserved_qty numeric,
              stock_total_qty numeric,
              to_customer_qty numeric,
              from_customer_qty numeric,
              stock_value_rub numeric,
              turnover_days numeric,
              days_to_stockout numeric,
              lost_orders_qty numeric,
              lost_orders_rub numeric,
              in_supply_orders_qty numeric,
              in_transit_supply_qty numeric,
              returning_from_customers_qty numeric,
              checking_qty numeric,
              defective_qty numeric,
              expiring_qty numeric,
              preparing_to_remove_qty numeric,
              marked_qty numeric,
              source_type text NOT NULL,
              source_ref text NOT NULL,
              imported_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_history_date "
            "ON public.inventory_history_daily (marketplace, snapshot_date)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_history_product "
            "ON public.inventory_history_daily (marketplace, product_name, seller_article, sku)"
        )


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_optional(values: Iterable[Any]) -> float | None:
    parsed = [_number(value) for value in values]
    present = [value for value in parsed if value is not None]
    return sum(present) if present else None


def upsert_rows(conn: Any, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    columns = [
        "marketplace",
        "snapshot_date",
        "sku",
        "seller_article",
        "product_name",
        "category_name",
        "warehouse_name",
        "cluster_name",
        *NUMERIC_FIELDS,
        "source_type",
        "source_ref",
    ]
    values = [
        tuple(
            row.get(column)
            if column not in {"warehouse_name", "cluster_name"}
            else str(row.get(column) or "")
            for column in columns
        )
        for row in rows
    ]
    assignments = ", ".join(
        f"{column}=EXCLUDED.{column}"
        for column in columns
        if column not in {"marketplace", "snapshot_date", "sku", "warehouse_name", "cluster_name"}
    )
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""
            INSERT INTO public.inventory_history_daily ({", ".join(columns)})
            VALUES %s
            ON CONFLICT (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            DO UPDATE SET {assignments}, imported_at=now()
            """,
            values,
            page_size=2000,
        )
    return len(values)


def store_ozon_api_snapshot(
    conn: Any,
    rows: list[dict[str, Any]],
    snapshot_date: date,
    source_ref: str,
) -> int:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        warehouse = str(row.get("warehouse_name") or "")
        cluster = str(row.get("cluster_name") or "")
        key = (sku, warehouse, cluster)
        target = grouped.setdefault(
            key,
            {
                "marketplace": "ozon",
                "snapshot_date": snapshot_date,
                "sku": sku,
                "seller_article": row.get("article"),
                "product_name": row.get("product_name"),
                "category_name": row.get("category_name"),
                "warehouse_name": warehouse,
                "cluster_name": cluster,
                "stock_available_qty": 0,
                "stock_preparing_qty": 0,
                "stock_reserved_qty": 0,
                "source_type": "ozon_seller_api",
                "source_ref": source_ref,
            },
        )
        target["seller_article"] = target.get("seller_article") or row.get("article")
        target["product_name"] = target.get("product_name") or row.get("product_name")
        target["stock_available_qty"] += _number(row.get("available_to_sell")) or 0
        target["stock_preparing_qty"] += _number(row.get("preparing_to_sell")) or 0
        target["stock_reserved_qty"] += _number(row.get("reserved")) or 0
    for target in grouped.values():
        target["stock_total_qty"] = _sum_optional(
            (
                target.get("stock_available_qty"),
                target.get("stock_preparing_qty"),
                target.get("stock_reserved_qty"),
            )
        )
    if not grouped:
        raise ValueError('Empty Ozon stock snapshot: previous history preserved')
    endpoint = '/v2/analytics/stock_on_warehouses' if '/v2/analytics/stock_on_warehouses' in source_ref else '/v4/product/info/stocks'
    ensure_schema(conn)
    # A daily layer is one complete source snapshot, never a sum of v2 warehouses
    # and v4 fulfilment types. Retry replaces this day inside the caller transaction.
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.inventory_history_daily WHERE marketplace='ozon' AND snapshot_date=%s", (snapshot_date,))
    count = upsert_rows(conn, list(grouped.values()))
    if count:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO public.inventory_snapshot_runs
              (marketplace,snapshot_date,source_ref,endpoint,row_count,sku_count)
              VALUES ('ozon',%s,%s,%s,%s,%s)
              ON CONFLICT (marketplace,snapshot_date,endpoint) DO UPDATE SET
              source_ref=EXCLUDED.source_ref,row_count=EXCLUDED.row_count,
              sku_count=EXCLUDED.sku_count,captured_at=now(),status='complete'""",
              (snapshot_date,source_ref,endpoint,count,len({x[0] for x in grouped})))
    return count


def store_ozon_current_tables_snapshot(
    conn: Any,
    snapshot_date: date,
    source_ref: str,
) -> int:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.inventory_history_daily (
              marketplace, snapshot_date, sku, seller_article, product_name,
              warehouse_name, cluster_name,
              stock_available_qty, stock_preparing_qty, stock_reserved_qty, stock_total_qty,
              in_supply_orders_qty, in_transit_supply_qty, returning_from_customers_qty,
              checking_qty, defective_qty, expiring_qty, preparing_to_remove_qty, marked_qty,
              source_type, source_ref
            )
            SELECT
              'ozon', %s, sku, max(article), max(product_name),
              coalesce(warehouse_name, ''), coalesce(cluster_name, ''),
              sum(available_to_sell), sum(preparing_to_sell), NULL,
              sum(available_to_sell + preparing_to_sell),
              sum(in_supply_orders), sum(in_transit_supply), sum(returning_from_customers),
              sum(checking), sum(defective_from_supply + defective_from_stock),
              sum(expiring), sum(preparing_to_remove),
              sum(marked_awaiting_removal + marked_awaiting_upd),
              'ozon_stock_workbook', %s
            FROM public.ozon_stock_product_warehouses
            WHERE nullif(sku, '') IS NOT NULL
            GROUP BY sku, coalesce(warehouse_name, ''), coalesce(cluster_name, '')
            ON CONFLICT (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            DO UPDATE SET
              seller_article=EXCLUDED.seller_article,
              product_name=EXCLUDED.product_name,
              stock_available_qty=EXCLUDED.stock_available_qty,
              stock_preparing_qty=EXCLUDED.stock_preparing_qty,
              stock_reserved_qty=EXCLUDED.stock_reserved_qty,
              stock_total_qty=EXCLUDED.stock_total_qty,
              in_supply_orders_qty=EXCLUDED.in_supply_orders_qty,
              in_transit_supply_qty=EXCLUDED.in_transit_supply_qty,
              returning_from_customers_qty=EXCLUDED.returning_from_customers_qty,
              checking_qty=EXCLUDED.checking_qty,
              defective_qty=EXCLUDED.defective_qty,
              expiring_qty=EXCLUDED.expiring_qty,
              preparing_to_remove_qty=EXCLUDED.preparing_to_remove_qty,
              marked_qty=EXCLUDED.marked_qty,
              source_type=EXCLUDED.source_type,
              source_ref=EXCLUDED.source_ref,
              imported_at=now()
            """,
            (snapshot_date, source_ref),
        )
        return cur.rowcount


def store_wb_product_snapshot(
    conn: Any,
    snapshot_date: date,
    items: list[dict[str, Any]],
    source_ref: str,
) -> int:
    rows: list[dict[str, Any]] = []
    for item in items:
        metrics = item.get("metrics") or {}
        sku = str(item.get("nmID") or item.get("nmId") or "").strip()
        if not sku:
            continue
        turnover = metrics.get("avgStockTurnover") or {}
        sale_rate = metrics.get("saleRate") or {}

        def duration(value: Any) -> float | None:
            if not isinstance(value, dict):
                return _number(value)
            days = _number(value.get("days"))
            hours = _number(value.get("hours"))
            if days is None and hours is None:
                return None
            return (days or 0) + (hours or 0) / 24

        stock_qty = _number(metrics.get("stockCount"))
        rows.append(
            {
                "marketplace": "wb",
                "snapshot_date": snapshot_date,
                "sku": sku,
                "seller_article": item.get("vendorCode"),
                "product_name": item.get("name") or item.get("title"),
                "category_name": item.get("subjectName"),
                "warehouse_name": "",
                "cluster_name": "",
                "stock_available_qty": stock_qty,
                "stock_total_qty": stock_qty,
                "to_customer_qty": _number(metrics.get("toClientCount")),
                "from_customer_qty": _number(metrics.get("fromClientCount")),
                "stock_value_rub": _number(metrics.get("stockSum")),
                "turnover_days": duration(turnover),
                "days_to_stockout": duration(sale_rate),
                "lost_orders_qty": _number(metrics.get("lostOrdersCount")),
                "lost_orders_rub": _number(metrics.get("lostOrdersSum")),
                "source_type": "wb_stock_api",
                "source_ref": source_ref,
            }
        )
    return upsert_rows(conn, rows)


def publish_wb_stock_history_csv(
    conn: Any,
    date_from: date,
    date_to: date,
    *,
    replace: bool = False,
) -> int:
    """Publish normalized WB stock-history CSV rows to the dashboard store.

    The WB report contains one row per size and warehouse. The dashboard uses
    the product grain, so sizes are summed before the canonical upsert.
    """
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_inventory_history_daily_csv')")
        relation = cur.fetchone()
        relation_name = next(iter(relation.values())) if isinstance(relation, dict) else relation[0]
        if not relation_name:
            return 0
        if replace:
            cur.execute(
                """
                DELETE FROM public.inventory_history_daily
                WHERE marketplace = 'wb'
                  AND source_type = 'wb_stock_history_csv'
                  AND snapshot_date BETWEEN %s AND %s
                """,
                (date_from, date_to),
            )
        cur.execute(
            """
            INSERT INTO public.inventory_history_daily (
              marketplace, snapshot_date, sku, seller_article, product_name,
              category_name, warehouse_name, cluster_name,
              stock_available_qty, stock_total_qty, source_type, source_ref
            )
            SELECT
              'wb', history.snapshot_date, history.nm_id::text,
              max(coalesce(current_stock.seller_article, history.vendor_code)),
              max(current_stock.product_name), max(current_stock.category_name),
              coalesce(history.warehouse_name, ''), '',
              sum(coalesce(history.quantity, 0)), sum(coalesce(history.quantity, 0)),
              'wb_stock_history_csv',
              'database://public.wb_inventory_history_daily_csv'
            FROM public.wb_inventory_history_daily_csv AS history
            LEFT JOIN public.wb_stock_api_current AS current_stock
              ON current_stock.wb_nmid = history.nm_id::text
            WHERE history.snapshot_date BETWEEN %s AND %s
              AND history.nm_id IS NOT NULL
            GROUP BY history.snapshot_date, history.nm_id, coalesce(history.warehouse_name, '')
            ON CONFLICT (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            DO UPDATE SET
              seller_article = coalesce(EXCLUDED.seller_article, inventory_history_daily.seller_article),
              product_name = coalesce(EXCLUDED.product_name, inventory_history_daily.product_name),
              category_name = coalesce(EXCLUDED.category_name, inventory_history_daily.category_name),
              stock_available_qty = EXCLUDED.stock_available_qty,
              stock_total_qty = EXCLUDED.stock_total_qty,
              source_type = EXCLUDED.source_type,
              source_ref = EXCLUDED.source_ref,
              imported_at = now()
            """,
            (date_from, date_to),
        )
        return cur.rowcount


def publish_wb_current_stock_table(conn: Any) -> int:
    """Publish the normalized current WB stock table to inventory history."""
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_stock_api_current')")
        relation = cur.fetchone()
        relation_name = next(iter(relation.values())) if isinstance(relation, dict) else relation[0]
        if not relation_name:
            return 0
        cur.execute(
            """
            INSERT INTO public.inventory_history_daily (
              marketplace, snapshot_date, sku, seller_article, product_name,
              category_name, warehouse_name, cluster_name,
              stock_available_qty, stock_total_qty, to_customer_qty,
              from_customer_qty, stock_value_rub, source_type, source_ref
            )
            SELECT
              'wb', snapshot_date, wb_nmid, seller_article, product_name,
              category_name, '', '', stock_qty, stock_qty, to_customer_qty,
              from_customer_qty, stock_amount_rub,
              'wb_stock_api', 'database://public.wb_stock_api_current'
            FROM public.wb_stock_api_current
            WHERE nullif(wb_nmid, '') IS NOT NULL
            ON CONFLICT (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            DO UPDATE SET
              seller_article = EXCLUDED.seller_article,
              product_name = EXCLUDED.product_name,
              category_name = EXCLUDED.category_name,
              stock_available_qty = EXCLUDED.stock_available_qty,
              stock_total_qty = EXCLUDED.stock_total_qty,
              to_customer_qty = EXCLUDED.to_customer_qty,
              from_customer_qty = EXCLUDED.from_customer_qty,
              stock_value_rub = EXCLUDED.stock_value_rub,
              source_type = EXCLUDED.source_type,
              source_ref = EXCLUDED.source_ref,
              imported_at = now()
            """
        )
        return cur.rowcount


def publish_wb_stock_detail_snapshot(conn: Any, source_ref: str) -> int:
    """Publish a complete WB Stock.zip snapshot at SKU and warehouse grain."""
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT report_date
            FROM public.wb_stock_detail_raw
            WHERE report_date IS NOT NULL
            ORDER BY report_date
            """
        )
        snapshot_dates = [row[0] for row in cur.fetchall() if row and row[0]]
        if not snapshot_dates:
            return 0
        cur.execute(
            """
            DELETE FROM public.inventory_history_daily
            WHERE marketplace = 'wb'
              AND source_type = 'wb_stock_detail_zip'
              AND snapshot_date = ANY(%s)
            """,
            (snapshot_dates,),
        )
        cur.execute(
            """
            INSERT INTO public.inventory_history_daily (
              marketplace, snapshot_date, sku, seller_article, product_name,
              category_name, warehouse_name, cluster_name,
              stock_available_qty, stock_total_qty, to_customer_qty,
              from_customer_qty, stock_value_rub, source_type, source_ref
            )
            SELECT
              'wb', report_date, wb_nmid::text, max(seller_article), max(product_name),
              max(subject_name), coalesce(warehouse_name, ''), coalesce(region_name, ''),
              sum(coalesce(current_stock_qty, 0)), sum(coalesce(current_stock_qty, 0)),
              sum(coalesce(to_customer_qty, 0)), sum(coalesce(from_customer_qty, 0)),
              sum(coalesce(current_stock_rub, 0)),
              'wb_stock_detail_zip', %s
            FROM public.wb_stock_detail_raw
            WHERE wb_nmid IS NOT NULL
              AND report_date = ANY(%s)
            GROUP BY report_date, wb_nmid, coalesce(warehouse_name, ''), coalesce(region_name, '')
            ON CONFLICT (marketplace, snapshot_date, sku, warehouse_name, cluster_name)
            DO UPDATE SET
              seller_article = EXCLUDED.seller_article,
              product_name = EXCLUDED.product_name,
              category_name = EXCLUDED.category_name,
              stock_available_qty = EXCLUDED.stock_available_qty,
              stock_total_qty = EXCLUDED.stock_total_qty,
              to_customer_qty = EXCLUDED.to_customer_qty,
              from_customer_qty = EXCLUDED.from_customer_qty,
              stock_value_rub = EXCLUDED.stock_value_rub,
              source_type = EXCLUDED.source_type,
              source_ref = EXCLUDED.source_ref,
              imported_at = now()
            """,
            (source_ref, snapshot_dates),
        )
        return cur.rowcount


def _table_exists(conn: Any) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.inventory_history_daily')")
        row = cur.fetchone()
        value = next(iter(row.values())) if isinstance(row, dict) else row[0]
        return bool(value)


def _stock_where(parsed: Any, marketplace: str) -> tuple[str, list[Any]]:
    params = parse_qs(parsed.query)
    clauses = ["marketplace = %s"]
    values: list[Any] = [marketplace]
    date_from = params.get("date_from", [""])[0]
    date_to = params.get("date_to", [""])[0]
    product = params.get("product", [""])[0].strip()
    article = params.get("article", [""])[0].strip()
    categories = [value for value in params.get("categories", []) if value]
    inventory_skus = [value.strip() for value in params.get("inventory_skus", []) if value.strip()]
    if inventory_skus:
        clauses.append("sku = ANY(%s)")
        values.append(inventory_skus)
    if date_from:
        clauses.append("snapshot_date >= %s")
        values.append(date_from)
    if date_to:
        clauses.append("snapshot_date <= %s")
        values.append(date_to)
    if product:
        clauses.append("product_name = %s")
        values.append(product)
    if article:
        clauses.append(
            "(sku ILIKE %s OR coalesce(seller_article, '') ILIKE %s OR coalesce(product_name, '') ILIKE %s)"
        )
        pattern = f"%{article}%"
        values.extend((pattern, pattern, pattern))
    if categories:
        clauses.append("category_name = ANY(%s)")
        values.append(categories)
    return " AND ".join(clauses), values



def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso_date(value: Any) -> str:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else ""


def _date_range(start: date | None, end: date | None) -> list[date]:
    if not start or not end or end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def snapshot_coverage(parsed: Any, get_conn: Callable[[], Any], marketplace: str) -> dict[str, Any]:
    """Describe observed snapshot dates without filling gaps."""
    params = parse_qs(parsed.query)
    requested_from = _date_value(params.get("date_from", [""])[0])
    requested_to = _date_value(params.get("date_to", [""])[0])
    with get_conn() as conn:
        if not _table_exists(conn):
            return {
                "date_from": _iso_date(requested_from),
                "date_to": _iso_date(requested_to),
                "expected_days": 0,
                "observed_days": 0,
                "coverage_pct": 0,
                "observed_dates": [],
                "missing_dates": [],
                "latest_snapshot_date": "",
                "previous_snapshot_date": "",
                "interpolated": False,
            }
        clauses = ["marketplace = %s"]
        values: list[Any] = [marketplace]
        if requested_from:
            clauses.append("snapshot_date >= %s")
            values.append(requested_from)
        if requested_to:
            clauses.append("snapshot_date <= %s")
            values.append(requested_to)
        previous_snapshot_date = None
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT snapshot_date
                FROM public.inventory_history_daily
                WHERE {' AND '.join(clauses)}
                ORDER BY snapshot_date
                """,
                values,
            )
            observed = [_date_value(dict(row).get("snapshot_date")) for row in cur.fetchall()]
            latest_observed = next((value for value in reversed(observed) if value), None)
            if latest_observed:
                cur.execute(
                    """
                    SELECT max(snapshot_date) AS previous_snapshot_date
                    FROM public.inventory_history_daily
                    WHERE marketplace = %s AND snapshot_date < %s
                    """,
                    (marketplace, latest_observed),
                )
                previous_snapshot_date = _date_value(
                    dict(cur.fetchone() or {}).get("previous_snapshot_date")
                )
    observed = [value for value in observed if value]
    range_from = requested_from or (observed[0] if observed else None)
    range_to = requested_to or (observed[-1] if observed else None)
    expected = _date_range(range_from, range_to)
    observed_set = set(observed)
    missing = [value for value in expected if value not in observed_set]
    return {
        "date_from": _iso_date(range_from),
        "date_to": _iso_date(range_to),
        "expected_days": len(expected),
        "observed_days": len(observed_set),
        "coverage_pct": round(len(observed_set) / len(expected) * 100, 1) if expected else 0,
        "observed_dates": [_iso_date(value) for value in observed],
        "missing_dates": [_iso_date(value) for value in missing],
        "latest_snapshot_date": _iso_date(observed[-1] if observed else None),
        "previous_snapshot_date": _iso_date(previous_snapshot_date),
        "interpolated": False,
    }


def stock_status(current_qty: float, inbound_qty: float, avg_daily_units: float) -> str:
    supply_qty = max(current_qty, 0) + max(inbound_qty, 0)
    demand_14d = max(avg_daily_units, 0) * 14
    if current_qty <= 0 and avg_daily_units > 0:
        return "oos"
    if demand_14d > supply_qty:
        return "shortage"
    if avg_daily_units <= 0:
        return "no_sales" if supply_qty > 0 else "normal"
    if supply_qty / avg_daily_units > 60:
        return "excess"
    return "normal"


def derive_product_metrics(row: dict[str, Any]) -> dict[str, Any]:
    current_present = bool(row.get("current_snapshot_present"))
    previous_present = bool(row.get("previous_snapshot_present"))
    demand_30d = _number(row.get("demand_30d_units")) or 0
    demand_days = int(_number(row.get("demand_window_days")) or 0)
    avg_daily = demand_30d / demand_days if demand_days else 0
    current_qty = _number(row.get("current_qty"))
    previous_qty = _number(row.get("previous_qty"))
    # A missing current row is zero only when recent demand or a prior snapshot
    # corroborates that the SKU belongs to the monitored assortment.
    current_qty = current_qty if current_qty is not None else (0 if demand_30d > 0 or previous_present else 0)
    previous_qty = previous_qty if previous_qty is not None else 0
    inbound_components = (
        row.get("stock_preparing_qty"),
        row.get("in_supply_orders_qty"),
        row.get("in_transit_supply_qty"),
    )
    inbound_available = any(value is not None for value in inbound_components)
    inbound_qty = sum(_number(value) or 0 for value in inbound_components)
    # Undated API inbound cannot prevent an early stockout.
    supply_qty = max(current_qty, 0)
    demand_14d = avg_daily * 14
    demand_60d = avg_daily * 60
    days_cover = supply_qty / avg_daily if avg_daily > 0 else None
    status = stock_status(current_qty, 0, avg_daily)
    action = {
        "oos": "Срочно пополнить; проверить доступность и остановить неэффективное продвижение до прихода.",
        "shortage": "Запланировать поставку минимум на дефицит 14 дней.",
        "normal": "Поддерживать текущий темп поставок и контролировать покрытие.",
        "excess": "Не наращивать запас; ускорять продажи только при приемлемой марже.",
        "no_sales": "Запас без продаж: проверить доступность карточки, цену и рекламу; новые поставки остановить.",
    }[status]
    snapshot_date = _date_value(row.get("snapshot_date"))
    previous_snapshot_date = _date_value(row.get("previous_snapshot_date"))
    snapshot_gap_days = (snapshot_date - previous_snapshot_date).days if snapshot_date and previous_snapshot_date else None
    return {
        "sku": str(row.get("sku") or ""),
        "seller_article": row.get("seller_article") or "",
        "product_name": row.get("product_name") or "Без названия",
        "category_name": row.get("category_name") or "Без категории",
        "snapshot_date": _iso_date(snapshot_date),
        "previous_snapshot_date": _iso_date(previous_snapshot_date),
        "snapshot_gap_days": snapshot_gap_days,
        "current_qty": round(current_qty, 2),
        "previous_qty": round(previous_qty, 2) if previous_present else None,
        "delta_qty": round(current_qty - previous_qty, 2) if previous_present else None,
        "demand_30d_units": round(demand_30d, 2),
        "demand_window_days": demand_days,
        "demand_through_date": _iso_date(row.get("demand_through_date")),
        "avg_daily_units": round(avg_daily, 3),
        "days_cover": round(days_cover, 1) if days_cover is not None else None,
        "stock_preparing_qty": _number(row.get("stock_preparing_qty")),
        "in_supply_orders_qty": _number(row.get("in_supply_orders_qty")),
        "in_transit_supply_qty": _number(row.get("in_transit_supply_qty")),
        "inbound_qty": round(inbound_qty, 2) if inbound_available else None,
        "inbound_data_available": inbound_available,
        "demand_14d": round(demand_14d, 2),
        "shortage_14d": round(max(demand_14d - supply_qty, 0), 2),
        "excess_over_60d": round(max(supply_qty - demand_60d, 0), 2),
        "status": status,
        "recommended_action": action,
        "current_snapshot_present": current_present,
        "previous_snapshot_present": previous_present,
        "catalog_present": bool(row.get("catalog_present")),
    }


def _matches_product_filters(row: dict[str, Any], params: dict[str, list[str]]) -> bool:
    product = params.get("product", [""])[0].strip().casefold()
    article = params.get("article", [""])[0].strip().casefold()
    q = params.get("q", [""])[0].strip().casefold()
    categories = {value.strip().casefold() for value in params.get("categories", []) if value.strip()}
    if product and str(row.get("product_name") or "").casefold() != product:
        return False
    if categories and str(row.get("category_name") or "").casefold() not in categories:
        return False
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("sku", "seller_article", "product_name", "category_name")
    ).casefold()
    if article and article not in haystack:
        return False
    if q and q not in haystack:
        return False
    return True


def product_monitoring_rows(
    parsed: Any,
    get_conn: Callable[[], Any],
    marketplace: str,
    funnel_view: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coverage = snapshot_coverage(parsed, get_conn, marketplace)
    latest_date = _date_value(coverage.get("latest_snapshot_date"))
    previous_date = _date_value(coverage.get("previous_snapshot_date"))
    if not latest_date:
        return [], coverage
    with get_conn() as conn:
        if not _table_exists(conn):
            return [], coverage
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT min(report_date) AS first_date, max(report_date) AS through_date FROM public.{} WHERE report_date <= %s").format(sql.Identifier(funnel_view)),
                (latest_date,),
            )
            demand_bounds = dict(cur.fetchone() or {})
            demand_through = _date_value(demand_bounds.get("through_date"))
            first_demand_date = _date_value(demand_bounds.get("first_date"))
            if demand_through:
                demand_from = max(demand_through - timedelta(days=29), first_demand_date or demand_through)
                demand_window_days = (demand_through - demand_from).days + 1
            else:
                demand_from = latest_date
                demand_through = latest_date
                demand_window_days = 0
            catalog_cte = sql.SQL(
                """
                catalog AS (
                  SELECT DISTINCT ON (sku)
                    sku,
                    artikul AS seller_article,
                    nazvanie_tovara AS product_name,
                    category_name
                  FROM public.ozon_cat_products
                  WHERE nullif(sku, '') IS NOT NULL
                  ORDER BY sku, updated_at DESC NULLS LAST, imported_at DESC NULLS LAST
                )
                """
            ) if marketplace == "ozon" else sql.SQL(
                """
                catalog AS (
                  SELECT sku,
                    current_article AS seller_article,
                    current_product_name AS product_name,
                    current_category_name AS category_name
                  FROM stock
                )
                """
            )
            query = sql.SQL(
                """
                WITH stock AS (
                  SELECT
                    sku,
                    max(seller_article) FILTER (WHERE snapshot_date = %(latest_date)s) AS current_article,
                    max(product_name) FILTER (WHERE snapshot_date = %(latest_date)s) AS current_product_name,
                    max(category_name) FILTER (WHERE snapshot_date = %(latest_date)s) AS current_category_name,
                    count(*) FILTER (WHERE snapshot_date = %(latest_date)s) > 0 AS current_snapshot_present,
                    count(*) FILTER (WHERE snapshot_date = %(previous_date)s) > 0 AS previous_snapshot_present,
                    sum(stock_available_qty) FILTER (WHERE snapshot_date = %(latest_date)s) AS current_qty,
                    sum(stock_available_qty) FILTER (WHERE snapshot_date = %(previous_date)s) AS previous_qty,
                    sum(stock_preparing_qty) FILTER (WHERE snapshot_date = %(latest_date)s) AS stock_preparing_qty,
                    sum(in_supply_orders_qty) FILTER (WHERE snapshot_date = %(latest_date)s) AS in_supply_orders_qty,
                    sum(in_transit_supply_qty) FILTER (WHERE snapshot_date = %(latest_date)s) AS in_transit_supply_qty
                  FROM public.inventory_history_daily
                  WHERE marketplace = %(marketplace)s
                    AND snapshot_date = ANY(%(snapshot_dates)s)
                  GROUP BY sku
                ),
                {catalog_cte},
                funnel_meta AS (
                  -- Ozon catalog and current stock already carry product metadata.
                  -- Keep the funnel fallback only for WB so an Ozon inventory
                  -- request does not sort the entire multi-million-row history.
                  SELECT DISTINCT ON (sku)
                    sku,
                    seller_article,
                    product_name,
                    category_name
                  FROM public.{funnel_view}
                  WHERE %(marketplace)s = 'wb'
                    AND report_date BETWEEN %(demand_from)s AND %(demand_through)s
                    AND nullif(sku, '') IS NOT NULL
                  ORDER BY sku, report_date DESC
                ),
                demand AS (
                  SELECT
                    sku,
                    sum(coalesce(ordered_units, 0)) AS demand_30d_units
                  FROM public.{funnel_view}
                  WHERE report_date BETWEEN %(demand_from)s AND %(demand_through)s
                    AND nullif(sku, '') IS NOT NULL
                  GROUP BY sku
                ),
                universe AS (
                  SELECT sku FROM catalog
                  UNION
                  SELECT sku FROM stock WHERE current_snapshot_present
                )
                SELECT
                  u.sku,
                  coalesce(s.current_article, c.seller_article, fm.seller_article) AS seller_article,
                  coalesce(s.current_product_name, c.product_name, fm.product_name) AS product_name,
                  coalesce(s.current_category_name, c.category_name, fm.category_name) AS category_name,
                  %(latest_date)s AS snapshot_date,
                  %(previous_date)s AS previous_snapshot_date,
                  coalesce(s.current_snapshot_present, false) AS current_snapshot_present,
                  coalesce(s.previous_snapshot_present, false) AS previous_snapshot_present,
                  c.sku IS NOT NULL AS catalog_present,
                  s.current_qty,
                  s.previous_qty,
                  s.stock_preparing_qty,
                  s.in_supply_orders_qty,
                  s.in_transit_supply_qty,
                  coalesce(d.demand_30d_units, 0) AS demand_30d_units,
                  %(demand_window_days)s AS demand_window_days,
                  %(demand_through)s AS demand_through_date
                FROM universe u
                LEFT JOIN catalog c ON c.sku = u.sku
                LEFT JOIN stock s ON s.sku = u.sku
                LEFT JOIN funnel_meta fm ON fm.sku = u.sku
                LEFT JOIN demand d ON d.sku = u.sku
                """
            ).format(funnel_view=sql.Identifier(funnel_view), catalog_cte=catalog_cte)
            cur.execute(
                query,
                {
                    "marketplace": marketplace,
                    "latest_date": latest_date,
                    "previous_date": previous_date,
                    "snapshot_dates": [value for value in (latest_date, previous_date) if value],
                    "demand_from": demand_from,
                    "demand_through": demand_through,
                    "demand_window_days": demand_window_days,
                },
            )
            source_rows = [dict(row) for row in cur.fetchall()]
    params = parse_qs(parsed.query)
    rows = [derive_product_metrics(row) for row in source_rows]
    rows = [row for row in rows if _matches_product_filters(row, params)]
    status_priority = {"oos": 0, "shortage": 1, "normal": 2, "excess": 3, "no_sales": 4}
    rows.sort(
        key=lambda row: (
            status_priority.get(str(row.get("status")), 9),
            -(_number(row.get("shortage_14d")) or 0),
            str(row.get("product_name") or ""),
        )
    )
    return rows, coverage


def summarize_inbound(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inbound_data_available = any(bool(row.get("inbound_data_available")) for row in rows)

    def sum_if_available(field: str) -> float | None:
        values = [_number(row.get(field)) for row in rows]
        available = [value for value in values if value is not None]
        return round(sum(available), 2) if available else None

    return {
        "inbound_data_available": inbound_data_available,
        "inbound_qty": (
            round(sum(_number(row.get("inbound_qty")) or 0 for row in rows), 2)
            if inbound_data_available
            else None
        ),
        "stock_preparing_qty": sum_if_available("stock_preparing_qty"),
        "in_supply_orders_qty": sum_if_available("in_supply_orders_qty"),
        "in_transit_supply_qty": sum_if_available("in_transit_supply_qty"),
    }


def handle_products(
    parsed: Any,
    get_conn: Callable[[], Any],
    marketplace_from_query: Callable[[str], str],
    funnel_view_for_marketplace: Callable[[str], str],
) -> dict[str, Any]:
    marketplace = marketplace_from_query(parsed.query)
    rows, coverage = product_monitoring_rows(
        parsed,
        get_conn,
        marketplace,
        funnel_view_for_marketplace(marketplace),
    )
    total_current = sum(_number(row.get("current_qty")) or 0 for row in rows)
    previous_rows = [row for row in rows if row.get("previous_qty") is not None]
    total_previous = sum(_number(row.get("previous_qty")) or 0 for row in previous_rows)
    total_avg_daily = sum(_number(row.get("avg_daily_units")) or 0 for row in rows)
    inbound_summary = summarize_inbound(rows)
    total_inbound = _number(inbound_summary.get("inbound_qty")) or 0
    snapshot_sku_count = sum(1 for row in rows if row.get("current_snapshot_present"))
    catalog_sku_count = sum(1 for row in rows if row.get("catalog_present"))
    catalog_only_sku_count = sum(
        1 for row in rows if row.get("catalog_present") and not row.get("current_snapshot_present")
    )
    stock_only_sku_count = sum(
        1 for row in rows if row.get("current_snapshot_present") and not row.get("catalog_present")
    )
    status_counts = {
        status: sum(1 for row in rows if row.get("status") == status)
        for status in ("oos", "shortage", "normal", "excess", "no_sales")
    }
    confirmed_oos_sku_count = sum(
        1 for row in rows if row.get("status") == "oos" and row.get("current_snapshot_present")
    )
    inferred_oos_sku_count = sum(
        1 for row in rows if row.get("status") == "oos" and not row.get("current_snapshot_present")
    )
    return {
        "rows": rows,
        "product_names": sorted({str(row.get("product_name") or "") for row in rows if row.get("product_name")}),
        "category_names": sorted({str(row.get("category_name") or "") for row in rows if row.get("category_name")}),
        "marketplace": marketplace,
        "summary": {
            "snapshot_date": coverage.get("latest_snapshot_date") or "",
            "previous_snapshot_date": coverage.get("previous_snapshot_date") or "",
            "snapshot_gap_days": rows[0].get("snapshot_gap_days") if rows else None,
            "monitoring_sku_count": len(rows),
            "snapshot_sku_count": snapshot_sku_count,
            "catalog_sku_count": catalog_sku_count,
            "catalog_only_sku_count": catalog_only_sku_count,
            "stock_only_sku_count": stock_only_sku_count,
            "total_stock_qty": round(total_current, 2),
            "stock_change_qty": round(total_current - total_previous, 2) if previous_rows else None,
            "inbound_qty": inbound_summary["inbound_qty"],
            "inbound_data_available": inbound_summary["inbound_data_available"],
            "stock_preparing_qty": inbound_summary["stock_preparing_qty"],
            "in_supply_orders_qty": inbound_summary["in_supply_orders_qty"],
            "in_transit_supply_qty": inbound_summary["in_transit_supply_qty"],
            "days_cover": round(total_current / total_avg_daily, 1) if total_avg_daily > 0 else None,
            "sku_count": len(rows),
            "oos_sku_count": status_counts["oos"],
            "confirmed_oos_sku_count": confirmed_oos_sku_count,
            "inferred_oos_sku_count": inferred_oos_sku_count,
            "shortage_sku_count": status_counts["shortage"],
            "normal_sku_count": status_counts["normal"],
            "excess_sku_count": status_counts["excess"],
            "no_sales_sku_count": status_counts["no_sales"],
        },
        "coverage": coverage,
        "thresholds": {"shortage_days": 14, "excess_days": 60, "demand_window_days": 30},
        "demand_through_date": rows[0].get("demand_through_date") if rows else "",
        "demand_window_days": rows[0].get("demand_window_days") if rows else 0,
        "definitions": {
            "days_cover": "физически доступный остаток / среднесуточный спрос; объём API без даты прихода не увеличивает покрытие",
            "shortage_14d": "дефицит физического остатка до спроса на 14 дней; подтверждённые ETA учитываются отдельно в разделе поставок",
            "excess_over_60d": "запас сверх спроса на 60 дней",
            "snapshot_gap": "дни без снимка не заполняются и не интерполируются",
        },
    }

def stock_daily_rows(parsed: Any, get_conn: Callable[[], Any], marketplace: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if not _table_exists(conn):
            return []
        where, values = _stock_where(parsed, marketplace)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH daily AS (
                  SELECT
                    snapshot_date AS report_date,
                    count(DISTINCT sku) AS stock_sku_count,
                    coalesce(
                      sum(stock_available_qty) FILTER (
                        WHERE source_type = 'wb_stock_history_daily_csv'
                      ),
                      sum(stock_available_qty)
                    ) AS stock_available_qty,
                    sum(stock_preparing_qty) AS stock_preparing_qty,
                    sum(stock_reserved_qty) AS stock_reserved_qty,
                    coalesce(
                      sum(stock_total_qty) FILTER (
                        WHERE source_type = 'wb_stock_history_daily_csv'
                      ),
                      sum(stock_total_qty)
                    ) AS stock_total_qty,
                    sum(to_customer_qty) AS to_customer_qty,
                    sum(from_customer_qty) AS from_customer_qty,
                    sum(stock_value_rub) AS stock_value_rub,
                    avg(turnover_days) AS turnover_days,
                    avg(days_to_stockout) AS days_to_stockout,
                    sum(lost_orders_qty) AS lost_orders_qty,
                    sum(lost_orders_rub) AS lost_orders_rub,
                    sum(in_supply_orders_qty) AS in_supply_orders_qty,
                    sum(in_transit_supply_qty) AS in_transit_supply_qty,
                    sum(returning_from_customers_qty) AS returning_from_customers_qty,
                    sum(checking_qty) AS checking_qty,
                    sum(defective_qty) AS defective_qty,
                    sum(expiring_qty) AS expiring_qty,
                    sum(preparing_to_remove_qty) AS preparing_to_remove_qty,
                    sum(marked_qty) AS marked_qty
                  FROM public.inventory_history_daily
                  WHERE {where}
                  GROUP BY snapshot_date
                ),
                movements AS (
                  SELECT daily.*,
                    report_date - lag(report_date) OVER (ORDER BY report_date) AS snapshot_gap_days,
                    stock_available_qty
                      - lag(stock_available_qty) OVER (ORDER BY report_date) AS stock_change_qty
                  FROM daily
                )
                SELECT movements.*,
                  greatest(stock_change_qty, 0) AS stock_inflow_qty,
                  greatest(-stock_change_qty, 0) AS stock_outflow_qty
                FROM movements
                ORDER BY report_date
                """,
                values,
            )
            return [dict(row) for row in cur.fetchall()]


def wb_orders_daily_rows(parsed: Any, get_conn: Callable[[], Any]) -> list[dict[str, Any]]:
    """Return complete WB order facts independently of the short Analytics funnel window."""
    params = parse_qs(parsed.query)
    clauses = ["source_key = 'statistics.orders'", "record_date IS NOT NULL"]
    values: list[Any] = []
    date_from = params.get("date_from", [""])[0]
    date_to = params.get("date_to", [""])[0]
    inventory_skus = [value.strip() for value in params.get("inventory_skus", []) if value.strip()]
    categories = [value for value in params.get("categories", []) if value]
    article = params.get("article", [""])[0].strip()
    if date_from:
        clauses.append("record_date >= %s")
        values.append(date_from)
    if date_to:
        clauses.append("record_date <= %s")
        values.append(date_to)
    if inventory_skus:
        clauses.append("payload->>'nmId' = ANY(%s)")
        values.append(inventory_skus)
    if categories:
        clauses.append("coalesce(payload->>'category', payload->>'subject', '') = ANY(%s)")
        values.append(categories)
    if article:
        clauses.append(
            "(payload->>'nmId' ILIKE %s OR coalesce(payload->>'supplierArticle', '') ILIKE %s)"
        )
        pattern = f"%{article}%"
        values.extend((pattern, pattern))

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_api_entities') AS relation")
        relation_row = cur.fetchone()
        relation = dict(relation_row or {}).get("relation") if isinstance(relation_row, dict) else relation_row[0]
        if not relation:
            return []
        cur.execute(
            f"""
            SELECT
              record_date AS report_date,
              count(*)::numeric AS ordered_units,
              coalesce(sum(nullif(payload->>'priceWithDisc', '')::numeric), 0) AS ordered_amount_rub,
              count(*) FILTER (
                WHERE lower(coalesce(payload->>'isCancel', 'false')) = 'true'
              )::numeric AS cancelled_units,
              coalesce(sum(nullif(payload->>'priceWithDisc', '')::numeric) FILTER (
                WHERE lower(coalesce(payload->>'isCancel', 'false')) = 'true'
              ), 0) AS cancelled_amount_rub,
              count(*) FILTER (
                WHERE lower(coalesce(payload->>'isCancel', 'false')) <> 'true'
              )::numeric AS active_ordered_units
            FROM public.wb_api_entities
            WHERE {' AND '.join(clauses)}
            GROUP BY record_date
            ORDER BY record_date
            """,
            values,
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["orders_source"] = "wb_statistics_orders"
    return rows


def parsed_with_inventory_skus(parsed: Any, skus: list[str]) -> Any:
    params = parse_qs(parsed.query)
    for key in ("product", "article", "q", "categories", "category", "category_exact"):
        params.pop(key, None)
    clean_skus = [str(sku).strip() for sku in skus if str(sku).strip()]
    params["inventory_skus"] = clean_skus or ["__inventory_no_match__"]
    return parsed._replace(query=urlencode(params, doseq=True))


def handle_dashboard(
    parsed: Any,
    get_conn: Callable[[], Any],
    marketplace_from_query: Callable[[str], str],
    funnel_daily_handler: Callable[[Any], dict[str, Any]],
    funnel_view_for_marketplace: Callable[[str], str],
) -> dict[str, Any]:
    marketplace = marketplace_from_query(parsed.query)
    params = parse_qs(parsed.query)
    selected_product = params.get("product", [""])[0].strip()
    effective_parsed = parsed
    resolved_skus: list[str] = []
    if selected_product:
        monitored_rows, _ = product_monitoring_rows(
            parsed,
            get_conn,
            marketplace,
            funnel_view_for_marketplace(marketplace),
        )
        resolved_skus = sorted({str(row.get("sku") or "") for row in monitored_rows if row.get("sku")})
        effective_parsed = parsed_with_inventory_skus(parsed, resolved_skus)
    stock_rows = stock_daily_rows(effective_parsed, get_conn, marketplace)
    try:
        funnel_rows = list((funnel_daily_handler(effective_parsed) or {}).get("rows") or [])
    except Exception:
        funnel_rows = []
    wb_order_rows = wb_orders_daily_rows(effective_parsed, get_conn) if marketplace == "wb" else []
    coverage = snapshot_coverage(parsed, get_conn, marketplace)
    merged: dict[str, dict[str, Any]] = {}
    for current_date in _date_range(_date_value(coverage.get("date_from")), _date_value(coverage.get("date_to"))):
        key = _iso_date(current_date)
        merged[key] = {"report_date": key}
    for row in funnel_rows:
        key = str(row.get("report_date") or "")
        if key:
            merged[key] = dict(row)
    for row in wb_order_rows:
        key = str(row.get("report_date") or "")
        if key:
            target = merged.setdefault(key, {"report_date": row.get("report_date")})
            target.update(row)
    for row in stock_rows:
        key = str(row.get("report_date") or "")
        target = merged.setdefault(key, {"report_date": row.get("report_date")})
        target.update(row)
    for row in merged.values():
        if row.get("orders_source") != "wb_statistics_orders":
            continue
        ordered_units = _number(row.get("ordered_units")) or 0
        adv_orders = _number(row.get("adv_orders")) or 0
        ordered_amount = _number(row.get("ordered_amount_rub")) or 0
        cancelled_units = _number(row.get("cancelled_units")) or 0
        row["organic_orders"] = max(ordered_units - adv_orders, 0)
        row["ordered_amount_per_unit_rub"] = round(ordered_amount / ordered_units, 2) if ordered_units else None
        row["cancellation_pct"] = round(cancelled_units / ordered_units * 100, 2) if ordered_units else None
    return {
        "rows": [merged[key] for key in sorted(merged)],
        "marketplace": marketplace,
        "coverage": coverage,
        "resolved_skus": resolved_skus,
        "movement_definition": (
            "Дельта остатка — изменение доступного остатка к предыдущему сохранённому снимку; "
            "это не бухгалтерский приход или расход."
        ),
    }

def handle_summary(
    parsed: Any,
    get_conn: Callable[[], Any],
    marketplace_from_query: Callable[[str], str],
    funnel_daily_handler: Callable[[Any], dict[str, Any]],
    funnel_view_for_marketplace: Callable[[str], str],
) -> dict[str, Any]:
    dashboard_payload = handle_dashboard(parsed, get_conn, marketplace_from_query, funnel_daily_handler, funnel_view_for_marketplace)
    stock_rows = [
        row for row in dashboard_payload["rows"]
        if row.get("stock_available_qty") is not None
    ]
    latest = stock_rows[-1] if stock_rows else {}
    products_payload = handle_products(
        parsed,
        get_conn,
        marketplace_from_query,
        funnel_view_for_marketplace,
    )
    product_summary = products_payload.get("summary") or {}
    return {
        "stock_reserved_qty": latest.get("stock_reserved_qty"),
        **product_summary,
        "stock_sku_count": product_summary.get("snapshot_sku_count"),
    }

def handle_filters(
    parsed: Any,
    get_conn: Callable[[], Any],
    marketplace_from_query: Callable[[str], str],
    marketplaces_payload: Callable[[], list[dict[str, Any]]],
    funnel_view_for_marketplace: Callable[[str], str],
) -> dict[str, Any]:
    marketplace = marketplace_from_query(parsed.query)
    empty = {
        "category_names": [],
        "product_names": [],
        "date_from": "",
        "date_to": "",
        "marketplace": marketplace,
        "marketplaces": marketplaces_payload(),
        "monitoring_sku_count": 0,
        "snapshot_sku_count": 0,
    }
    rows, coverage = product_monitoring_rows(
        parsed,
        get_conn,
        marketplace,
        funnel_view_for_marketplace(marketplace),
    )
    return {
        **empty,
        "date_from": coverage.get("date_from") or "",
        "date_to": coverage.get("date_to") or "",
        "category_names": sorted({str(row.get("category_name") or "") for row in rows if row.get("category_name")}),
        "product_names": sorted({str(row.get("product_name") or "") for row in rows if row.get("product_name")}),
        "monitoring_sku_count": len(rows),
        "snapshot_sku_count": sum(1 for row in rows if row.get("current_snapshot_present")),
    }

