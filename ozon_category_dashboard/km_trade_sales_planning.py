"""Source-backed Ozon and Wildberries sales planning.

The demand forecast and the stock-constrained recommendation are deliberately
separate so inventory shortages stay visible instead of lowering demand silently.
Reads use the dashboard-selected client database while keeping the legacy
``/api/km-trade/*`` route names for compatibility.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import threading
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

from km_trade_finance import connect_km
from km_trade_planfact import add_months, available_range, iso, month_start, number, percent
from sales_coefficient_forecast import product_trend, build_coefficient_forecast


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def sales_fact_cutoff_date() -> date:
    """Return the last complete calendar day used by fact and Run Rate."""
    return date.today() - timedelta(days=1)


def query_float(
    params: dict[str, list[str]],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = (params.get(key) or [""])[0].strip()
    if not raw:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Некорректный коэффициент {key}") from exc
    return clamp(value, minimum, maximum)


def _market_trend_rows(cur: Any, marketplace: str) -> list[dict[str, Any]]:
    cur.execute("SELECT to_regclass('public.km_category_market_trends_mv') AS name")
    if not cur.fetchone()["name"]:
        return []
    cur.execute(
        """
        SELECT
            marketplace,
            category_name,
            mpstats_niche_id,
            mpstats_niche_name,
            month_start,
            month_number,
            sales_qty,
            revenue_rub,
            seasonality_coefficient,
            market_trend_coefficient,
            source_period_to,
            refreshed_at
        FROM public.km_category_market_trends_mv
        WHERE marketplace = %s
        ORDER BY category_name, month_start
        """,
        (marketplace,),
    )
    return [dict(row) for row in cur.fetchall()]


def _market_daily_rows(cur: Any, marketplace: str) -> list[dict[str, Any]]:
    cur.execute("SELECT to_regclass('public.km_category_market_daily') AS name")
    if not cur.fetchone()["name"]:
        return []
    cur.execute(
        """
        SELECT
            marketplace,
            category_name,
            mpstats_niche_id,
            mpstats_niche_name,
            report_date,
            sales_qty,
            revenue_rub,
            balance_qty,
            items_qty,
            items_with_sales_qty,
            brands_qty,
            sellers_qty,
            average_sale_price_rub,
            source_name,
            fetched_at
        FROM public.km_category_market_daily
        WHERE marketplace = %s
        ORDER BY category_name, report_date
        """,
        (marketplace,),
    )
    return [dict(row) for row in cur.fetchall()]


def _promotion_rows(cur: Any, marketplace: str) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT to_regclass('public.km_sales_planning_promotion_coefficients') AS name"
    )
    if not cur.fetchone()["name"]:
        return []
    cur.execute(
        """
        SELECT marketplace, category_name, month_start, coefficient, updated_at
        FROM public.km_sales_planning_promotion_coefficients
        WHERE marketplace = %s
        ORDER BY category_name, month_start
        """,
        (marketplace,),
    )
    return [dict(row) for row in cur.fetchall()]


def _stock_history_rows(cur: Any, marketplace: str) -> list[dict[str, Any]]:
    """Return one sellable-stock observation per marketplace, date and SKU."""
    cur.execute("SELECT to_regclass('public.inventory_history_daily') AS name")
    if not cur.fetchone()["name"]:
        return []
    cur.execute(
        """
        SELECT
            snapshot_date,
            sku::text AS sku,
            coalesce(sum(stock_available_qty), 0) AS stock_available_qty
        FROM public.inventory_history_daily
        WHERE marketplace = %s
          AND nullif(sku, '') IS NOT NULL
        GROUP BY snapshot_date, sku::text
        ORDER BY sku::text, snapshot_date
        """,
        (marketplace,),
    )
    return [dict(row) for row in cur.fetchall()]


def _oos_adjusted_daily_rates(
    daily_sales: list[dict[str, Any]],
    stock_history: list[dict[str, Any]],
    selected_skus: set[str],
    period_from: date,
    period_to: date,
) -> dict[str, dict[date, dict[str, float | int | None]]]:
    """Calculate SKU order velocity using only calendar days with sellable stock.

    The last observed stock state is carried forward until the next snapshot. Days
    before a SKU's first stock observation are unknown and therefore excluded.
    """
    if period_to < period_from:
        return {}
    sales_by_sku_date: dict[tuple[str, date], float] = defaultdict(float)
    for row in daily_sales:
        sku = str(row.get("sku") or "")
        sale_date = row.get("sale_date")
        if sku in selected_skus and isinstance(sale_date, date):
            sales_by_sku_date[(sku, sale_date)] += number(row.get("units"))

    observations_by_sku: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in stock_history:
        sku = str(row.get("sku") or "")
        snapshot_date = row.get("snapshot_date")
        if sku in selected_skus and isinstance(snapshot_date, date):
            observations_by_sku[sku].append(
                (snapshot_date, max(number(row.get("stock_available_qty")), 0))
            )

    result: dict[str, dict[date, dict[str, float | int | None]]] = defaultdict(dict)
    for sku, observations in observations_by_sku.items():
        observations.sort(key=lambda item: item[0])
        cursor = max(period_from, observations[0][0])
        observation_index = 0
        current_stock: float | None = None
        buckets: dict[date, dict[str, float]] = defaultdict(
            lambda: {
                "in_stock_units": 0.0,
                "in_stock_days": 0.0,
                "stock_covered_days": 0.0,
            }
        )
        while cursor <= period_to:
            while (
                observation_index < len(observations)
                and observations[observation_index][0] <= cursor
            ):
                current_stock = observations[observation_index][1]
                observation_index += 1
            if current_stock is not None:
                bucket = buckets[month_start(cursor)]
                bucket["stock_covered_days"] += 1
                if current_stock > 0:
                    bucket["in_stock_days"] += 1
                    bucket["in_stock_units"] += max(
                        sales_by_sku_date.get((sku, cursor), 0.0),
                        0.0,
                    )
            cursor += timedelta(days=1)
        for month_value, bucket in buckets.items():
            in_stock_days = int(bucket["in_stock_days"])
            result[sku][month_value] = {
                "oos_adjusted_daily_units": (
                    round(bucket["in_stock_units"] / in_stock_days, 4)
                    if in_stock_days > 0
                    else None
                ),
                "oos_in_stock_days": in_stock_days,
                "oos_stock_covered_days": int(bucket["stock_covered_days"]),
            }
    return dict(result)


def _detail_date_range(
    params: dict[str, list[str]],
    available_from: date,
    available_to: date,
) -> tuple[date, date]:
    fallback_to = available_to
    fallback_from = max(available_from, fallback_to - timedelta(days=29))

    def parse_value(key: str, fallback: date) -> date:
        raw = (params.get(key) or [""])[0].strip()
        if not raw:
            return fallback
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise ValueError(f"Некорректная дата {key}") from exc

    detail_to = min(parse_value("date_to", fallback_to), available_to)
    detail_from = max(parse_value("date_from", fallback_from), available_from)
    if detail_from > detail_to:
        raise ValueError("Дата начала детализации позже даты окончания")
    if (detail_to - detail_from).days > 365:
        detail_from = detail_to - timedelta(days=365)
    return detail_from, detail_to


def _seasonality_detail(
    label: str,
    categories: set[str],
    daily_rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    selected_rows = [
        row
        for row in daily_rows
        if str(row.get("category_name") or "Без категории") in categories
        and date_from <= row["report_date"] <= date_to
    ]
    by_date: dict[date, dict[str, float]] = defaultdict(
        lambda: {"sales_qty": 0.0, "revenue_rub": 0.0}
    )
    for row in selected_rows:
        bucket = by_date[row["report_date"]]
        bucket["sales_qty"] += number(row.get("sales_qty"))
        bucket["revenue_rub"] += number(row.get("revenue_rub"))
    average_sales = (
        sum(row["sales_qty"] for row in by_date.values()) / len(by_date)
        if by_date
        else 0.0
    )
    points: list[dict[str, Any]] = []
    previous_sales = 0.0
    for report_date, values in sorted(by_date.items()):
        sales_qty = values["sales_qty"]
        points.append(
            {
                "report_date": report_date.isoformat(),
                "date_label": report_date.strftime("%d.%m"),
                "sales_qty": round(sales_qty, 2),
                "revenue_rub": round(values["revenue_rub"], 2),
                "seasonality_index": round(sales_qty / average_sales, 3)
                if average_sales > 0
                else 1.0,
                "change_pct": round((sales_qty / previous_sales - 1) * 100, 1)
                if previous_sales > 0
                else None,
            }
        )
        previous_sales = sales_qty
    niche_ids = {
        str(row.get("mpstats_niche_id") or "")
        for row in selected_rows
        if row.get("mpstats_niche_id")
    }
    niche_names = {
        str(row.get("mpstats_niche_name") or "")
        for row in selected_rows
        if row.get("mpstats_niche_name")
    }
    return {
        "category_name": label,
        "period_start": date_from.isoformat(),
        "period_end": date_to.isoformat(),
        "points": points,
        "average_daily_sales": round(average_sales, 2),
        "mpstats_niche_id": next(iter(niche_ids)) if len(niche_ids) == 1 else None,
        "mpstats_niche_name": next(iter(niche_names)) if len(niche_names) == 1 else "",
        "source": "MPStats Analytics API · Ozon niche/by_date · groupBy=day",
        "index_method": "Продажи дня / средние дневные продажи выбранного периода",
    }


MONTH_NAMES_RU = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
MONTH_SHORT_NAMES_RU = (
    "Янв.",
    "Фев.",
    "Мар.",
    "Апр.",
    "Май",
    "Июн.",
    "Июл.",
    "Авг.",
    "Сен.",
    "Окт.",
    "Ноя.",
    "Дек.",
)


def _monthly_seasonality_detail(
    label: str,
    categories: set[str],
    market_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_rows = [
        row
        for row in market_rows
        if str(row.get("category_name") or "Без категории") in categories
    ]
    points: list[dict[str, Any]] = []
    for month_number in range(1, 13):
        month_rows = [
            row for row in selected_rows if int(row.get("month_number") or 0) == month_number
        ]
        if not month_rows:
            continue
        totals_by_period: dict[date, dict[str, float]] = defaultdict(
            lambda: {"sales_qty": 0.0, "revenue_rub": 0.0}
        )
        coefficients_by_category: dict[str, list[float]] = defaultdict(list)
        for row in month_rows:
            month_value = row.get("month_start")
            if month_value:
                totals_by_period[month_value]["sales_qty"] += number(row.get("sales_qty"))
                totals_by_period[month_value]["revenue_rub"] += number(row.get("revenue_rub"))
            coefficients_by_category[str(row.get("category_name") or "Без категории")].append(
                number(row.get("seasonality_coefficient")) or 1.0
            )
        category_coefficients = [
            sum(values) / len(values)
            for values in coefficients_by_category.values()
            if values
        ]
        period_values = list(totals_by_period.values())
        points.append(
            {
                "month_number": month_number,
                "month_label": MONTH_NAMES_RU[month_number - 1],
                "month_short_label": MONTH_SHORT_NAMES_RU[month_number - 1],
                "average_sales_qty": round(
                    sum(row["sales_qty"] for row in period_values) / len(period_values), 2
                ) if period_values else 0.0,
                "average_revenue_rub": round(
                    sum(row["revenue_rub"] for row in period_values) / len(period_values), 2
                ) if period_values else 0.0,
                "seasonality_index": round(
                    sum(category_coefficients) / len(category_coefficients), 3
                ) if category_coefficients else 1.0,
                "observations": len(period_values),
            }
        )
    niche_ids = {
        str(row.get("mpstats_niche_id") or "")
        for row in selected_rows
        if row.get("mpstats_niche_id")
    }
    niche_names = {
        str(row.get("mpstats_niche_name") or "")
        for row in selected_rows
        if row.get("mpstats_niche_name")
    }
    return {
        "category_name": label,
        "period_start": iso(min((row.get("month_start") for row in selected_rows if row.get("month_start")), default=None)),
        "period_end": iso(max((row.get("source_period_to") for row in selected_rows if row.get("source_period_to")), default=None)),
        "points": points,
        "mpstats_niche_id": next(iter(niche_ids)) if len(niche_ids) == 1 else None,
        "mpstats_niche_name": next(iter(niche_names)) if len(niche_names) == 1 else "",
        "source": "MPStats Analytics API · Ozon niche/trends · groupBy=month",
        "index_method": "Средние продажи календарного месяца / среднемесячные продажи ниши",
    }


def save_promotion_coefficients(
    config: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    marketplace = str(payload.get("marketplace") or "ozon").strip().lower()
    if marketplace not in {"ozon", "wb"}:
        raise ValueError("Маркетплейс должен быть ozon или wb")
    category_name = str(payload.get("category_name") or "__all__").strip()
    if not category_name or len(category_name) > 250:
        raise ValueError("Некорректная категория коэффициента продвижения")
    raw_rows = payload.get("coefficients")
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > 24:
        raise ValueError("Передайте от 1 до 24 помесячных коэффициентов")
    normalized: list[tuple[str, str, date, float]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            raise ValueError("Некорректная строка коэффициента продвижения")
        try:
            month_value = date.fromisoformat(str(row.get("month_start") or "")[:10])
            coefficient = float(str(row.get("coefficient") or "").replace(",", "."))
        except (TypeError, ValueError) as exc:
            raise ValueError("Месяц или коэффициент продвижения заполнен неверно") from exc
        if month_value.day != 1:
            raise ValueError("Месяц коэффициента должен начинаться с первого числа")
        if not math.isfinite(coefficient) or coefficient < 0.1:
            raise ValueError("Уровень продвижения должен быть не меньше 0,10")
        normalized.append((marketplace, category_name, month_value, round(coefficient, 4)))
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.km_sales_planning_promotion_coefficients (
                marketplace, category_name, month_start, coefficient, updated_at
            ) VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (marketplace, category_name, month_start) DO UPDATE SET
                coefficient = EXCLUDED.coefficient,
                updated_at = now()
            """,
            normalized,
        )
    return {
        "ok": True,
        "marketplace": marketplace,
        "category_name": category_name,
        "coefficients": [
            {"month_start": month_value.isoformat(), "coefficient": coefficient}
            for _, _, month_value, coefficient in normalized
        ],
    }


def _rolling_decade_windows(source_date: date) -> list[tuple[date, date]]:
    start = source_date - timedelta(days=29)
    return [
        (start + timedelta(days=offset), start + timedelta(days=offset + 9))
        for offset in (0, 10, 20)
    ]


def _trend_detail(
    label: str,
    skus: set[str],
    daily_sales: list[dict[str, Any]],
    source_date: date,
    market_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    windows = _rolling_decade_windows(source_date)
    decades: list[dict[str, Any]] = []
    for index, (date_from, date_to) in enumerate(windows, start=1):
        rows = [
            row
            for row in daily_sales
            if str(row.get("sku") or "") in skus
            and date_from <= row["sale_date"] <= date_to
        ]
        units = sum(number(row.get("units")) for row in rows)
        revenue = sum(number(row.get("revenue")) for row in rows)
        decades.append(
            {
                "decade": index,
                "period_start": date_from.isoformat(),
                "period_end": date_to.isoformat(),
                "period_label": f"{date_from.strftime('%d.%m')}–{date_to.strftime('%d.%m')}",
                "sales_units": round(units, 2),
                "sales_revenue": round(revenue, 2),
            }
        )
    previous_average = (
        (number(decades[0]["sales_units"]) + number(decades[1]["sales_units"])) / 2
    )
    latest_units = number(decades[2]["sales_units"])
    coefficient = latest_units / previous_average if previous_average > 0 else 1.0
    previous_units = number(decades[1]["sales_units"])
    for index, row in enumerate(decades):
        if index == 0:
            row["change_pct"] = None
            continue
        base = number(decades[index - 1]["sales_units"])
        row["change_pct"] = round((number(row["sales_units"]) / base - 1) * 100, 1) if base else None

    period_start = windows[0][0]
    period_end = windows[-1][1]
    daily_buckets: dict[date, dict[str, float]] = defaultdict(
        lambda: {"sales_units": 0.0, "sales_revenue": 0.0}
    )
    for row in daily_sales:
        sale_date = row.get("sale_date")
        if (
            str(row.get("sku") or "") not in skus
            or not sale_date
            or sale_date < period_start
            or sale_date > period_end
        ):
            continue
        daily_buckets[sale_date]["sales_units"] += number(row.get("units"))
        daily_buckets[sale_date]["sales_revenue"] += number(row.get("revenue"))

    daily_points: list[dict[str, Any]] = []
    previous_units: float | None = None
    for offset in range((period_end - period_start).days + 1):
        report_date = period_start + timedelta(days=offset)
        values = daily_buckets[report_date]
        sales_units = values["sales_units"]
        daily_points.append(
            {
                "report_date": report_date.isoformat(),
                "date_label": report_date.strftime("%d.%m"),
                "decade": offset // 10 + 1,
                "sales_units": round(sales_units, 2),
                "sales_revenue": round(values["sales_revenue"], 2),
                "change_pct": (
                    round((sales_units / previous_units - 1) * 100, 1)
                    if previous_units is not None and previous_units > 0
                    else None
                ),
            }
        )
        previous_units = sales_units
    niche_ids = {str(row.get("mpstats_niche_id") or "") for row in market_rows if row.get("mpstats_niche_id")}
    niche_names = {str(row.get("mpstats_niche_name") or "") for row in market_rows if row.get("mpstats_niche_name")}
    return {
        "category_name": label,
        "trend_coefficient": round(coefficient, 3),
        "latest_vs_previous_pct": (
            round((latest_units / previous_units - 1) * 100, 1)
            if previous_units > 0
            else None
        ),
        "period_start": windows[0][0].isoformat(),
        "period_end": windows[-1][1].isoformat(),
        "decades": decades,
        "daily_points": daily_points,
        "mpstats_niche_id": next(iter(niche_ids)) if len(niche_ids) == 1 else None,
        "mpstats_niche_name": next(iter(niche_names)) if len(niche_names) == 1 else "",
        "market_source_period_to": iso(
            max(
                (row.get("source_period_to") for row in market_rows if row.get("source_period_to")),
                default=None,
            )
        ),
        "source": "Фактические продажи выбранного клиента",
    }


def _available_range(config: dict[str, Any], marketplace: str) -> tuple[date, date]:
    if marketplace == "ozon":
        return available_range(config)
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT min(report_date) AS date_from, max(report_date) AS date_to
            FROM public.wb_funnel_daily
            """
        )
        row = cur.fetchone()
    today = date.today()
    return row["date_from"] or today, row["date_to"] or today


def _forecast_sources(config: dict[str, Any], marketplace: str) -> dict[str, Any]:
    from pulse_supply import read_register
    from pulse_sales_model import versions
    from sales_activity import read_settings
    from search_demand import read_sources
    sources = _forecast_sources_base(config, marketplace)
    with connect_km(config) as conn:
        policy, supplies = read_register(conn, marketplace)
        activity_settings = read_settings(conn, marketplace)
        demand_sources = read_sources(conn) if marketplace == "wb" else ([], [])
    sources.update(demand_sources=demand_sources, supply_policy=policy, confirmed_supplies=supplies, activity_settings=activity_settings,
                   plan_versions=versions(config, marketplace))
    return sources


def _forecast_sources_base(config: dict[str, Any], marketplace: str) -> dict[str, Any]:
    if marketplace == "wb":
        with connect_km(config) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    wb_nmid::text AS sku,
                    max(nullif(seller_article, '')) AS article,
                    max(nullif(product_name, '')) AS product_name,
                    max(nullif(category_name, '')) AS category_name
                FROM public.wb_funnel_daily
                WHERE nullif(wb_nmid, '') IS NOT NULL
                GROUP BY wb_nmid::text
                """
            )
            products = {str(row["sku"]): dict(row) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT
                    date_trunc('month', report_date)::date AS month_start,
                    wb_nmid::text AS sku,
                    max(nullif(seller_article, '')) AS article,
                    max(nullif(product_name, '')) AS product_name,
                    coalesce(sum(ordered_units), 0) AS units,
                    coalesce(sum(ordered_amount_rub), 0) AS revenue
                FROM public.wb_funnel_daily
                WHERE nullif(wb_nmid, '') IS NOT NULL AND report_date <= %s
                GROUP BY date_trunc('month', report_date)::date, wb_nmid::text
                ORDER BY month_start, sku
                """, (sales_fact_cutoff_date(),)
            )
            sales = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT
                    report_date AS sale_date,
                    wb_nmid::text AS sku,
                    coalesce(sum(ordered_units), 0) AS units,
                    coalesce(sum(ordered_amount_rub), 0) AS revenue
                FROM public.wb_funnel_daily
                WHERE nullif(wb_nmid, '') IS NOT NULL AND report_date <= %s
                GROUP BY report_date, wb_nmid::text
                ORDER BY report_date, sku
                """, (sales_fact_cutoff_date(),)
            )
            daily_sales = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT
                    wb_nmid::text AS sku,
                    nullif(seller_article, '') AS article,
                    CASE
                        WHEN coalesce(price_min_rub, 0) > 0 AND coalesce(price_max_rub, 0) > 0
                            THEN (price_min_rub + price_max_rub) / 2
                        WHEN coalesce(price_min_rub, 0) > 0 THEN price_min_rub
                        WHEN coalesce(price_max_rub, 0) > 0 THEN price_max_rub
                        WHEN coalesce(buyouts_qty, 0) > 0 THEN buyouts_amount_rub / buyouts_qty
                        WHEN coalesce(orders_qty, 0) > 0 THEN orders_amount_rub / orders_qty
                        ELSE 0
                    END AS price_rub
                FROM public.wb_stock_api_current
                WHERE nullif(wb_nmid, '') IS NOT NULL
                """
            )
            prices = {str(row["sku"]): dict(row) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT
                    wb_nmid::text AS sku,
                    nullif(seller_article, '') AS article,
                    nullif(product_name, '') AS product_name,
                    coalesce(stock_qty, 0) AS stock_available_qty,
                    0::numeric AS stock_preparing_qty,
                    snapshot_date
                FROM public.wb_stock_api_current
                WHERE nullif(wb_nmid, '') IS NOT NULL
                """
            )
            stocks = {str(row["sku"]): dict(row) for row in cur.fetchall()}
            market_trends = _market_trend_rows(cur, marketplace)
            promotion_coefficients = _promotion_rows(cur, marketplace)
            stock_history = _stock_history_rows(cur, marketplace)
        return {
            "products": products,
            "sales": sales,
            "daily_sales": daily_sales,
            "stock_history": stock_history,
            "prices": prices,
            "stocks": stocks,
            "logistics": {},
            "market_trends": market_trends,
            "promotion_coefficients": promotion_coefficients,
            "approved_monthly_plans": [],
            "approved_product_yearly": {},
        }

    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (sku)
                sku,
                nullif(artikul, '') AS article,
                nullif(nazvanie_tovara, '') AS product_name,
                nullif(kategoriya, '') AS category_name
            FROM public.ozon_products
            WHERE nullif(sku, '') IS NOT NULL
            ORDER BY sku, imported_at DESC, id DESC
            """
        )
        products = {str(row["sku"]): dict(row) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT
                date_trunc('month', operation_date)::date AS month_start,
                sku,
                max(article) FILTER (WHERE nullif(article, '') IS NOT NULL) AS article,
                max(product_name) FILTER (WHERE nullif(product_name, '') IS NOT NULL) AS product_name,
                coalesce(sum(quantity), 0) AS units,
                coalesce(sum(amount), 0) AS revenue
            FROM public.ozon_finance_lines
            WHERE line_kind = 'revenue'
              AND nullif(sku, '') IS NOT NULL
            GROUP BY date_trunc('month', operation_date)::date, sku
            ORDER BY month_start, sku
            """
        )
        sales = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT
                operation_date::date AS sale_date,
                sku,
                coalesce(sum(quantity), 0) AS units,
                coalesce(sum(amount), 0) AS revenue
            FROM public.ozon_finance_lines
            WHERE line_kind = 'revenue'
              AND nullif(sku, '') IS NOT NULL
            GROUP BY operation_date::date, sku
            ORDER BY sale_date, sku
            """
        )
        daily_sales = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT DISTINCT ON (sku)
                sku,
                nullif(offer_id, '') AS article,
                coalesce(marketing_seller_price, price, 0) AS price_rub
            FROM public.ozon_product_price_snapshots
            WHERE nullif(sku, '') IS NOT NULL
            ORDER BY sku, snapshot_date DESC
            """
        )
        prices = {str(row["sku"]): dict(row) for row in cur.fetchall()}
        cur.execute(
            """
            WITH latest AS (
                SELECT max(snapshot_date) AS snapshot_date
                FROM public.inventory_history_daily
                WHERE marketplace = 'ozon'
            )
            SELECT
                sku,
                max(seller_article) FILTER (WHERE nullif(seller_article, '') IS NOT NULL) AS article,
                max(product_name) FILTER (WHERE nullif(product_name, '') IS NOT NULL) AS product_name,
                coalesce(sum(stock_available_qty), 0) AS stock_available_qty,
                coalesce(sum(stock_preparing_qty), 0) AS stock_preparing_qty,
                max(snapshot_date) AS snapshot_date
            FROM public.inventory_history_daily
            WHERE marketplace = 'ozon'
              AND snapshot_date = (SELECT snapshot_date FROM latest)
            GROUP BY sku
            """
        )
        stocks = {str(row["sku"]): dict(row) for row in cur.fetchall()}
        cur.execute(
            """
            WITH logistics_date AS (
                SELECT max(snapshot_date) AS snapshot_date
                FROM public.inventory_history_daily
                WHERE marketplace = 'ozon'
                  AND (
                    coalesce(in_supply_orders_qty, 0)
                    + coalesce(in_transit_supply_qty, 0)
                    + coalesce(returning_from_customers_qty, 0)
                  ) > 0
            )
            SELECT
                sku,
                coalesce(sum(in_supply_orders_qty), 0) AS in_supply_orders_qty,
                coalesce(sum(in_transit_supply_qty), 0) AS in_transit_supply_qty,
                coalesce(sum(returning_from_customers_qty), 0) AS returning_from_customers_qty,
                max(snapshot_date) AS logistics_snapshot_date
            FROM public.inventory_history_daily
            WHERE marketplace = 'ozon'
              AND snapshot_date = (SELECT snapshot_date FROM logistics_date)
            GROUP BY sku
            """
        )
        logistics = {str(row["sku"]): dict(row) for row in cur.fetchall()}
        market_trends = _market_trend_rows(cur, marketplace)
        promotion_coefficients = _promotion_rows(cur, marketplace)
        stock_history = _stock_history_rows(cur, marketplace)
        approved_monthly_plans: list[dict[str, Any]] = []
        approved_product_yearly: dict[str, dict[str, Any]] = {}
        cur.execute(
            "SELECT to_regclass('public.km_trade_sales_plan_monthly') AS table_name"
        )
        if cur.fetchone()["table_name"]:
            cur.execute(
                """
                SELECT
                    month_start,
                    coalesce(sales_plan_units, 0) AS plan_units,
                    coalesce(sales_plan_rub, 0) AS plan_revenue
                FROM public.km_trade_sales_plan_monthly
                ORDER BY month_start
                """
            )
            approved_monthly_plans = [dict(row) for row in cur.fetchall()]
        cur.execute(
            "SELECT to_regclass('public.km_trade_sales_plan_product_yearly') AS table_name"
        )
        if cur.fetchone()["table_name"]:
            cur.execute(
                """
                SELECT
                    plan_year,
                    sku::text AS sku,
                    coalesce(plan_units, 0) AS plan_units,
                    coalesce(plan_revenue, 0) AS plan_revenue
                FROM public.km_trade_sales_plan_product_yearly
                """
            )
            approved_product_yearly = {
                f"{row['plan_year']}:{row['sku']}": dict(row)
                for row in cur.fetchall()
            }
    return {
        "products": products,
        "sales": sales,
        "daily_sales": daily_sales,
        "stock_history": stock_history,
        "prices": prices,
        "stocks": stocks,
        "logistics": logistics,
        "market_trends": market_trends,
        "promotion_coefficients": promotion_coefficients,
        "approved_monthly_plans": approved_monthly_plans,
        "approved_product_yearly": approved_product_yearly,
    }


def _sales_forecast_payload_uncached(config: dict[str, Any], raw_query: str = "", *, include_all_products: bool = False) -> dict[str, Any]:
    params = parse_qs(raw_query)
    client_key = (params.get("client") or ["km_trade"])[0].strip().lower() or "km_trade"
    marketplace = (params.get("marketplace") or ["ozon"])[0].strip().lower()
    if marketplace not in {"ozon", "wb"}:
        raise ValueError("Маркетплейс должен быть ozon или wb")
    growth_pct = query_float(params, "growth_pct", 0.0, -50.0, 100.0)
    trend_weight_pct = query_float(params, "trend_weight_pct", 60.0, 0.0, 100.0)
    stock_weight_pct = query_float(params, "stock_weight_pct", 30.0, 0.0, 100.0)
    seasonality_pct = query_float(params, "seasonality_pct", 0.0, -50.0, 100.0)
    safety_stock_days = query_float(params, "safety_stock_days", 21.0, 0.0, 180.0)
    category_filter = (params.get("category") or [""])[0].strip()
    product_filter = (params.get("product") or [""])[0].strip()
    article_filter = (
        params.get("article") or params.get("q") or [""]
    )[0].strip().lower()

    available_from, available_to = _available_range(config, marketplace)
    available_to = min(available_to, sales_fact_cutoff_date())
    anchor_month = month_start(available_to)
    year_start = date(anchor_month.year, 1, 1)
    year_end = date(anchor_month.year, 12, 31)
    rolling = (params.get("horizon") or [""])[0] == "rolling"
    if rolling:
        year_start = min(date(anchor_month.year, 1, 1), month_start(available_from))
        year_end = add_months(anchor_month, 12) - timedelta(days=1)
    actual_source_month_start = month_start(available_from)
    next_month = add_months(anchor_month, 1)
    previous_month = add_months(anchor_month, -1)
    sources = _forecast_sources(config, marketplace)
    from sales_planning_workbench import validate_overrides
    saved_snapshot = next((v.get('assumptions', {}).get('snapshot') for v in sources.get('plan_versions', []) if v.get('assumptions', {}).get('snapshot', {}).get('metric') == ('orders' if marketplace == 'wb' else 'finance_sales')), {}) or {}
    overrides = validate_overrides(json.loads(params['coefficient_overrides'][0]) if 'coefficient_overrides' in params else saved_snapshot.get('coefficient_overrides', [])) if rolling else []
    from sales_activity import merge_overrides
    if rolling:
        overrides = merge_overrides(overrides, sources.get('activity_settings', []))
    override_lookup = {(r['scope'], r['entity'], r['metric'], r['month_start']): r['value'] for r in overrides}
    def factor(sku, category, metric, month, default):
        return override_lookup.get(('sku', sku, metric, month.isoformat()), override_lookup.get(('category', category, metric, month.isoformat()), default))
    approved_monthly_lookup = {
        row["month_start"]: row
        for row in sources.get("approved_monthly_plans", [])
        if row.get("month_start") and row["month_start"].year == anchor_month.year
    }
    approved_product_lookup = {
        key.split(":", 1)[1]: row
        for key, row in sources.get("approved_product_yearly", {}).items()
        if key.startswith(f"{anchor_month.year}:")
    }
    approved_year_units = sum(
        max(number(row.get("plan_units")), 0)
        for row in approved_product_lookup.values()
    )
    approved_year_revenue = sum(
        max(number(row.get("plan_revenue")), 0)
        for row in approved_product_lookup.values()
    )

    product_meta: dict[str, dict[str, Any]] = {
        sku: {
            "sku": sku,
            "article": row.get("article") or "",
            "product_name": row.get("product_name") or "",
            "category_name": row.get("category_name") or "Без категории",
        }
        for sku, row in sources["products"].items()
    }
    monthly_by_sku: dict[str, dict[date, dict[str, float]]] = defaultdict(dict)
    for row in sources["sales"]:
        sku = str(row["sku"])
        meta = product_meta.setdefault(
            sku,
            {
                "sku": sku,
                "article": row.get("article") or "",
                "product_name": row.get("product_name") or "",
                "category_name": "Без категории",
            },
        )
        if not meta["article"] and row.get("article"):
            meta["article"] = row["article"]
        if not meta["product_name"] and row.get("product_name"):
            meta["product_name"] = row["product_name"]
        monthly_by_sku[sku][row["month_start"]] = {
            "units": number(row["units"]),
            "revenue": number(row["revenue"]),
        }
    for source_key in ("prices", "stocks"):
        for sku, row in sources[source_key].items():
            meta = product_meta.setdefault(
                sku,
                {
                    "sku": sku,
                    "article": "",
                    "product_name": "",
                    "category_name": "Без категории",
                },
            )
            if not meta["article"] and row.get("article"):
                meta["article"] = row["article"]
            if not meta["product_name"] and row.get("product_name"):
                meta["product_name"] = row["product_name"]

    all_categories = sorted(
        {str(row["category_name"]) for row in product_meta.values() if row["category_name"]}
    )
    all_products = sorted(
        {str(row["product_name"]) for row in product_meta.values() if row["product_name"]}
    )

    def selected(meta: dict[str, Any]) -> bool:
        if category_filter and meta["category_name"] != category_filter:
            return False
        if product_filter and meta["product_name"] != product_filter:
            return False
        if article_filter:
            haystack = " ".join(
                str(meta.get(key) or "").lower()
                for key in ("sku", "article", "product_name", "category_name")
            )
            if article_filter not in haystack:
                return False
        return True

    selected_skus = [sku for sku, meta in product_meta.items() if selected(meta)]
    daily_sales = list(sources.get("daily_sales") or [])
    daily_sales_by_sku = defaultdict(list)
    for daily_row in daily_sales:
        daily_sales_by_sku[str(daily_row.get("sku") or "")].append(daily_row)
    stock_history = list(sources.get("stock_history") or [])
    oos_daily_rates = _oos_adjusted_daily_rates(
        daily_sales,
        stock_history,
        set(selected_skus),
        year_start,
        available_to,
    )
    oos_stock_history_dates = [
        row["snapshot_date"]
        for row in stock_history
        if str(row.get("sku") or "") in selected_skus
        and isinstance(row.get("snapshot_date"), date)
        and row["snapshot_date"] <= available_to
    ]
    oos_stock_history_from = (
        min(oos_stock_history_dates) if oos_stock_history_dates else None
    )
    oos_stock_history_to = (
        max(oos_stock_history_dates) if oos_stock_history_dates else None
    )
    market_trend_rows = list(sources.get("market_trends") or [])
    seasonality_sources = []
    if rolling and marketplace == "wb":
        from sales_planning_workbench import research_seasonality
        research_rows, seasonality_sources = research_seasonality(client_key)
        market_trend_rows = research_rows or market_trend_rows
    promotion_rows = list(sources.get("promotion_coefficients") or [])
    promotion_lookup = {
        (
            str(row.get("category_name") or "__all__"),
            row["month_start"],
        ): number(row.get("coefficient"))
        for row in promotion_rows
        if row.get("month_start")
    }
    market_rows_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_trend_rows:
        market_rows_by_category[str(row.get("category_name") or "Без категории")].append(row)
    selected_skus_by_category: dict[str, set[str]] = defaultdict(set)
    for sku in selected_skus:
        selected_skus_by_category[str(product_meta[sku]["category_name"])].add(sku)
    category_history_skus: dict[str, set[str]] = defaultdict(set)
    for sku, meta in product_meta.items():
        category_history_skus[str(meta["category_name"])].add(sku)
    # Trend evidence covers only the last three ten-day windows.  Passing the
    # complete daily history to every category used to rescan hundreds of
    # thousands of rows once per category.  Build the 30-day index once.
    trend_period_from = available_to - timedelta(days=29)
    recent_daily_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    recent_selected_daily: list[dict[str, Any]] = []
    selected_sku_set = set(selected_skus)
    for row in daily_sales:
        sale_date = row.get("sale_date")
        sku = str(row.get("sku") or "")
        if (
            not isinstance(sale_date, date)
            or sale_date < trend_period_from
            or sale_date > available_to
            or sku not in product_meta
        ):
            continue
        recent_daily_by_category[str(product_meta[sku]["category_name"])].append(row)
        if sku in selected_sku_set:
            recent_selected_daily.append(row)
    category_trends = [
        _trend_detail(
            category,
            category_history_skus[category],
            recent_daily_by_category.get(category, []),
            available_to,
            market_rows_by_category.get(category, []),
        )
        for category, skus in sorted(selected_skus_by_category.items())
    ]
    category_trend_lookup = {
        row["category_name"]: number(row["trend_coefficient"]) if row.get("trend_coefficient") is not None else 1.0
        for row in category_trends
    }
    overall_trend_detail = _trend_detail(
        category_filter or product_filter or "Все выбранные категории",
        selected_sku_set,
        recent_selected_daily,
        available_to,
        [
            row
            for category in selected_skus_by_category
            for row in market_rows_by_category.get(category, [])
        ],
    )
    category_seasonality_details = [
        _monthly_seasonality_detail(
            category,
            {category},
            market_trend_rows,
        )
        for category in sorted(selected_skus_by_category)
    ]
    overall_seasonality_detail = _monthly_seasonality_detail(
        category_filter or product_filter or "Все выбранные категории",
        set(selected_skus_by_category),
        market_trend_rows,
    )
    seasonality_by_category_month: dict[tuple[str, int], float] = {}
    for category, rows in market_rows_by_category.items():
        for row in rows:
            coefficient = number(row.get("seasonality_coefficient")) or 1.0
            seasonality_by_category_month[(category, int(row["month_number"]))] = coefficient

    def category_seasonality(category: str, target_month: date) -> float:
        level = seasonality_by_category_month.get((category, target_month.month), 1.0)
        previous = seasonality_by_category_month.get((category, add_months(target_month, -1).month))
        return level / previous if rolling and previous and previous > 0 else level

    def actual_source_available(target_month: date) -> bool:
        return actual_source_month_start <= target_month <= anchor_month

    elapsed_days = max(available_to.day, 1)
    days_in_anchor_month = calendar.monthrange(anchor_month.year, anchor_month.month)[1]
    complete_months = [add_months(anchor_month, offset) for offset in (-3, -2, -1)]
    trend_weight = trend_weight_pct / 100
    stock_weight = stock_weight_pct / 100
    growth_factor = 1 + growth_pct / 100
    manual_seasonality_factor = 1 + seasonality_pct / 100

    def promotion_level(category: str, target_month: date) -> float:
        return promotion_lookup.get(
            (category, target_month),
            promotion_lookup.get(("__all__", target_month), 1.0),
        )

    def promotion_coefficient(category: str, target_month: date) -> float:
        """Return the month-over-month factor implied by saved promotion levels."""
        current_level = promotion_level(category, target_month)
        previous_level = promotion_level(category, add_months(target_month, -1))
        return current_level / previous_level if previous_level > 0 else current_level

    promotion_settings_by_category: dict[str, dict[str, float]] = defaultdict(dict)
    for (scope, month_value), coefficient in promotion_lookup.items():
        promotion_settings_by_category[scope][month_value.isoformat()] = round(coefficient, 4)

    month_aggregates: dict[date, dict[str, float]] = defaultdict(
        lambda: {
            "actual_units": 0.0,
            "actual_revenue": 0.0,
            "oos_adjusted_daily_units": 0.0,
            "oos_adjusted_sku_count": 0.0,
            "oos_in_stock_days": 0.0,
            "oos_stock_covered_days": 0.0,
            "forecast_units": 0.0,
            "forecast_revenue": 0.0,
            "recommended_units": 0.0,
            "recommended_revenue": 0.0,
            "approved_plan_units": 0.0,
            "approved_plan_revenue": 0.0,
            "calculated_plan_units": 0.0,
            "calculated_plan_revenue": 0.0,
            "projected_start_stock": 0.0,
            "expected_supply_qty": 0.0,
            "projected_end_stock_without_supply": 0.0,
            "safety_stock_qty": 0.0,
            "recommended_supply_qty": 0.0,
            "projected_total_stock": 0.0,
            "projected_end_stock": 0.0,
            "planning_baseline_units": 0.0,
            "planning_after_growth_units": 0.0,
            "planning_after_trend_units": 0.0,
            "planning_after_seasonality_units": 0.0,
            "raw_trend_sum": 0.0,
            "weight_units": 0.0,
        }
    )
    product_rows: list[dict[str, Any]] = []
    coefficient_products: list[dict[str, Any]] = []
    for sku in selected_skus:
        meta = product_meta[sku]
        approved_product = approved_product_lookup.get(sku, {})
        approved_units_share = (
            max(number(approved_product.get("plan_units")), 0) / approved_year_units
            if approved_year_units > 0
            else None
        )
        approved_revenue_share = (
            max(number(approved_product.get("plan_revenue")), 0) / approved_year_revenue
            if approved_year_revenue > 0
            else None
        )
        sales_by_month = monthly_by_sku.get(sku, {})
        for actual_month, actual in sales_by_month.items():
            if year_start <= actual_month <= year_end:
                month_aggregates[actual_month]["actual_units"] += actual["units"]
                month_aggregates[actual_month]["actual_revenue"] += actual["revenue"]

        recent_units = [
            max(number(sales_by_month.get(month, {}).get("units")), 0)
            for month in complete_months
        ]
        recent_revenue = [
            max(number(sales_by_month.get(month, {}).get("revenue")), 0)
            for month in complete_months
        ]
        previous_actual_units = max(
            number(sales_by_month.get(previous_month, {}).get("units")), 0
        )
        previous_actual_revenue = max(
            number(sales_by_month.get(previous_month, {}).get("revenue")), 0
        )
        current_actual_units = max(
            number(sales_by_month.get(anchor_month, {}).get("units")), 0
        )
        current_actual_revenue = max(
            number(sales_by_month.get(anchor_month, {}).get("revenue")), 0
        )
        current_forecast_units = current_actual_units / elapsed_days * days_in_anchor_month
        current_forecast_revenue = current_actual_revenue / elapsed_days * days_in_anchor_month
        category_name = str(meta["category_name"])
        raw_trend = category_trend_lookup.get(category_name, 1.0)
        trend_evidence = product_trend(daily_sales_by_sku.get(sku, []), available_from, available_to, raw_trend)
        raw_trend = trend_evidence["raw"]
        bounded_trend = clamp(raw_trend, 0.7, 1.35)
        trend_factor = 1 + (bounded_trend - 1) * trend_weight
        current_seasonal_factor = (
            category_seasonality(category_name, anchor_month)
            * manual_seasonality_factor
        )
        current_seasonal_factor = factor(sku, category_name, 'seasonality', anchor_month, current_seasonal_factor)
        trend_factor = factor(sku, category_name, 'trend', anchor_month, trend_factor)
        current_growth = factor(sku, category_name, 'growth', anchor_month, growth_factor)
        current_promotion_factor = factor(sku, category_name, 'activity', anchor_month, promotion_coefficient(category_name, anchor_month))
        current_plan_after_growth_units = previous_actual_units * current_growth
        current_planning_coefficient = (
            current_growth
            * trend_factor
            * current_seasonal_factor
            * current_promotion_factor
        )
        current_plan_after_trend_units = current_plan_after_growth_units * trend_factor
        current_plan_after_seasonality_units = (
            current_plan_after_trend_units * current_seasonal_factor
        )
        current_calculated_plan_units = max(
            current_plan_after_seasonality_units * current_promotion_factor,
            0,
        )
        current_calculated_plan_revenue = max(
            previous_actual_revenue * current_planning_coefficient,
            0,
        )
        anchor_approved_month = approved_monthly_lookup.get(anchor_month, {})
        anchor_approved_plan_units = (
            max(number(anchor_approved_month.get("plan_units")), 0) * approved_units_share
            if approved_units_share is not None
            and number(anchor_approved_month.get("plan_units")) > 0
            else None
        )
        anchor_approved_plan_revenue = (
            max(number(anchor_approved_month.get("plan_revenue")), 0)
            * approved_revenue_share
            if approved_revenue_share is not None
            and number(anchor_approved_month.get("plan_revenue")) > 0
            else None
        )
        price_row = sources["prices"].get(sku, {})
        total_units_for_price = sum(recent_units) + current_actual_units
        total_revenue_for_price = sum(recent_revenue) + current_actual_revenue
        price_rub = (
            total_revenue_for_price / total_units_for_price
            if total_units_for_price > 0
            else number(price_row.get("price_rub"))
        )
        if price_rub <= 0 and current_forecast_units > 0:
            price_rub = current_forecast_revenue / current_forecast_units

        stock = sources["stocks"].get(sku, {})
        logistics = sources["logistics"].get(sku, {})
        stock_available = max(number(stock.get("stock_available_qty")), 0)
        stock_preparing = max(number(stock.get("stock_preparing_qty")), 0)
        inbound = max(number(logistics.get("in_supply_orders_qty")), 0)
        in_transit = max(number(logistics.get("in_transit_supply_qty")), 0)
        returning = max(number(logistics.get("returning_from_customers_qty")), 0)
        usable_stock = stock_available + stock_preparing + inbound + in_transit + returning

        anchor_aggregate = month_aggregates[anchor_month]
        anchor_aggregate["forecast_units"] += current_forecast_units
        anchor_aggregate["forecast_revenue"] += current_forecast_revenue
        anchor_aggregate["recommended_units"] += current_forecast_units
        anchor_aggregate["recommended_revenue"] += current_forecast_revenue
        anchor_aggregate["calculated_plan_units"] += current_calculated_plan_units
        anchor_aggregate["calculated_plan_revenue"] += current_calculated_plan_revenue
        anchor_aggregate["planning_baseline_units"] += previous_actual_units
        anchor_aggregate["planning_after_growth_units"] += current_plan_after_growth_units
        anchor_aggregate["planning_after_trend_units"] += current_plan_after_trend_units
        anchor_aggregate["planning_after_seasonality_units"] += current_plan_after_seasonality_units

        current_plan: dict[str, float] = {
            "calculated_plan_units": current_calculated_plan_units,
            "calculated_plan_revenue": current_calculated_plan_revenue,
            "planning_baseline_units": previous_actual_units,
            "effective_growth_coefficient": current_growth,
            "planning_after_growth_units": current_plan_after_growth_units,
            "effective_trend_coefficient": trend_factor,
            "planning_after_trend_units": current_plan_after_trend_units,
            "effective_seasonality_coefficient": current_seasonal_factor,
            "planning_after_seasonality_units": current_plan_after_seasonality_units,
            "planning_forecast_units": current_calculated_plan_units,
            "planning_coefficient": current_planning_coefficient,
        }
        future_by_month: dict[date, dict[str, float | None]] = {}
        rolling_baseline_units = current_forecast_units
        rolling_baseline_revenue = current_forecast_revenue
        # Coefficients cover a rolling year (M0..M11), including next calendar year.
        # Annual sales/inventory tables below retain their calendar-year horizon.
        for offset in range(1, 12):
            target_month = add_months(anchor_month, offset)
            first_month_growth = factor(sku, category_name, 'growth', target_month, growth_factor if offset == 1 else 1.0)
            monthly_trend = factor(sku, category_name, 'trend', target_month, 1 + (bounded_trend - 1) * trend_weight)
            seasonal_factor = (
                category_seasonality(category_name, target_month)
                * manual_seasonality_factor
            )
            seasonal_factor = factor(sku, category_name, 'seasonality', target_month, seasonal_factor)
            promotion_factor = factor(sku, category_name, 'activity', target_month, promotion_coefficient(category_name, target_month))
            planning_coefficient = (
                first_month_growth
                * monthly_trend
                * seasonal_factor
                * promotion_factor
            )
            baseline_units = rolling_baseline_units
            baseline_revenue = rolling_baseline_revenue
            planning_after_growth_units = baseline_units * first_month_growth
            planning_after_trend_units = planning_after_growth_units * monthly_trend
            planning_after_seasonality_units = (
                planning_after_trend_units * seasonal_factor
            )
            demand_units = max(
                planning_after_seasonality_units * promotion_factor,
                0,
            )
            demand_revenue = max(demand_units * price_rub if rolling else baseline_revenue * planning_coefficient, 0)
            stock_readiness = (
                min(usable_stock / demand_units, 1.0) if demand_units > 0 else 1.0
            )
            stock_factor = (
                (1 - stock_weight) + stock_weight * stock_readiness
                if offset == 1
                else 1.0
            )
            recommended_units = demand_units * stock_factor
            recommended_revenue = demand_revenue * stock_factor
            future_by_month[target_month] = {
                "demand_units": demand_units,
                "demand_revenue": demand_revenue,
                "recommended_units": recommended_units,
                "recommended_revenue": recommended_revenue,
                "stock_readiness": stock_readiness if offset == 1 else None,
                "promotion_factor": promotion_factor,
                "effective_trend_coefficient": monthly_trend,
                "effective_seasonality_coefficient": seasonal_factor,
                "planning_baseline_units": baseline_units,
                "effective_growth_coefficient": first_month_growth,
                "planning_after_growth_units": planning_after_growth_units,
                "planning_after_trend_units": planning_after_trend_units,
                "planning_after_seasonality_units": planning_after_seasonality_units,
                "planning_forecast_units": demand_units,
                "planning_coefficient": planning_coefficient,
            }
            aggregate = month_aggregates[target_month]
            aggregate["forecast_units"] += demand_units
            aggregate["forecast_revenue"] += demand_revenue
            aggregate["recommended_units"] += recommended_units
            aggregate["recommended_revenue"] += recommended_revenue
            aggregate["planning_baseline_units"] += baseline_units
            aggregate["planning_after_growth_units"] += planning_after_growth_units
            aggregate["planning_after_trend_units"] += planning_after_trend_units
            aggregate["planning_after_seasonality_units"] += planning_after_seasonality_units
            aggregate["raw_trend_sum"] += raw_trend * max(demand_units, 1)
            aggregate["weight_units"] += max(demand_units, 1)
            rolling_baseline_units = demand_units
            rolling_baseline_revenue = demand_revenue

        coefficient_months = []
        for offset in range(12):
            target_month = add_months(anchor_month, offset)
            values = current_plan if offset == 0 else future_by_month[target_month]
            coefficient_months.append({
                "month_start": target_month.isoformat(),
                **{key: number(values.get(key)) for key in (
                    "planning_baseline_units", "planning_after_growth_units",
                    "planning_after_trend_units", "planning_after_seasonality_units",
                    "planning_forecast_units",
                )},
                "planning_coefficient": round(number(values.get("planning_coefficient")), 4),
                **{key: round(number(values.get(key)), 6) for key in (
                    "effective_growth_coefficient", "effective_trend_coefficient",
                    "effective_seasonality_coefficient",
                )},
                "promotion_coefficient": round(factor(sku, category_name, 'activity', target_month, promotion_coefficient(category_name, target_month)), 4),
            })
        coefficient_products.append({**meta, "trend_evidence": trend_evidence, "monthly": coefficient_months})

        product_months: list[dict[str, Any]] = []
        remaining_current_demand = max(current_forecast_units - current_actual_units, 0)
        current_expected_supply = stock_preparing + inbound + in_transit + returning
        carried_stock = stock_available
        cursor = year_start
        while cursor <= year_end:
            actual = sales_by_month.get(cursor, {})
            actual_units = max(number(actual.get("units")), 0)
            actual_revenue = max(number(actual.get("revenue")), 0)
            oos_rate = oos_daily_rates.get(sku, {}).get(cursor, {})
            oos_adjusted_daily_units = oos_rate.get("oos_adjusted_daily_units")
            oos_in_stock_days = int(number(oos_rate.get("oos_in_stock_days")))
            oos_stock_covered_days = int(number(oos_rate.get("oos_stock_covered_days")))
            if oos_adjusted_daily_units is not None:
                oos_aggregate = month_aggregates[cursor]
                oos_aggregate["oos_adjusted_daily_units"] += number(
                    oos_adjusted_daily_units
                )
                oos_aggregate["oos_adjusted_sku_count"] += 1
                oos_aggregate["oos_in_stock_days"] += oos_in_stock_days
                oos_aggregate["oos_stock_covered_days"] += oos_stock_covered_days
            future = future_by_month.get(cursor, {})
            is_current = cursor == anchor_month
            is_future = cursor > anchor_month
            planning = current_plan if is_current else future if is_future else {}
            forecast_units = (
                current_forecast_units
                if is_current
                else number(future.get("demand_units")) if is_future else actual_units
            )
            forecast_revenue = (
                current_forecast_revenue
                if is_current
                else number(future.get("demand_revenue")) if is_future else actual_revenue
            )
            projected_start_stock: float | None = None
            expected_supply_qty: float | None = None
            projected_end_stock_without_supply: float | None = None
            safety_stock_qty: float | None = None
            recommended_supply_qty: float | None = None
            projected_total_stock: float | None = None
            projected_end_stock: float | None = None
            if is_current or is_future:
                projected_start_stock = stock_available if is_current else carried_stock
                expected_supply_qty = current_expected_supply if is_current else 0.0
                demand_to_cover = remaining_current_demand if is_current else forecast_units
                days_in_cursor = max(calendar.monthrange(cursor.year, cursor.month)[1], 1)
                safety_stock_qty = forecast_units / days_in_cursor * safety_stock_days
                projected_end_stock_without_supply = (
                    projected_start_stock + expected_supply_qty - demand_to_cover
                )
                recommended_supply_qty = max(
                    safety_stock_qty - projected_end_stock_without_supply,
                    0.0,
                )
                projected_total_stock = (
                    projected_start_stock
                    + expected_supply_qty
                    + recommended_supply_qty
                )
                projected_end_stock = projected_total_stock - demand_to_cover
                carried_stock = projected_end_stock

                aggregate = month_aggregates[cursor]
                aggregate["projected_start_stock"] += projected_start_stock
                aggregate["expected_supply_qty"] += expected_supply_qty
                aggregate["projected_end_stock_without_supply"] += projected_end_stock_without_supply
                aggregate["safety_stock_qty"] += safety_stock_qty
                aggregate["recommended_supply_qty"] += recommended_supply_qty
                aggregate["projected_total_stock"] += projected_total_stock
                aggregate["projected_end_stock"] += projected_end_stock
            effective_promotion = factor(sku, category_name, 'activity', cursor, promotion_coefficient(category_name, cursor))
            promotion_level_value = promotion_level(category_name, cursor)
            approved_month = approved_monthly_lookup.get(cursor, {})
            approved_plan_units = (
                max(number(approved_month.get("plan_units")), 0) * approved_units_share
                if approved_units_share is not None
                and number(approved_month.get("plan_units")) > 0
                else None
            )
            approved_plan_revenue = (
                max(number(approved_month.get("plan_revenue")), 0) * approved_revenue_share
                if approved_revenue_share is not None
                and number(approved_month.get("plan_revenue")) > 0
                else None
            )
            if approved_plan_units is not None:
                month_aggregates[cursor]["approved_plan_units"] += approved_plan_units
            if approved_plan_revenue is not None:
                month_aggregates[cursor]["approved_plan_revenue"] += approved_plan_revenue
            comparison_plan_units = (
                approved_plan_units
                if approved_plan_units is not None and approved_plan_units > 0
                else current_calculated_plan_units if is_current else None
            )
            product_months.append(
                {
                    "month_start": cursor.isoformat(),
                    "status": (
                        "Факт"
                        if cursor < anchor_month
                        else "Текущий" if is_current else "Прогноз"
                    ),
                    "actual_source_available": actual_source_available(cursor),
                    "actual_units": (
                        round(actual_units, 2)
                        if actual_source_available(cursor)
                        else None
                    ),
                    "actual_revenue": (
                        round(actual_revenue, 2)
                        if actual_source_available(cursor)
                        else None
                    ),
                    "oos_adjusted_daily_units": (
                        round(number(oos_adjusted_daily_units), 2)
                        if oos_adjusted_daily_units is not None
                        else None
                    ),
                    "oos_in_stock_days": oos_in_stock_days,
                    "oos_stock_covered_days": oos_stock_covered_days,
                    "run_rate_units": (
                        round(current_forecast_units, 2) if is_current else None
                    ),
                    "calculated_plan_units": (
                        round(current_calculated_plan_units, 2) if is_current else None
                    ),
                    "calculated_plan_revenue": (
                        round(current_calculated_plan_revenue, 2) if is_current else None
                    ),
                    "plan_fact_pct": (
                        round(actual_units / comparison_plan_units * 100, 1)
                        if comparison_plan_units and cursor <= anchor_month
                        else None
                    ),
                    "forecast_units": round(forecast_units, 2),
                    "forecast_revenue": round(forecast_revenue, 2),
                    "approved_plan_units": (
                        round(approved_plan_units, 2)
                        if approved_plan_units is not None
                        else None
                    ),
                    "approved_plan_revenue": (
                        round(approved_plan_revenue, 2)
                        if approved_plan_revenue is not None
                        else None
                    ),
                    "system_plan_units": (
                        round(number(future.get("recommended_units")), 2)
                        if is_future
                        else None
                    ),
                    "system_plan_revenue": (
                        round(number(future.get("recommended_revenue")), 2)
                        if is_future
                        else None
                    ),
                    "category_seasonality_coefficient": round(category_seasonality(category_name, cursor), 3),
                    "trend_coefficient": round(raw_trend, 3),
                    "promotion_level": round(promotion_level_value, 4),
                    "promotion_coefficient": round(effective_promotion, 4),
                    "planning_baseline_units": (
                        round(number(planning.get("planning_baseline_units")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "effective_growth_coefficient": (
                        round(number(planning.get("effective_growth_coefficient")) or 1.0, 6)
                        if is_current or is_future
                        else None
                    ),
                    "planning_after_growth_units": (
                        round(number(planning.get("planning_after_growth_units")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "effective_trend_coefficient": (
                        round(number(planning.get("effective_trend_coefficient")), 6)
                        if is_current or is_future
                        else None
                    ),
                    "planning_after_trend_units": (
                        round(number(planning.get("planning_after_trend_units")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "effective_seasonality_coefficient": (
                        round(number(planning.get("effective_seasonality_coefficient")), 6)
                        if is_current or is_future
                        else None
                    ),
                    "planning_after_seasonality_units": (
                        round(number(planning.get("planning_after_seasonality_units")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "planning_forecast_units": (
                        round(number(planning.get("planning_forecast_units")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "planning_coefficient": (
                        round(number(planning.get("planning_coefficient")), 4)
                        if is_current or is_future
                        else None
                    ),
                    "sales_seasonality_coefficient": round(category_seasonality(category_name, cursor), 3),
                    "demand_seasonality_coefficient": round(category_seasonality(category_name, cursor) * manual_seasonality_factor, 3),
                    "activity_coefficient": round(effective_promotion, 3),
                    "projected_start_stock": (
                        round(projected_start_stock, 2)
                        if projected_start_stock is not None
                        else None
                    ),
                    "expected_supply_qty": (
                        round(expected_supply_qty, 2)
                        if expected_supply_qty is not None
                        else None
                    ),
                    "projected_end_stock_without_supply": (
                        round(projected_end_stock_without_supply, 2)
                        if projected_end_stock_without_supply is not None
                        else None
                    ),
                    "safety_stock_qty": (
                        round(safety_stock_qty, 2)
                        if safety_stock_qty is not None
                        else None
                    ),
                    "recommended_supply_qty": (
                        round(recommended_supply_qty, 2)
                        if recommended_supply_qty is not None
                        else None
                    ),
                    "projected_total_stock": (
                        round(projected_total_stock, 2)
                        if projected_total_stock is not None
                        else None
                    ),
                    "projected_end_stock": (
                        round(projected_end_stock, 2)
                        if projected_end_stock is not None
                        else None
                    ),
                }
            )
            cursor = add_months(cursor, 1)

        next_forecast = future_by_month.get(
            next_month,
            {
                "demand_units": 0.0,
                "demand_revenue": 0.0,
                "recommended_units": 0.0,
                "recommended_revenue": 0.0,
                "stock_readiness": 1.0,
            },
        )
        daily_demand = number(next_forecast["demand_units"]) / max(
            calendar.monthrange(next_month.year, next_month.month)[1], 1
        )
        coverage_days = usable_stock / daily_demand if daily_demand > 0 else None
        safety_stock_units = daily_demand * safety_stock_days
        stock_gap_units = max(
            number(next_forecast["demand_units"]) + safety_stock_units - usable_stock,
            0,
        )
        history_month_count = sum(1 for value in recent_units if value > 0)
        confidence = (
            "Высокая"
            if history_month_count >= 3
            else ("Средняя" if history_month_count >= 2 else "Низкая")
        )
        product_rows.append(
            {
                **meta,
                "price_rub": round(price_rub, 2),
                "current_actual_units": round(current_actual_units, 2),
                "current_forecast_units": round(current_forecast_units, 2),
                "next_month_demand_units": round(number(next_forecast["demand_units"]), 2),
                "next_month_demand_revenue": round(number(next_forecast["demand_revenue"]), 2),
                "recommended_plan_units": round(number(next_forecast["recommended_units"]), 2),
                "recommended_plan_revenue": round(number(next_forecast["recommended_revenue"]), 2),
                "stock_available_qty": round(stock_available, 2),
                "stock_preparing_qty": round(stock_preparing, 2),
                "inbound_qty": round(inbound + in_transit + returning, 2),
                "stock_readiness_pct": round(number(next_forecast["stock_readiness"]) * 100, 1),
                "coverage_days": round(coverage_days, 1) if coverage_days is not None else None,
                "stock_gap_units": round(stock_gap_units, 2),
                "trend_coefficient": round(raw_trend, 3),
                "history_months": history_month_count,
                "trend_evidence": trend_evidence,
                "confidence": confidence,
                "monthly": product_months,
                "stock_snapshot_date": iso(stock.get("snapshot_date")),
                "logistics_snapshot_date": iso(logistics.get("logistics_snapshot_date")),
            }
        )

    product_rows.sort(
        key=lambda row: (-number(row["recommended_plan_revenue"]), str(row["article"]))
    )

    def weighted_product_month_coefficient(month_value: date, key: str) -> float:
        weighted_sum = 0.0
        total_weight = 0.0
        month_key = month_value.isoformat()
        for product in product_rows:
            product_month = next(
                (row for row in product["monthly"] if row["month_start"] == month_key),
                None,
            )
            if not product_month:
                continue
            weight = max(number(product_month.get("forecast_units")), number(product_month.get("actual_units")), 1.0)
            weighted_sum += (number(product_month.get(key)) or 1.0) * weight
            total_weight += weight
        return weighted_sum / total_weight if total_weight else 1.0

    months = []
    cursor = year_start
    while cursor <= year_end:
        aggregate = month_aggregates[cursor]
        status = (
            "Факт"
            if cursor < anchor_month
            else ("Факт + прогноз" if cursor == anchor_month else "Прогнозный план")
        )
        weighted_trend = number(overall_trend_detail.get("trend_coefficient")) or 1.0
        weighted_seasonality = weighted_product_month_coefficient(cursor, "category_seasonality_coefficient")
        weighted_promotion = weighted_product_month_coefficient(cursor, "promotion_coefficient")
        global_promotion_level = promotion_level("__all__", cursor)
        planning_numerator_units = (
            aggregate["calculated_plan_units"]
            if cursor == anchor_month
            else aggregate["forecast_units"]
        )
        planning_coefficient = (
            planning_numerator_units / aggregate["planning_baseline_units"]
            if cursor >= anchor_month and aggregate["planning_baseline_units"] > 0
            else None
        )
        months.append(
            {
                "month_start": cursor.isoformat(),
                "status": status,
                "actual_source_available": actual_source_available(cursor),
                "actual_units": (
                    round(aggregate["actual_units"], 2)
                    if actual_source_available(cursor)
                    else None
                ),
                "actual_revenue": (
                    round(aggregate["actual_revenue"], 2)
                    if actual_source_available(cursor)
                    else None
                ),
                "oos_adjusted_daily_units": (
                    round(aggregate["oos_adjusted_daily_units"], 2)
                    if aggregate["oos_adjusted_sku_count"] > 0
                    else None
                ),
                "oos_adjusted_sku_count": int(aggregate["oos_adjusted_sku_count"]),
                "oos_in_stock_days": int(aggregate["oos_in_stock_days"]),
                "oos_stock_covered_days": int(aggregate["oos_stock_covered_days"]),
                "approved_plan_units": (
                    round(aggregate["approved_plan_units"], 2)
                    if aggregate["approved_plan_units"] > 0
                    else None
                ),
                "approved_plan_revenue": (
                    round(aggregate["approved_plan_revenue"], 2)
                    if aggregate["approved_plan_revenue"] > 0
                    else None
                ),
                "calculated_plan_units": (
                    round(aggregate["calculated_plan_units"], 2)
                    if cursor == anchor_month and aggregate["calculated_plan_units"] > 0
                    else None
                ),
                "calculated_plan_revenue": (
                    round(aggregate["calculated_plan_revenue"], 2)
                    if cursor == anchor_month and aggregate["calculated_plan_revenue"] > 0
                    else None
                ),
                "forecast_units": round(aggregate["forecast_units"], 2),
                "forecast_revenue": round(aggregate["forecast_revenue"], 2),
                "recommended_units": round(aggregate["recommended_units"], 2),
                "recommended_revenue": round(aggregate["recommended_revenue"], 2),
                "trend_coefficient": round(weighted_trend, 3),
                "category_seasonality_coefficient": round(weighted_seasonality, 3),
                "promotion_level": round(global_promotion_level, 4),
                "promotion_coefficient": round(weighted_promotion, 3),
                "planning_baseline_units": (
                    round(aggregate["planning_baseline_units"], 4)
                    if cursor >= anchor_month
                    else None
                ),
                "growth_coefficient": (
                    round(growth_factor, 4) if cursor == anchor_month else 1.0
                ) if cursor >= anchor_month else None,
                "planning_after_growth_units": (
                    round(aggregate["planning_after_growth_units"], 4)
                    if cursor >= anchor_month
                    else None
                ),
                "planning_after_trend_units": (
                    round(aggregate["planning_after_trend_units"], 4)
                    if cursor >= anchor_month
                    else None
                ),
                "planning_after_seasonality_units": (
                    round(aggregate["planning_after_seasonality_units"], 4)
                    if cursor >= anchor_month
                    else None
                ),
                "planning_coefficient": (
                    round(planning_coefficient, 4)
                    if planning_coefficient is not None
                    else None
                ),
                # Backward-compatible aliases for existing consumers.
                "growth_coefficient": round(weighted_promotion, 3),
                "seasonality_coefficient": round(weighted_seasonality, 3),
                "activity_coefficient": round(weighted_promotion, 3),
                "projected_start_stock": (
                    round(aggregate["projected_start_stock"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "expected_supply_qty": (
                    round(aggregate["expected_supply_qty"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "projected_end_stock_without_supply": (
                    round(aggregate["projected_end_stock_without_supply"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "safety_stock_qty": (
                    round(aggregate["safety_stock_qty"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "recommended_supply_qty": (
                    round(aggregate["recommended_supply_qty"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "projected_total_stock": (
                    round(aggregate["projected_total_stock"], 2)
                    if cursor >= anchor_month
                    else None
                ),
                "projected_end_stock": (
                    round(aggregate["projected_end_stock"], 2)
                    if cursor >= anchor_month
                    else None
                ),
            }
        )
        cursor = add_months(cursor, 1)

    month_lookup = {row["month_start"]: row for row in months}
    current_row = month_lookup.get(anchor_month.isoformat(), {})
    next_row = month_lookup.get(next_month.isoformat(), {})
    future_rows = [
        row
        for row in months
        if anchor_month < date.fromisoformat(row["month_start"]) <= year_end
    ]
    from pulse_supply import DEFAULTS
    from pulse_sales_model import apply_feasibility
    supply_policy = sources.get('supply_policy', DEFAULTS)
    apply_feasibility(product_rows, months, available_to, sources.get('confirmed_supplies', []))
    plan_versions = sources.get('plan_versions', [])
    approved_version = next((x for x in plan_versions if x['status']=='approved'), None)
    if approved_version:
        approved_values = {x['month_start']: x for x in approved_version['months']}
        for month_row in months:
            approved = approved_values.get(month_row['month_start'])
            month_row['approved_plan_units'] = approved['plan_units'] if approved else None
            month_row['approved_plan_revenue'] = approved['plan_revenue'] if approved else None
            month_row['approved_version_id'] = approved_version['version_id'] if approved else None
        # Versioned portfolio targets are not silently allocated to product rows.
        for product in product_rows:
            for month_row in product.get('monthly', []):
                month_row['approved_plan_units'] = None
                month_row['approved_plan_revenue'] = None
    from sales_planning_workbench import attach_commercial_plan
    commercial = attach_commercial_plan(product_rows, months, plan_versions, anchor_month.isoformat(),
                                         bool(category_filter or product_filter or article_filter), marketplace)
    page_size = max(25, min(int((params.get('limit') or ['500'])[0]), 500))
    total_pages = max(1, math.ceil(len(product_rows)/page_size))
    page = max(1, min(int((params.get('page') or ['1'])[0]), total_pages))
    product_page = product_rows[(page-1)*page_size:page*page_size]
    stock_total = sum(number(row["stock_available_qty"]) for row in product_rows)
    inbound_total = sum(
        number(row["stock_preparing_qty"]) + number(row["inbound_qty"])
        for row in product_rows
    )
    next_demand_units = sum(number(row["next_month_demand_units"]) for row in product_rows)
    next_readiness_pct = percent(stock_total + inbound_total, next_demand_units)
    missing_market_categories = sorted(
        category
        for category in selected_skus_by_category
        if not market_rows_by_category.get(category)
    )
    from search_demand import build_payload
    search_demand_payload = None
    if marketplace == 'wb' and rolling:
        search_demand_payload = build_payload(*sources.get('demand_sources', ([], [])), sorted({p['category_name'] for p in product_rows}),
            [add_months(anchor_month, i).isoformat() for i in range(12)], available_to)
    return {
        "ok": True,
        "client": client_key,
        "marketplace": marketplace,
        "marketplace_label": "WB" if marketplace == "wb" else "Ozon",
        "period": {
            "available_from": available_from.isoformat(),
            "available_to": available_to.isoformat(),
            "anchor_month": anchor_month.isoformat(),
            "next_month": next_month.isoformat(),
            "year_end": year_end.isoformat(),
            "oos_stock_history_from": iso(oos_stock_history_from),
            "oos_stock_history_to": iso(oos_stock_history_to),
        },
        "filters": {
            "categories": all_categories,
            "products": all_products,
            "selected_category": category_filter,
            "selected_product": product_filter,
            "selected_article": article_filter,
        },
        "coefficients": {
            "growth_pct": growth_pct,
            "trend_weight_pct": trend_weight_pct,
            "stock_weight_pct": stock_weight_pct,
            "seasonality_pct": seasonality_pct,
            "safety_stock_days": safety_stock_days,
        },
        "totals": {
            "selected_sku_count": len(product_rows),
            "current_actual_revenue": number(current_row.get("actual_revenue")),
            "current_forecast_revenue": number(current_row.get("forecast_revenue")),
            "next_month_plan_revenue": number(next_row.get("recommended_revenue")),
            "year_end_plan_revenue": sum(
                number(row["recommended_revenue"]) for row in future_rows
            ),
            "stock_available_qty": stock_total,
            "stock_inbound_qty": inbound_total,
            "next_month_stock_readiness_pct": next_row.get("supply_readiness_pct"),
            "next_month_demand_revenue": number(next_row.get("forecast_revenue")),
            "next_month_achievable_revenue": next_row.get("achievable_revenue"),
            "next_month_known_achievable_revenue": next_row.get("known_achievable_revenue", next_row.get("achievable_revenue")),
            "uncovered_demand_units": next_row.get("uncovered_demand_units"),
            "feasibility_missing_sku_count": next_row.get("feasibility_missing_sku_count", 0),
        },
        "months": months,
        "products": product_rows if include_all_products else product_page,
        "pagination": {"page": page,"page_size":page_size,"total":len(product_rows),"total_pages":total_pages},
        "plan_versions": plan_versions,
        "commercial": commercial,
        "seasonality_sources": seasonality_sources,
        "coefficient_overrides": overrides,
        "activity_settings": sources.get("activity_settings", []),
        "search_demand": search_demand_payload,
        "confirmed_supplies": sources.get('confirmed_supplies', []),
        "metric_contract": {"primary": "orders" if marketplace == "wb" else "finance_sales", "label": "Заказы WB по дате отчёта, сумма из Seller Analytics; отмены отдельно" if marketplace == "wb" else "Продажи Ozon по финансовым операциям", "buyout_forecast": None, "buyout_forecast_reason": "Нужна история когорт заказов и задержки выкупа; отношение операций одного месяца не является вероятностью выкупа"},
        "supply_policy": supply_policy,
        "planning_notice": "Спрос, достижимые продажи и утверждённый план разделены. Достижимость учитывает ежедневный остаток и подтверждённые ETA реестра поставок. При отсутствии свежих остатков достижимость не определена; в месяцах указан счётчик SKU без данных. Товарная детализация постраничная; итоги относятся ко всему фильтру.",
        "trend_detail": overall_trend_detail,
        "category_trends": category_trends,
        "coefficient_forecast": build_coefficient_forecast(coefficient_products, product_page, category_trends, market_trend_rows, anchor_month.isoformat(), available_from, available_to, marketplace),
        "seasonality_detail": overall_seasonality_detail,
        "category_seasonality_details": category_seasonality_details,
        "promotion_settings": {
            "default_coefficient": 1.0,
            "minimum": 0.1,
            "maximum": None,
            "calculation": "current_level / previous_level",
            "global": promotion_settings_by_category.get("__all__", {}),
            "by_category": {
                key: value for key, value in promotion_settings_by_category.items() if key != "__all__"
            },
        },
        "methodology": {
            "actual": (
                (
                    "Факт — заказы и сумма заказов WB Seller Analytics; последний доступный "
                    f"день источника {available_to.isoformat()}."
                )
                if marketplace == "wb"
                else (
                    "Факт — строки revenue Ozon Finance с возвратами; последний доступный "
                    f"день источника {available_to.isoformat()}."
                )
            ),
            "oos_adjusted_daily_rate": (
                "RR/день без OOS = заказы SKU в дни с доступным для продажи остатком "
                "/ число таких календарных дней. Между снимками применяется последнее "
                "известное состояние остатка; дни до первого снимка SKU не участвуют. "
                "Итог категории и кабинета — сумма скоростей SKU."
            ),
            "current_month": (
                "Расчётный план текущего месяца = факт прошлого завершённого месяца × "
                "рост к базе × коэффициент планирования текущего месяца. Рост применяется "
                "один раз и переносится в будущие месяцы через базу предыдущего плана, без "
                "ежемесячного сложного процента. Run-Rate = факт текущего месяца / "
                "прошедшие дни источника × число дней месяца."
            ),
            "baseline": (
                "Первый прогнозный месяц считается от Run Rate текущего месяца и "
                "коэффициентов первого будущего месяца. Каждый следующий месяц считается "
                "от прогнозного плана предыдущего месяца и коэффициентов нового месяца."
            ),
            "trend": (
                "Тренд — фактические продажи выбранной категории за три последовательных "
                "10-дневных окна. Коэффициент сравнивает последнюю декаду со средним двух "
                "предыдущих; в каждый будущий месяц входит с указанным весом и защитным коридором 0,70–1,35. "
                "Применяемый множитель показан в строке К тренда."
            ),
            "stock": (
                "Остатки и поставки показаны отдельным блоком и не изменяют прогнозный план. "
                "Дефицит виден в строке остатка на конец месяца."
            ),
            "seasonality": (
                "Сезонность категории — нормализованный месячный индекс продаж ниши Ozon "
                "из MPStats. Дневная детализация использует исходный niche/by_date с groupBy=day; "
                "рыночные продажи не суммируются с фактом выбранного клиента."
            ),
            "promotion": (
                "Продвижение хранится как помесячный уровень без верхнего ограничения. "
                "В коэффициент планирования входит изменение к предыдущему периоду: "
                "уровень текущего месяца / уровень предыдущего. Категорийный уровень "
                "приоритетнее общего; 1,00 означает базовый уровень."
            ),
            "inventory_roll_forward": (
                "В текущем месяце остаток на конец = текущий остаток на сегодня + "
                "поставки до конца месяца − расход до конца месяца по Run-Rate. "
                "Каждый будущий месяц начинается с остатка предыдущего: "
                "остаток на начало + поставки − прогноз продаж."
            ),
            "approved_plan": (
                "Утверждённый план берётся только из финмодели. Месячный план кабинета "
                "распределяется по SKU по годовым долям финмодели. Если утверждённого плана "
                "текущего месяца нет, для сравнения используется расчётный план от факта "
                "прошлого месяца; будущие утверждённые планы прогнозом не подменяются."
            ),
        },
        "warnings": [
            "План является расчётной рекомендацией, а не утверждённым коммерческим планом.",
            *(
                [
                    "Факт продаж доступен только с "
                    f"{available_from.isoformat()}; более ранние месяцы показаны как "
                    "нет данных источника, а не как нулевые продажи."
                ]
                if actual_source_month_start > year_start
                else []
            ),
            *(
                [
                    "RR/день без OOS рассчитан только с первого доступного снимка "
                    f"остатка ({oos_stock_history_from.isoformat()}); более ранние дни "
                    "не считаются ни наличием, ни OOS."
                ]
                if oos_stock_history_from and oos_stock_history_from > year_start
                else []
            ),
            (
                "WB API передаёт текущий остаток, но не календарь входящих поставок; "
                "ожидаемые поставки в модели WB равны нулю."
                if marketplace == "wb"
                else "Текущие ожидаемые поставки учитываются до конца текущего месяца; для будущих месяцев они равны нулю до появления источника календаря поставок."
            ),
            *(
                ["MPStats-сезонность недоступна для категорий: " + ", ".join(missing_market_categories) + ". Для них временно используется 1,00."]
                if missing_market_categories
                else []
            ),
        ],
    }


_READ_ONLY_FORECAST_CACHE_TTL_SECONDS = 900
_READ_ONLY_FORECAST_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_READ_ONLY_FORECAST_CACHE_LOCK = threading.Lock()
_READ_ONLY_FORECAST_KEY_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}
_READ_ONLY_FORECAST_REFRESHING: set[tuple[Any, ...]] = set()


def _forecast_cache_key(
    config: dict[str, Any], raw_query: str, include_all_products: bool
) -> tuple[Any, ...]:
    """Ignore unrelated dashboard/date parameters that do not affect the model."""
    params = parse_qs(raw_query)
    normalized: list[tuple[str, str]] = []
    defaults = {
        "growth_pct": "0", "trend_weight_pct": "60", "stock_weight_pct": "30",
        "seasonality_pct": "0", "safety_stock_days": "21", "limit": "500", "page": "1",
    }
    for name in ("client", "marketplace", "horizon"):
        for value in params.get(name, []):
            normalized.append((name, value))
    for name, default in defaults.items():
        value = (params.get(name) or [default])[0]
        try:
            canonical = f"{float(value):g}"
        except ValueError:
            canonical = value
        normalized.append((name, canonical))
    for name in ("category", "product", "coefficient_overrides"):
        for value in params.get(name, []):
            if value:
                normalized.append((name, value))
    article = (params.get("article") or params.get("q") or [""])[0]
    if article:
        normalized.append(("article", article))
    return (str(config.get("database") or ""), tuple(normalized), include_all_products)


def _store_forecast_cache(key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    with _READ_ONLY_FORECAST_CACHE_LOCK:
        _READ_ONLY_FORECAST_CACHE[key] = (time.monotonic(), payload)


def _refresh_forecast_cache(
    key: tuple[Any, ...], config: dict[str, Any], raw_query: str, include_all_products: bool
) -> None:
    try:
        payload = _sales_forecast_payload_uncached(
            config, raw_query, include_all_products=include_all_products
        )
        _store_forecast_cache(key, payload)
    except Exception as exc:
        print(f"PULSE forecast cache refresh failed: {type(exc).__name__}: {exc}", flush=True)
    finally:
        with _READ_ONLY_FORECAST_CACHE_LOCK:
            _READ_ONLY_FORECAST_REFRESHING.discard(key)


def start_sales_forecast_prewarm(
    read_config: Any,
    clients: tuple[str, ...] = ("toptop",),
    marketplaces: tuple[str, ...] = ("wb", "ozon"),
) -> threading.Thread | None:
    """Warm the expensive canonical portfolio snapshots without blocking HTTP."""
    if os.environ.get("PULSE_READ_ONLY_REPORT_CACHE") != "1":
        return None

    def run() -> None:
        started = time.monotonic()
        tasks = [(client, market) for client in clients for market in marketplaces]
        print(
            f"ПЛАН: прогрев прогнозов {len(tasks)} | последовательно | HTTP уже доступен",
            flush=True,
        )
        errors = 0
        for index, (client, market) in enumerate(tasks, start=1):
            item_started = time.monotonic()
            try:
                sales_forecast_payload(
                    read_config(client),
                    urlencode({"client": client, "marketplace": market, "horizon": "rolling", "page": 1}),
                )
                status = "готово"
            except Exception as exc:
                errors += 1
                status = f"ошибка {type(exc).__name__}"
            elapsed = time.monotonic() - started
            average = elapsed / index
            eta = average * (len(tasks) - index)
            print(
                f"ПРОГРЕСС: {index}/{len(tasks)} ({index / len(tasks) * 100:.0f}%) | "
                f"{client}/{market}: {status}, {time.monotonic() - item_started:.1f}s | "
                f"ошибки {errors} | ETA {eta:.0f}s",
                flush=True,
            )
        print(
            f"ИТОГ: прогнозы {len(tasks) - errors}/{len(tasks)}, ошибки {errors}, "
            f"время {time.monotonic() - started:.1f}s, partial={str(bool(errors)).lower()}",
            flush=True,
        )

    thread = threading.Thread(target=run, name="pulse-forecast-prewarm", daemon=True)
    thread.start()
    return thread


def sales_forecast_payload(
    config: dict[str, Any], raw_query: str = "", *, include_all_products: bool = False
) -> dict[str, Any]:
    """Cache expensive immutable reads only in the isolated read-only runtime."""
    if os.environ.get("PULSE_READ_ONLY_REPORT_CACHE") != "1":
        return _sales_forecast_payload_uncached(
            config, raw_query, include_all_products=include_all_products
        )
    key = _forecast_cache_key(config, raw_query, include_all_products)
    with _READ_ONLY_FORECAST_CACHE_LOCK:
        now = time.monotonic()
        cached = _READ_ONLY_FORECAST_CACHE.get(key)
        if cached and now - cached[0] <= _READ_ONLY_FORECAST_CACHE_TTL_SECONDS:
            return cached[1]
        if cached:
            if key not in _READ_ONLY_FORECAST_REFRESHING:
                _READ_ONLY_FORECAST_REFRESHING.add(key)
                threading.Thread(
                    target=_refresh_forecast_cache,
                    args=(key, dict(config), raw_query, include_all_products),
                    name="pulse-forecast-refresh",
                    daemon=True,
                ).start()
            # Stale-while-revalidate: a routine report never waits for the
            # portfolio model merely because its 15-minute TTL elapsed.
            return cached[1]
        key_lock = _READ_ONLY_FORECAST_KEY_LOCKS.setdefault(key, threading.Lock())

    # Only equal cold keys serialize. Different clients/markets no longer wait
    # behind one global cache lock.
    with key_lock:
        with _READ_ONLY_FORECAST_CACHE_LOCK:
            cached = _READ_ONLY_FORECAST_CACHE.get(key)
            if cached:
                return cached[1]
        payload = _sales_forecast_payload_uncached(
            config, raw_query, include_all_products=include_all_products
        )
        _store_forecast_cache(key, payload)
    return payload
