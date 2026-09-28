"""Editable Ozon sales plan and plan/fact payloads for KM Trade.

All connections are hard-locked to the isolated ``km_trade_products`` database.
Actual sales come from exact Ozon finance revenue lines; advertising comes from
the Ozon Performance daily source. Revenue plans are synchronized with the
``План продаж`` report for KM Trade.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import parse_qs

from psycopg2.extras import RealDictCursor, execute_values

from km_trade_finance import TARGET_DB, connect_km


_SALES_FORECAST_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_SALES_FORECAST_LOCK = Lock()
_SALES_FORECAST_TTL_SECONDS = 300.0


def number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def decimal_value(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value!r}") from exc


def percent(numerator: Any, denominator: Any) -> float:
    denominator_value = number(denominator)
    return round(number(numerator) / denominator_value * 100, 2) if denominator_value else 0.0


def iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value or "")


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def add_months(day: date, offset: int) -> date:
    month_index = day.year * 12 + day.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def query_dates(parsed, *, default_from: date, default_to: date) -> tuple[date, date]:
    params = parse_qs(parsed.query)
    raw_from = (params.get("date_from") or [""])[0].strip()
    raw_to = (params.get("date_to") or [""])[0].strip()
    date_from = date.fromisoformat(raw_from) if raw_from else default_from
    date_to = date.fromisoformat(raw_to) if raw_to else default_to
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    return date_from, date_to


def ensure_schema(config: dict[str, Any]) -> None:
    with connect_km(config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.km_trade_sales_plan_monthly (
                    month_start date PRIMARY KEY,
                    sales_plan_units numeric NOT NULL DEFAULT 0,
                    sales_plan_rub numeric NOT NULL DEFAULT 0,
                    ad_spend_plan_rub numeric NOT NULL DEFAULT 0,
                    updated_at timestamp without time zone NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS public.km_trade_sales_plan_product_yearly (
                    plan_year integer NOT NULL,
                    sku text NOT NULL,
                    article text,
                    product_name text,
                    price_rub numeric NOT NULL DEFAULT 0,
                    plan_units numeric NOT NULL DEFAULT 0,
                    plan_revenue numeric NOT NULL DEFAULT 0,
                    updated_at timestamp without time zone NOT NULL DEFAULT now(),
                    PRIMARY KEY (plan_year, sku)
                );
                """
            )
        conn.commit()


def available_range(config: dict[str, Any]) -> tuple[date, date]:
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT min(operation_date) AS date_from, max(operation_date) AS date_to
            FROM public.ozon_finance_events
            """
        )
        row = cur.fetchone()
    today = date.today()
    return row["date_from"] or today, row["date_to"] or today


def _sales_forecast_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Return one shared sales-planning snapshot for concurrent dashboard calls."""
    cached = _SALES_FORECAST_CACHE.get("payload")
    if cached and monotonic() < number(_SALES_FORECAST_CACHE.get("expires_at")):
        return cached
    with _SALES_FORECAST_LOCK:
        cached = _SALES_FORECAST_CACHE.get("payload")
        if cached and monotonic() < number(_SALES_FORECAST_CACHE.get("expires_at")):
            return cached
        from km_trade_sales_planning import sales_forecast_payload

        payload = sales_forecast_payload(config, "marketplace=ozon")
        _SALES_FORECAST_CACHE["payload"] = payload
        _SALES_FORECAST_CACHE["expires_at"] = monotonic() + _SALES_FORECAST_TTL_SECONDS
        return payload


def _effective_month_plan(
    month: date, row: dict[str, Any], anchor_month: date
) -> tuple[float, float, str]:
    """Use exactly the plan semantics shown in the KM Trade sales-plan report."""
    if month == date(2026, 7, 1):
        return number(row.get("actual_units")), number(row.get("actual_revenue")), "Факт июля"
    approved_units = number(row.get("approved_plan_units"))
    approved_revenue = number(row.get("approved_plan_revenue"))
    if approved_units > 0 or approved_revenue > 0:
        return approved_units, approved_revenue, "Утверждённый план"
    if month == anchor_month:
        return (
            number(row.get("calculated_plan_units")),
            number(row.get("calculated_plan_revenue")),
            "Расчётный план",
        )
    if month > anchor_month:
        return (
            number(row.get("forecast_units")),
            number(row.get("forecast_revenue")),
            "Прогнозный план",
        )
    return 0.0, 0.0, "Нет плана"


def _forecast_month_lookup(
    config: dict[str, Any],
) -> tuple[dict[date, dict[str, Any]], date, dict[str, Any]]:
    payload = _sales_forecast_snapshot(config)
    anchor_month = date.fromisoformat(payload["period"]["anchor_month"])
    lookup = {
        date.fromisoformat(row["month_start"]): row
        for row in payload.get("months", [])
    }
    return lookup, anchor_month, payload


def plan_rows(config: dict[str, Any], year: int) -> dict[date, dict[str, Any]]:
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT month_start, sales_plan_units, sales_plan_rub, ad_spend_plan_rub
            FROM public.km_trade_sales_plan_monthly
            WHERE extract(year FROM month_start)::int = %s
            ORDER BY month_start
            """,
            (year,),
        )
        plans = {row["month_start"]: dict(row) for row in cur.fetchall()}
    forecast_months, anchor_month, _ = _forecast_month_lookup(config)
    for month, forecast_row in forecast_months.items():
        if month.year != year or month < date(2026, 7, 1):
            continue
        units, revenue, source = _effective_month_plan(month, forecast_row, anchor_month)
        existing = plans.setdefault(month, {})
        existing["sales_plan_units"] = units
        existing["sales_plan_rub"] = revenue
        existing["plan_source"] = source
        existing.setdefault("ad_spend_plan_rub", 0)
    return plans


def daily_actual(
    config: dict[str, Any], date_from: date, date_to: date
) -> dict[date, dict[str, Decimal]]:
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH finance AS (
                SELECT
                    operation_date AS report_date,
                    coalesce(sum(amount) FILTER (WHERE line_kind = 'revenue'), 0) AS sales_rub,
                    coalesce(sum(quantity) FILTER (WHERE line_kind = 'revenue'), 0) AS sales_units
                FROM public.ozon_finance_lines
                WHERE operation_date BETWEEN %s AND %s
                GROUP BY operation_date
            ),
            adv AS (
                SELECT
                    report_date,
                    coalesce(sum(coalesce(fact_expense_rub, expense_rub, 0)), 0) AS ad_spend_rub
                FROM public.ozon_adv_daily_raw
                WHERE report_date BETWEEN %s AND %s
                GROUP BY report_date
            )
            SELECT
                coalesce(f.report_date, a.report_date) AS report_date,
                coalesce(f.sales_rub, 0) AS sales_rub,
                coalesce(f.sales_units, 0) AS sales_units,
                coalesce(a.ad_spend_rub, 0) AS ad_spend_rub
            FROM finance f
            FULL JOIN adv a USING (report_date)
            ORDER BY report_date
            """,
            (date_from, date_to, date_from, date_to),
        )
        return {
            row["report_date"]: {
                "sales_rub": decimal_value(row["sales_rub"]),
                "sales_units": decimal_value(row["sales_units"]),
                "ad_spend_rub": decimal_value(row["ad_spend_rub"]),
            }
            for row in cur.fetchall()
        }


def daily_rows(
    config: dict[str, Any], date_from: date, date_to: date
) -> list[dict[str, Any]]:
    plans_by_month: dict[date, dict[str, Any]] = {}
    for year in range(date_from.year, date_to.year + 1):
        plans_by_month.update(plan_rows(config, year))
    actual = daily_actual(config, date_from, date_to)
    rows: list[dict[str, Any]] = []
    cursors: dict[date, dict[str, Decimal]] = {}
    current = date_from
    while current <= date_to:
        plan_month = month_start(current)
        plan = plans_by_month.get(plan_month, {})
        days_in_month = Decimal(calendar.monthrange(current.year, current.month)[1])
        sales_plan = decimal_value(plan.get("sales_plan_rub"))
        units_plan = decimal_value(plan.get("sales_plan_units"))
        ad_plan = decimal_value(plan.get("ad_spend_plan_rub"))
        sales_plan_daily = sales_plan / days_in_month
        units_plan_daily = units_plan / days_in_month
        ad_plan_daily = ad_plan / days_in_month
        cursor = cursors.setdefault(
            plan_month,
            {
                "sales": Decimal("0"),
                "units": Decimal("0"),
                "ad": Decimal("0"),
            },
        )
        fact = actual.get(
            current,
            {
                "sales_rub": Decimal("0"),
                "sales_units": Decimal("0"),
                "ad_spend_rub": Decimal("0"),
            },
        )
        cursor["sales"] += fact["sales_rub"]
        cursor["units"] += fact["sales_units"]
        cursor["ad"] += fact["ad_spend_rub"]
        sales_elapsed = sales_plan_daily * Decimal(current.day)
        units_elapsed = units_plan_daily * Decimal(current.day)
        ad_elapsed = ad_plan_daily * Decimal(current.day)
        rows.append(
            {
                "marketplace": "ozon",
                "marketplace_label": "Ozon",
                "report_date": current.isoformat(),
                "plan_month": plan_month.isoformat(),
                "orders_rub": number(fact["sales_rub"]),
                "orders_qty": number(fact["sales_units"]),
                "sales_rub": number(fact["sales_rub"]),
                "sales_units": number(fact["sales_units"]),
                "ad_spend_rub": number(fact["ad_spend_rub"]),
                "sales_plan_rub": number(sales_plan),
                "sales_plan_units": number(units_plan),
                "ad_spend_plan_rub": number(ad_plan),
                "sales_plan_daily_rub": number(sales_plan_daily),
                "sales_plan_daily_units": number(units_plan_daily),
                "ad_spend_plan_daily_rub": number(ad_plan_daily),
                "sales_plan_elapsed_rub": number(sales_elapsed),
                "sales_plan_elapsed_units": number(units_elapsed),
                "ad_spend_plan_elapsed_rub": number(ad_elapsed),
                "orders_cum_rub": number(cursor["sales"]),
                "sales_cum_rub": number(cursor["sales"]),
                "sales_cum_units": number(cursor["units"]),
                "ad_spend_cum_rub": number(cursor["ad"]),
                "sales_month_plan_fact_pct": percent(cursor["sales"], sales_plan),
                "sales_elapsed_plan_fact_pct": percent(cursor["sales"], sales_elapsed),
                "ad_spend_budget_used_pct": percent(cursor["ad"], ad_plan),
                "ad_spend_elapsed_budget_pct": percent(cursor["ad"], ad_elapsed),
                "tacos_pct": percent(fact["ad_spend_rub"], fact["sales_rub"]),
                "tacos_cum_pct": percent(cursor["ad"], cursor["sales"]),
                "has_fact": current in actual,
            }
        )
        current += timedelta(days=1)
    return rows


def filters_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    years = sorted({available_from.year, available_to.year, date.today().year})
    months = []
    for year in years:
        for month in range(1, 13):
            start = date(year, month, 1)
            months.append(
                {
                    "month": start.isoformat(),
                    "date_from": start.isoformat(),
                    "date_to": month_end(start).isoformat(),
                }
            )
    return {
        "category_names": [],
        "product_names": [],
        "date_from": available_from.isoformat(),
        "date_to": available_to.isoformat(),
        "months": months,
        "marketplaces": [{"id": "ozon", "label": "Ozon"}],
        "marketplace": "ozon",
        "abc_orders": [],
        "abc_sales": [],
        "abc_stock": [],
        "abc_combined": [],
        "view": "public.ozon_finance_lines + km_trade_sales_plan_monthly",
    }


def daily_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    default_from = month_start(available_to)
    date_from, date_to = query_dates(
        parsed, default_from=default_from, default_to=month_end(available_to)
    )
    return {
        "rows": [
            {key: value for key, value in row.items() if key != "has_fact"}
            for row in daily_rows(config, date_from, date_to)
        ],
        "report_kind": "km_trade_ozon_planfact",
    }


def summary_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    date_from, date_to = query_dates(
        parsed,
        default_from=month_start(available_to),
        default_to=month_end(available_to),
    )
    rows = daily_rows(config, date_from, date_to)
    sales = sum((decimal_value(row["sales_rub"]) for row in rows), Decimal("0"))
    units = sum((decimal_value(row["sales_units"]) for row in rows), Decimal("0"))
    ad_spend = sum((decimal_value(row["ad_spend_rub"]) for row in rows), Decimal("0"))
    unique_months = {row["plan_month"]: row for row in rows}
    sales_plan = sum(
        (decimal_value(row["sales_plan_rub"]) for row in unique_months.values()),
        Decimal("0"),
    )
    units_plan = sum(
        (decimal_value(row["sales_plan_units"]) for row in unique_months.values()),
        Decimal("0"),
    )
    ad_plan = sum(
        (decimal_value(row["ad_spend_plan_rub"]) for row in unique_months.values()),
        Decimal("0"),
    )
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days_count": len(rows),
        "orders_rub": number(sales),
        "orders_qty": number(units),
        "sales_rub": number(sales),
        "sales_units": number(units),
        "sales_plan_rub": number(sales_plan),
        "sales_plan_units": number(units_plan),
        "ad_spend_rub": number(ad_spend),
        "ad_spend_plan_rub": number(ad_plan),
        "sales_plan_fact_pct": percent(sales, sales_plan),
        "ad_spend_budget_used_pct": percent(ad_spend, ad_plan),
        "tacos_fact_pct": percent(ad_spend, sales),
        "tacos_plan_pct": percent(ad_plan, sales_plan),
    }


def monthly_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    date_from, date_to = query_dates(
        parsed,
        default_from=month_start(available_to),
        default_to=month_end(available_to),
    )
    rows = daily_rows(config, month_start(date_from), month_end(date_to))
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["plan_month"]
        item = grouped.setdefault(
            key,
            {
                "marketplace": "ozon",
                "marketplace_label": "Ozon",
                "plan_month": key,
                "date_from": key,
                "date_to": month_end(date.fromisoformat(key)).isoformat(),
                "days_with_fact": 0,
                "sales_plan_rub": row["sales_plan_rub"],
                "sales_plan_units": row["sales_plan_units"],
                "sales_rub": 0.0,
                "sales_units": 0.0,
                "ad_spend_plan_rub": row["ad_spend_plan_rub"],
                "ad_spend_rub": 0.0,
                "orders_rub": 0.0,
            },
        )
        item["sales_rub"] += number(row["sales_rub"])
        item["sales_units"] += number(row["sales_units"])
        item["ad_spend_rub"] += number(row["ad_spend_rub"])
        item["orders_rub"] += number(row["orders_rub"])
        if row["has_fact"]:
            item["days_with_fact"] += 1
    for item in grouped.values():
        item["sales_plan_fact_pct"] = percent(
            item["sales_rub"], item["sales_plan_rub"]
        )
        item["ad_spend_budget_used_pct"] = percent(
            item["ad_spend_rub"], item["ad_spend_plan_rub"]
        )
        item["tacos_plan_pct"] = percent(
            item["ad_spend_plan_rub"], item["sales_plan_rub"]
        )
        item["tacos_fact_pct"] = percent(item["ad_spend_rub"], item["sales_rub"])
    return {"rows": list(grouped.values())}


def scorecard_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    date_from, date_to = query_dates(
        parsed,
        default_from=month_start(available_to),
        default_to=month_end(available_to),
    )
    rows = daily_rows(config, date_from, date_to)
    factual_rows = [row for row in rows if row["has_fact"]]
    if not rows:
        return {"rows": []}
    last_date = max(
        (date.fromisoformat(row["report_date"]) for row in factual_rows),
        default=min(date_to, available_to),
    )
    last_day = last_date.day
    recent_start = max(last_day - 4, 1)
    recent_rows = [
        row
        for row in factual_rows
        if recent_start <= date.fromisoformat(row["report_date"]).day <= last_day
    ]
    days_in_month = calendar.monthrange(last_date.year, last_date.month)[1]
    sales = sum(number(row["sales_rub"]) for row in factual_rows)
    ad_spend = sum(number(row["ad_spend_rub"]) for row in factual_rows)
    units = sum(number(row["sales_units"]) for row in factual_rows)
    recent_sales = sum(number(row["sales_rub"]) for row in recent_rows)
    recent_ad = sum(number(row["ad_spend_rub"]) for row in recent_rows)
    elapsed_days = max(last_day, 1)
    recent_days = max(len(recent_rows), 1)
    sales_plan = number(rows[0]["sales_plan_rub"])
    units_plan = number(rows[0]["sales_plan_units"])
    ad_plan = number(rows[0]["ad_spend_plan_rub"])
    sales_runrate = sales / elapsed_days * days_in_month
    ad_runrate = ad_spend / elapsed_days * days_in_month
    sales_recent_runrate = sales + recent_sales / recent_days * max(
        days_in_month - last_day, 0
    )
    ad_recent_runrate = ad_spend + recent_ad / recent_days * max(
        days_in_month - last_day, 0
    )
    row = {
        "report_kind": "km_trade_ozon_planfact",
        "marketplace": "ozon",
        "marketplace_label": "Ozon",
        "plan_month": rows[0]["plan_month"],
        "last_date": last_date.isoformat(),
        "last_day": last_day,
        "recent_start_day": recent_start,
        "days_in_month": days_in_month,
        "days_with_fact": len(factual_rows),
        "orders_rub": sales,
        "orders_qty": units,
        "sales_plan_rub": sales_plan,
        "sales_plan_units": units_plan,
        "sales_rub": sales,
        "sales_units": units,
        "sales_plan_fact_pct": percent(sales, sales_plan),
        "sales_runrate_rub": sales_runrate,
        "sales_runrate_pct": percent(sales_runrate, sales_plan),
        "sales_recent_runrate_rub": sales_recent_runrate,
        "sales_recent_runrate_pct": percent(sales_recent_runrate, sales_plan),
        "ad_spend_plan_rub": ad_plan,
        "ad_spend_rub": ad_spend,
        "ad_spend_plan_fact_pct": percent(ad_spend, ad_plan),
        "ad_spend_runrate_rub": ad_runrate,
        "ad_spend_runrate_pct": percent(ad_runrate, ad_plan),
        "ad_spend_recent_runrate_rub": ad_recent_runrate,
        "ad_spend_recent_runrate_pct": percent(ad_recent_runrate, ad_plan),
    }
    return {"rows": [row]}



def product_planfact_payload(config: dict[str, Any], parsed) -> dict[str, Any]:
    available_from, available_to = available_range(config)
    selected_from, selected_to = query_dates(
        parsed,
        default_from=month_start(available_to),
        default_to=month_end(available_to),
    )
    selected_month = month_start(selected_from)
    _, anchor_month, payload = _forecast_month_lookup(config)
    rows: list[dict[str, Any]] = []
    source_label = "Нет плана"
    for product in payload.get("products", []):
        month_row = next(
            (
                row
                for row in product.get("monthly", [])
                if row.get("month_start") == selected_month.isoformat()
            ),
            None,
        )
        if not month_row:
            continue
        plan_units, plan_revenue, source = _effective_month_plan(
            selected_month, month_row, anchor_month
        )
        source_label = source
        actual_units = number(month_row.get("actual_units"))
        actual_revenue = number(month_row.get("actual_revenue"))
        if not any((plan_units, plan_revenue, actual_units, actual_revenue)):
            continue
        rows.append(
            {
                "sku": str(product.get("sku") or ""),
                "article": str(product.get("article") or ""),
                "product_name": str(product.get("product_name") or ""),
                "category_name": str(product.get("category_name") or "Без категории"),
                "plan_units": round(plan_units, 2),
                "actual_units": round(actual_units, 2),
                "units_deviation": round(actual_units - plan_units, 2),
                "units_plan_fact_pct": percent(actual_units, plan_units),
                "plan_revenue": round(plan_revenue, 2),
                "actual_revenue": round(actual_revenue, 2),
                "revenue_deviation": round(actual_revenue - plan_revenue, 2),
                "revenue_plan_fact_pct": percent(actual_revenue, plan_revenue),
            }
        )
    rows.sort(key=lambda row: (-max(row["plan_revenue"], row["actual_revenue"]), row["article"]))
    totals = {
        "plan_units": sum(number(row["plan_units"]) for row in rows),
        "actual_units": sum(number(row["actual_units"]) for row in rows),
        "plan_revenue": sum(number(row["plan_revenue"]) for row in rows),
        "actual_revenue": sum(number(row["actual_revenue"]) for row in rows),
    }
    totals["units_deviation"] = totals["actual_units"] - totals["plan_units"]
    totals["units_plan_fact_pct"] = percent(totals["actual_units"], totals["plan_units"])
    totals["revenue_deviation"] = totals["actual_revenue"] - totals["plan_revenue"]
    totals["revenue_plan_fact_pct"] = percent(totals["actual_revenue"], totals["plan_revenue"])
    return {
        "ok": True,
        "month_start": selected_month.isoformat(),
        "date_from": selected_from.isoformat(),
        "date_to": selected_to.isoformat(),
        "plan_source": source_label,
        "rows": rows,
        "totals": totals,
        "methodology": (
            "Июль 2026: план равен факту. Август–декабрь: значения берутся "
            "из отчёта «План продаж»; утверждённый план имеет приоритет, "
            "для текущего месяца используется расчётный, для будущих — прогнозный."
        ),
    }

def product_rows(config: dict[str, Any], year: int) -> list[dict[str, Any]]:
    date_from = date(year, 1, 1)
    date_to = date(year, 12, 31)
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH actual AS (
                SELECT
                    sku,
                    max(article) FILTER (WHERE nullif(article, '') IS NOT NULL) AS article,
                    max(product_name) FILTER (WHERE nullif(product_name, '') IS NOT NULL) AS product_name,
                    coalesce(sum(quantity) FILTER (WHERE line_kind = 'revenue'), 0) AS actual_units,
                    coalesce(sum(amount) FILTER (WHERE line_kind = 'revenue'), 0) AS actual_revenue
                FROM public.ozon_finance_lines
                WHERE operation_date BETWEEN %s AND %s
                  AND nullif(sku, '') IS NOT NULL
                GROUP BY sku
            ),
            latest_price AS (
                SELECT DISTINCT ON (coalesce(nullif(offer_id, ''), sku))
                    sku, offer_id, coalesce(marketing_seller_price, price, 0) AS current_price
                FROM public.ozon_product_price_snapshots
                ORDER BY coalesce(nullif(offer_id, ''), sku), snapshot_date DESC
            ),
            all_skus AS (
                SELECT sku FROM actual
                UNION SELECT sku FROM public.ozon_unit_product_settings
                UNION SELECT sku FROM public.km_trade_sales_plan_product_yearly WHERE plan_year = %s
            )
            SELECT
                s.sku,
                coalesce(a.article, p.article, lp.offer_id, s.sku) AS article,
                coalesce(a.product_name, p.product_name, 'SKU ' || s.sku) AS product_name,
                coalesce(p.price_rub, us.planned_price, lp.current_price, 0) AS price_rub,
                coalesce(p.plan_units, 0) AS plan_units,
                coalesce(p.plan_revenue, 0) AS plan_revenue,
                coalesce(a.actual_units, 0) AS actual_units,
                coalesce(a.actual_revenue, 0) AS actual_revenue
            FROM all_skus s
            LEFT JOIN actual a ON a.sku = s.sku
            LEFT JOIN public.ozon_unit_product_settings us ON us.sku = s.sku
            LEFT JOIN public.km_trade_sales_plan_product_yearly p
              ON p.plan_year = %s AND p.sku = s.sku
            LEFT JOIN latest_price lp
              ON lp.sku = s.sku OR nullif(lp.offer_id, '') = nullif(a.article, '')
            ORDER BY coalesce(p.plan_revenue, 0) DESC, coalesce(a.actual_revenue, 0) DESC, s.sku
            """,
            (date_from, date_to, year, year),
        )
        rows = []
        for row in cur.fetchall():
            item = dict(row)
            item["plan_fact_pct"] = percent(
                item["actual_revenue"], item["plan_revenue"]
            )
            rows.append({key: (number(value) if isinstance(value, Decimal) else value) for key, value in item.items()})
        return rows


def sales_plan_payload(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
) -> dict[str, Any]:
    ensure_schema(config)
    available_from, available_to = available_range(config)
    selected = date.fromisoformat(raw_from) if raw_from else available_to
    year = selected.year
    year_from, year_to = date(year, 1, 1), date(year, 12, 31)
    plans = plan_rows(config, year)
    actual = daily_actual(config, year_from, year_to)
    months = []
    plan_units_cum = actual_units_cum = Decimal("0")
    plan_revenue_cum = actual_revenue_cum = Decimal("0")
    for index in range(12):
        start = add_months(year_from, index)
        end = month_end(start)
        plan = plans.get(start, {})
        plan_units = decimal_value(plan.get("sales_plan_units"))
        plan_revenue = decimal_value(plan.get("sales_plan_rub"))
        ad_plan = decimal_value(plan.get("ad_spend_plan_rub"))
        month_actual = [
            value for day, value in actual.items() if start <= day <= end
        ]
        actual_units = sum(
            (value["sales_units"] for value in month_actual), Decimal("0")
        )
        actual_revenue = sum(
            (value["sales_rub"] for value in month_actual), Decimal("0")
        )
        actual_ad = sum(
            (value["ad_spend_rub"] for value in month_actual), Decimal("0")
        )
        plan_units_cum += plan_units
        actual_units_cum += actual_units
        plan_revenue_cum += plan_revenue
        actual_revenue_cum += actual_revenue
        months.append(
            {
                "month_start": start.isoformat(),
                "month": start.strftime("%Y-%m"),
                "plan_units": number(plan_units),
                "actual_units": number(actual_units),
                "plan_revenue": number(plan_revenue),
                "actual_revenue": number(actual_revenue),
                "ad_spend_plan": number(ad_plan),
                "actual_ad_spend": number(actual_ad),
                "plan_fact_pct": percent(actual_revenue, plan_revenue),
                "plan_units_cum": number(plan_units_cum),
                "actual_units_cum": number(actual_units_cum),
                "plan_revenue_cum": number(plan_revenue_cum),
                "actual_revenue_cum": number(actual_revenue_cum),
                "plan_fact_cum_pct": percent(
                    actual_revenue_cum, plan_revenue_cum
                ),
            }
        )
    products = product_rows(config, year)
    totals = {
        "plan_units": sum(number(row["plan_units"]) for row in months),
        "actual_units": sum(number(row["actual_units"]) for row in months),
        "plan_revenue": sum(number(row["plan_revenue"]) for row in months),
        "actual_revenue": sum(number(row["actual_revenue"]) for row in months),
        "ad_spend_plan": sum(number(row["ad_spend_plan"]) for row in months),
        "actual_ad_spend": sum(number(row["actual_ad_spend"]) for row in months),
    }
    totals["plan_fact_pct"] = percent(
        totals["actual_revenue"], totals["plan_revenue"]
    )
    totals["remaining_revenue"] = max(
        totals["plan_revenue"] - totals["actual_revenue"], 0
    )
    return {
        "ok": True,
        "client": client_key,
        "marketplace": "ozon",
        "period": {
            "available_from": available_from.isoformat(),
            "available_to": available_to.isoformat(),
            "date_from": year_from.isoformat(),
            "date_to": year_to.isoformat(),
        },
        "totals": totals,
        "months": months,
        "products": products,
        "methodology": (
            "Факт продаж — точные строки revenue финансов Ozon с возвратами; "
            "факт рекламы — Ozon Performance. Планы вводятся вручную и не "
            "подменяются прогнозом."
        ),
    }


def save_sales_plan(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(config)
    raw_year = payload.get("year")
    year = int(raw_year or date.today().year)
    if not 2020 <= year <= 2100:
        raise ValueError("Некорректный год плана")
    months = payload.get("months") or []
    products = payload.get("products") or []
    if not isinstance(months, list) or len(months) > 12:
        raise ValueError("Ожидается не более 12 месяцев")
    if not isinstance(products, list) or len(products) > 2000:
        raise ValueError("Ожидается не более 2000 товаров")
    prepared_months = []
    for row in months:
        start = date.fromisoformat(str(row.get("month_start") or ""))
        if start.year != year or start.day != 1:
            raise ValueError("Месяц плана не соответствует выбранному году")
        values = (
            decimal_value(row.get("plan_units")),
            decimal_value(row.get("plan_revenue")),
            decimal_value(row.get("ad_spend_plan")),
        )
        if any(value < 0 for value in values):
            raise ValueError("Плановые значения не могут быть отрицательными")
        prepared_months.append((start, *values))
    prepared_products = []
    for row in products:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        values = (
            decimal_value(row.get("price_rub")),
            decimal_value(row.get("plan_units")),
            decimal_value(row.get("plan_revenue")),
        )
        if any(value < 0 for value in values):
            raise ValueError("План товара не может быть отрицательным")
        prepared_products.append(
            (
                year,
                sku,
                str(row.get("article") or "").strip() or None,
                str(row.get("product_name") or "").strip() or None,
                *values,
            )
        )
    with connect_km(config) as conn:
        try:
            with conn.cursor() as cur:
                if prepared_months:
                    execute_values(
                        cur,
                        """
                        INSERT INTO public.km_trade_sales_plan_monthly (
                            month_start, sales_plan_units, sales_plan_rub,
                            ad_spend_plan_rub
                        ) VALUES %s
                        ON CONFLICT (month_start) DO UPDATE SET
                            sales_plan_units = EXCLUDED.sales_plan_units,
                            sales_plan_rub = EXCLUDED.sales_plan_rub,
                            ad_spend_plan_rub = EXCLUDED.ad_spend_plan_rub,
                            updated_at = now()
                        """,
                        prepared_months,
                    )
                if prepared_products:
                    execute_values(
                        cur,
                        """
                        INSERT INTO public.km_trade_sales_plan_product_yearly (
                            plan_year, sku, article, product_name, price_rub,
                            plan_units, plan_revenue
                        ) VALUES %s
                        ON CONFLICT (plan_year, sku) DO UPDATE SET
                            article = EXCLUDED.article,
                            product_name = EXCLUDED.product_name,
                            price_rub = EXCLUDED.price_rub,
                            plan_units = EXCLUDED.plan_units,
                            plan_revenue = EXCLUDED.plan_revenue,
                            updated_at = now()
                        """,
                        prepared_products,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "ok": True,
        "database": str(config.get("database") or TARGET_DB),
        "saved_months": len(prepared_months),
        "saved_products": len(prepared_products),
    }
