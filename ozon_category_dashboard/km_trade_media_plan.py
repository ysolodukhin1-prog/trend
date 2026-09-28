"""Source-backed annual Ozon media plan for KM-style client databases."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from km_trade_finance import connect_km
from km_trade_planfact import add_months, available_range, month_start, number
from km_trade_sales_planning import sales_fact_cutoff_date, sales_forecast_payload, _available_range


REFERENCE_FIELDS = (
    "ctr_pct", "click_to_cart_pct", "cart_to_order_pct",
    "organic_share_pct", "ad_ctr_pct", "cpc_rub", "tacos_pct", "acos_pct",
)
METRICS = (
    ("orders_rub", "Заказы, ₽", "money"),
    ("orders_units", "Заказы, шт.", "number"),
    ("average_price_rub", "Средняя цена", "money"),
    ("impressions", "Показы карточек", "number"),
    ("ad_impressions", "Рекламные показы", "number"),
    ("ad_clicks", "Рекламные переходы", "number"),
    ("ad_ctr_pct", "CTR рекламы", "percent"),
    ("ad_expense_rub", "Расходы", "money"),
    ("cpo_rub", "CPO", "money"),
    ("cpc_rub", "CPC", "money"),
    ("ecpm_rub", "eCPM", "money"),
    ("organic_share_pct", "Organic Share", "percent"),
    ("clicks", "Переходы в карточку", "number"),
    ("ctr_pct", "CTR карточек", "percent"),
    ("carts", "Корзины", "number"),
    ("click_to_cart_pct", "CR (клик → корзина)", "percent"),
    ("orders_units_repeat", "Заказы, шт.", "number"),
    ("cart_to_order_pct", "CRO (корзина → заказ)", "percent"),
    ("tacos_pct", "TACoS", "percent"),
    ("acos_pct", "ACoS", "percent"),
    ("planning_coefficient", "Коэффициент планирования", "coefficient"),
)
ADDITIVE_METRICS = {
    "orders_rub", "orders_units", "impressions", "ad_impressions", "ad_clicks",
    "ad_expense_rub", "clicks", "carts", "orders_units_repeat",
}


def ratio(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None:
        return None
    top, bottom = number(numerator), number(denominator)
    return top / bottom * 100 if bottom > 0 else None


def rounded(value: Any, digits: int = 2) -> float | None:
    return None if value is None else round(number(value), digits)


def actual_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    def available(field: str, marker: str | None = None) -> Any:
        key = marker or f"{field}_available"
        return row.get(field) if row.get(key, row.get(field) is not None) else None

    orders_rub = available("orders_rub")
    orders_units = available("orders_units")
    impressions = available("impressions")
    clicks = available("clicks")
    carts = available("carts")
    ad_impressions = available("ad_impressions")
    ad_clicks = available("ad_clicks")
    ad_expense = available("ad_expense_rub", "ad_expense_available")
    ad_orders_rub = available("ad_orders_rub")
    ad_share = ratio(ad_impressions, impressions)
    average_price = (
        number(orders_rub) / number(orders_units)
        if orders_rub is not None and orders_units is not None and number(orders_units) > 0
        else None
    )
    return {
        "orders_rub": rounded(orders_rub),
        "orders_units": rounded(orders_units),
        "average_price_rub": rounded(average_price),
        "impressions": rounded(impressions),
        "ad_impressions": rounded(ad_impressions),
        "ad_clicks": rounded(ad_clicks),
        "ad_ctr_pct": rounded(ratio(ad_clicks, ad_impressions)),
        "ad_expense_rub": rounded(ad_expense),
        "cpo_rub": rounded(number(ad_expense) / number(orders_units)) if ad_expense is not None and number(orders_units) > 0 else None,
        "cpc_rub": rounded(number(ad_expense) / number(ad_clicks)) if ad_expense is not None and number(ad_clicks) > 0 else None,
        "ecpm_rub": rounded(number(ad_expense) / number(ad_impressions) * 1000) if ad_expense is not None and number(ad_impressions) > 0 else None,
        "organic_share_pct": rounded(max(0.0, min(100.0, 100.0 - ad_share))) if ad_share is not None else None,
        "clicks": rounded(clicks),
        "ctr_pct": rounded(ratio(clicks, impressions)),
        "carts": rounded(carts),
        "click_to_cart_pct": rounded(ratio(carts, clicks)),
        "orders_units_repeat": rounded(orders_units),
        "cart_to_order_pct": rounded(ratio(orders_units, carts)),
        "tacos_pct": rounded(ratio(ad_expense, orders_rub)),
        "acos_pct": rounded(ratio(ad_expense, ad_orders_rub)),
        "planning_coefficient": None,
    }


def sum_source_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    markers = {"ad_expense_rub": "ad_expense_available"}
    for field in (
        "orders_rub", "orders_units", "impressions", "ad_impressions", "ad_clicks",
        "clicks", "carts", "ad_expense_rub", "ad_orders_rub",
    ):
        marker = markers.get(field, f"{field}_available")
        present = [row for row in rows if row.get(marker, row.get(field) is not None)]
        result[marker] = bool(present)
        result[field] = sum(number(row.get(field)) for row in present) if present else None
    return result


def smoothed_references(actual_by_month: dict[date, dict[str, Any]], anchor_month: date) -> dict[str, float | None]:
    months = sorted((value for value in actual_by_month if value < anchor_month), reverse=True)[:3]
    metrics = actual_metrics(sum_source_rows([actual_by_month[value] for value in months]))
    return {field: metrics.get(field) for field in REFERENCE_FIELDS}


def planned_metrics(
    sales_plan: dict[str, Any], references: dict[str, float | None],
    coefficient: float, fallback_average_price: float | None,
) -> dict[str, float | None]:
    units, revenue = number(sales_plan.get("plan_units")), number(sales_plan.get("plan_revenue"))
    average_price = revenue / units if units > 0 and revenue > 0 else fallback_average_price
    if units <= 0 and revenue > 0 and average_price and average_price > 0:
        units = revenue / average_price
    if revenue <= 0 and units > 0 and average_price and average_price > 0:
        revenue = units * average_price
    effective = {
        field: min(number(references[field]) * coefficient, 100.0) if references.get(field) is not None else None
        for field in ("ctr_pct", "click_to_cart_pct", "cart_to_order_pct")
    }
    cro, cr, ctr = (effective["cart_to_order_pct"], effective["click_to_cart_pct"], effective["ctr_pct"])
    carts = units / (cro / 100) if units > 0 and cro and cro > 0 else None
    clicks = carts / (cr / 100) if carts is not None and cr and cr > 0 else None
    impressions = clicks / (ctr / 100) if clicks is not None and ctr and ctr > 0 else None
    organic_share = references.get("organic_share_pct")
    ad_impressions = (
        impressions * (1 - min(max(number(organic_share), 0.0), 100.0) / 100)
        if impressions is not None and organic_share is not None else None
    )
    ad_ctr = references.get("ad_ctr_pct")
    ad_clicks = (
        ad_impressions * number(ad_ctr) / 100
        if ad_impressions is not None and ad_ctr is not None else None
    )
    cpc = references.get("cpc_rub")
    tacos = references.get("tacos_pct")
    ad_expense = (
        ad_clicks * number(cpc)
        if ad_clicks is not None and cpc is not None
        else revenue * number(tacos) / 100 if revenue > 0 and tacos is not None else None
    )
    return {
        "orders_rub": rounded(revenue) if revenue > 0 else None,
        "orders_units": rounded(units) if units > 0 else None,
        "average_price_rub": rounded(average_price),
        "impressions": rounded(impressions),
        "ad_impressions": rounded(ad_impressions),
        "ad_clicks": rounded(ad_clicks),
        "ad_ctr_pct": rounded(ratio(ad_clicks, ad_impressions)),
        "ad_expense_rub": rounded(ad_expense),
        "cpo_rub": rounded(ad_expense / units) if ad_expense is not None and units > 0 else None,
        "cpc_rub": rounded(cpc) if cpc is not None else rounded(ad_expense / ad_clicks) if ad_expense is not None and ad_clicks is not None and ad_clicks > 0 else None,
        "ecpm_rub": rounded(ad_expense / ad_impressions * 1000) if ad_expense is not None and ad_impressions is not None and ad_impressions > 0 else None,
        "organic_share_pct": rounded(organic_share),
        "clicks": rounded(clicks),
        "ctr_pct": rounded(ctr),
        "carts": rounded(carts),
        "click_to_cart_pct": rounded(cr),
        "orders_units_repeat": rounded(units) if units > 0 else None,
        "cart_to_order_pct": rounded(cro),
        "tacos_pct": rounded(ratio(ad_expense, revenue)),
        "acos_pct": rounded(references.get("acos_pct")),
        "planning_coefficient": rounded(coefficient),
    }


def sales_plan_rows_from_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the effective monthly orders plan displayed by «План продаж»."""
    period = payload.get("period") if isinstance(payload.get("period"), dict) else {}
    anchor_raw = str(period.get("anchor_month") or "")[:10]
    try:
        anchor_month = month_start(date.fromisoformat(anchor_raw))
    except ValueError:
        return []

    result: list[dict[str, Any]] = []
    for source_row in payload.get("months") or []:
        if not isinstance(source_row, dict):
            continue
        try:
            target_raw = source_row.get("month_start")
            target_date = target_raw if isinstance(target_raw, date) else date.fromisoformat(str(target_raw)[:10])
            target_month = month_start(target_date)
        except (TypeError, ValueError):
            continue
        if target_month < anchor_month:
            continue

        approved_available = any(
            number(source_row.get(field)) > 0
            for field in ("approved_plan_units", "approved_plan_revenue")
        )
        if approved_available:
            prefix, source = "approved_plan", "approved_plan"
        elif target_month == anchor_month:
            prefix, source = "calculated_plan", "calculated_plan"
        else:
            prefix, source = "forecast", "forecast_plan"

        result.append({
            "month_start": target_month,
            "plan_units": source_row.get(f"{prefix}_units"),
            "plan_revenue": source_row.get(f"{prefix}_revenue"),
            "plan_source": source,
            "planning_coefficient": source_row.get("planning_coefficient"),
        })
    return result


def align_current_calculated_plan_to_order_fact(
    plan_by_month: dict[date, dict[str, Any]],
    actual_by_month: dict[date, dict[str, Any]],
    anchor_month: date,
) -> None:
    """Keep a calculated current-month plan on the same orders basis as its fact.

    The sales-planning report calculates its fallback revenue from finance lines,
    while the media plan compares against ordered amount from the funnel.  Reuse
    the planning coefficient, but apply it to the previous completed month's
    funnel orders so plan and fact retain one metric contract.  Approved plans
    remain authoritative and are never rewritten here.
    """
    plan = plan_by_month.get(anchor_month)
    if not plan or plan.get("plan_source") != "calculated_plan":
        return
    previous = actual_metrics(actual_by_month.get(add_months(anchor_month, -1), {}))
    coefficient = max(number(plan.get("planning_coefficient", 1.0)), 0.0)
    if coefficient <= 0:
        coefficient = 1.0
    if previous.get("orders_units") is not None and number(previous["orders_units"]) > 0:
        plan["plan_units"] = number(previous["orders_units"]) * coefficient
    if previous.get("orders_rub") is not None and number(previous["orders_rub"]) > 0:
        plan["plan_revenue"] = number(previous["orders_rub"]) * coefficient
    plan["plan_source"] = "calculated_order_fact"


def run_rate(actual: dict[str, float | None], factor: float) -> dict[str, float | None]:
    return {
        key: rounded(value * factor if key in ADDITIVE_METRICS and value is not None else value)
        for key, value in actual.items()
    }


def comparison(values: dict[str, Any], plan: dict[str, Any]) -> dict[str, float | None]:
    return {
        key: rounded(ratio(values.get(key), plan.get(key))) if key != "planning_coefficient" else None
        for key, _label, _kind in METRICS
    }


def build_media_plan(
    *, anchor_date: date, actual_rows: list[dict[str, Any]], approved_plans: list[dict[str, Any]],
    stored_references: dict[str, Any] | None = None,
    stored_coefficients: dict[date, float] | None = None,
    client_key: str = "km_trade",
) -> dict[str, Any]:
    anchor_month = month_start(anchor_date)
    actual_by_month = {month_start(row["month_start"]): dict(row) for row in actual_rows}
    plan_by_month = {month_start(row["month_start"]): dict(row) for row in approved_plans}
    align_current_calculated_plan_to_order_fact(plan_by_month, actual_by_month, anchor_month)
    stored_references = stored_references or {}
    smoothed = smoothed_references(actual_by_month, anchor_month)
    references = {
        field: rounded(stored_references[field]) if stored_references.get(field) is not None else smoothed.get(field)
        for field in REFERENCE_FIELDS
    }
    reference_sources = {
        field: "manual" if stored_references.get(field) is not None else "smoothed_fact"
        for field in REFERENCE_FIELDS
    }
    completed = [row for value, row in actual_by_month.items() if value < anchor_month]
    fallback_price = actual_metrics(sum_source_rows(completed)).get("average_price_rub")
    coefficients = stored_coefficients or {}
    month_rows = []
    elapsed_days = max(1, min(anchor_date.day, calendar.monthrange(anchor_month.year, anchor_month.month)[1]))
    rr_factor = calendar.monthrange(anchor_month.year, anchor_month.month)[1] / elapsed_days
    cursor = date(anchor_month.year, 1, 1)
    while cursor <= date(anchor_month.year, 12, 1):
        coefficient = max(number(coefficients.get(cursor, 1.0)), 0.1)
        actual = actual_metrics(actual_by_month.get(cursor, {}))
        plan = planned_metrics(plan_by_month.get(cursor, {}), references, coefficient, fallback_price)
        if cursor < anchor_month:
            item = {"month_start": cursor.isoformat(), "state": "fact", "actual": actual}
        elif cursor == anchor_month:
            rr = run_rate(actual, rr_factor)
            item = {
                "month_start": cursor.isoformat(), "state": "current", "plan": plan, "actual": actual,
                "plan_source": plan_by_month.get(cursor, {}).get("plan_source"),
                "run_rate": rr, "plan_fact_pct": comparison(actual, plan), "plan_rr_pct": comparison(rr, plan),
            }
        else:
            item = {
                "month_start": cursor.isoformat(), "state": "plan", "plan": plan,
                "plan_source": plan_by_month.get(cursor, {}).get("plan_source"),
            }
        month_rows.append(item)
        cursor = add_months(cursor, 1)

    columns = []
    for item in month_rows:
        month = item["month_start"]
        scenarios = (
            (("plan", "План"), ("actual", "Факт"), ("run_rate", "Run Rate"),
             ("plan_fact_pct", "План/факт, %"), ("plan_rr_pct", "План/RR, %"))
            if item["state"] == "current"
            else ((("actual", "Факт"),) if item["state"] == "fact" else (("plan", "План"),))
        )
        columns.extend(
            {"key": f"{month}:{scenario}", "month_start": month, "scenario": scenario, "label": label}
            for scenario, label in scenarios
        )
    rows = []
    for key, label, kind in METRICS:
        values = {}
        for item in month_rows:
            for scenario in ("actual", "plan", "run_rate", "plan_fact_pct", "plan_rr_pct"):
                if scenario in item:
                    values[f"{item['month_start']}:{scenario}"] = item[scenario].get(key)
        rows.append({"key": key, "label": label, "kind": kind, "values": values})
    missing = [field for field in ("ctr_pct", "click_to_cart_pct", "cart_to_order_pct", "ad_ctr_pct") if references.get(field) is None]
    return {
        "ok": True, "client": client_key, "marketplace": "ozon",
        "period": {"year": anchor_month.year, "anchor_month": anchor_month.isoformat(), "fact_to": anchor_date.isoformat(), "history_window": "3 последних завершённых месяца"},
        "references": references, "reference_sources": reference_sources,
        "columns": columns, "rows": rows, "months": month_rows,
        "warnings": ["Недостаточно истории для части конверсий: заполните референсный блок вручную."] if missing else [],
        "methodology": {
            "sales_plan": (
                "Заказы ₽/шт берутся из отчёта «План продаж»: для текущего месяца — "
                "утверждённый план либо расчёт по факту заказов предыдущего завершённого месяца "
                "с коэффициентом планирования; для будущих — утверждённый или прогнозный план."
            ),
            "decomposition": "Заказы → корзины → общие переходы → общие показы рассчитываются обратной декомпозицией по CRO, CR и CTR. Рекламные показы = общие показы × (1 − Organic Share), рекламные переходы = рекламные показы × CTR рекламы.",
            "costs": "Расходы плана = рекламные переходы × экспертный CPC; если CPC недоступен, используется заказы ₽ × TACoS. CPO = расходы / заказы шт.; плановый TACoS пересчитывается из итогового бюджета.",
            "ecpm": "eCPM = рекламные расходы / рекламные показы × 1000. Эквивалентная проверка для CPC-модели: eCPM = CPC × CTR рекламы (%) × 10. Расходы, клики и показы берутся из одного рекламного источника Ozon; общие показы карточек в знаменатель не входят.",
            "references": "По умолчанию — взвешенный факт трёх завершённых месяцев; ручной референс имеет приоритет.",
            "coefficient": "Коэффициент умножает CTR, CR и CRO месяца: больше 1,00 означает улучшение эффективности.",
            "run_rate": "Суммируемые показатели экстраполируются на календарный месяц; средние и конверсии сохраняют фактический темп.",
        },
    }


def ensure_schema(cur: Any) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.km_trade_media_plan_reference (
            marketplace text PRIMARY KEY, ctr_pct numeric, click_to_cart_pct numeric,
            cart_to_order_pct numeric, organic_share_pct numeric, ad_ctr_pct numeric,
            cpc_rub numeric, tacos_pct numeric, acos_pct numeric,
            updated_at timestamp without time zone NOT NULL DEFAULT now()
        )
    """)
    cur.execute("ALTER TABLE public.km_trade_media_plan_reference ADD COLUMN IF NOT EXISTS cpc_rub numeric")
    cur.execute("ALTER TABLE public.km_trade_media_plan_reference ADD COLUMN IF NOT EXISTS ad_ctr_pct numeric")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.km_trade_media_plan_coefficients (
            marketplace text NOT NULL, month_start date NOT NULL,
            coefficient numeric NOT NULL DEFAULT 1 CHECK (coefficient >= 0.1),
            updated_at timestamp without time zone NOT NULL DEFAULT now(),
            PRIMARY KEY (marketplace, month_start)
        )
    """)


def ensure_read_schema(cur: Any) -> bool:
    """Validate schema without issuing DDL in a read-only reporting runtime."""
    cur.execute("SHOW transaction_read_only")
    state_row = cur.fetchone()
    state = next(iter(state_row.values())) if isinstance(state_row, dict) else state_row[0]
    if state != "on":
        ensure_schema(cur)
        return True
    cur.execute("""
        SELECT to_regclass('public.km_trade_media_plan_reference') IS NOT NULL,
               to_regclass('public.km_trade_media_plan_coefficients') IS NOT NULL,
               EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name='km_trade_media_plan_reference'
                     AND column_name='cpc_rub'
               ),
               EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name='km_trade_media_plan_reference'
                     AND column_name='ad_ctr_pct'
               )
    """)
    schema_row = cur.fetchone()
    values = schema_row.values() if isinstance(schema_row, dict) else schema_row
    if not all(values):
        raise RuntimeError("km_trade_media_plan schema is not provisioned")
    return False


def monthly_sources(config: dict[str, Any], year: int, fact_to: date, marketplace: str = "ozon"):
    if marketplace not in {"ozon", "wb"}:
        raise ValueError("Неподдерживаемая площадка")
    funnel_table = f"public.{marketplace}_funnel_daily"
    ads_table = "public.ozon_adv_daily_raw" if marketplace == "ozon" else "public.mv_wb_adv_daily_by_article_category"
    year_start, year_end = date(year, 1, 1), date(year, 12, 1)
    with connect_km(config) as conn, conn.cursor() as cur:
        if ensure_read_schema(cur):
            conn.commit()
        cur.execute(f"""
            WITH funnel AS (
                SELECT date_trunc('month', report_date)::date AS month_start,
                       sum(ordered_amount_rub) AS orders_rub, sum(ordered_units) AS orders_units,
                       sum(impressions_total) AS impressions, sum(card_visits) AS clicks,
                       sum(cart_adds) AS carts,
                       count(ordered_amount_rub) > 0 AS orders_rub_available,
                       count(ordered_units) > 0 AS orders_units_available,
                       count(impressions_total) > 0 AS impressions_available,
                       count(card_visits) > 0 AS clicks_available,
                       count(cart_adds) > 0 AS carts_available
                FROM {funnel_table} WHERE report_date BETWEEN %s AND %s GROUP BY 1
            ), ads AS (
                SELECT date_trunc('month', report_date)::date AS month_start,
                       sum(impressions) AS ad_impressions,
                       sum(clicks) AS ad_clicks,
                       sum(coalesce(fact_expense_rub, expense_rub)) AS ad_expense_rub,
                       sum(orders_amount_rub) AS ad_orders_rub,
                       count(impressions) > 0 AS ad_impressions_available,
                       count(clicks) > 0 AS ad_clicks_available,
                       count(coalesce(fact_expense_rub, expense_rub)) > 0 AS ad_expense_available,
                       count(orders_amount_rub) > 0 AS ad_orders_rub_available
                FROM {ads_table} WHERE report_date BETWEEN %s AND %s GROUP BY 1
            )
            SELECT coalesce(f.month_start, a.month_start) AS month_start,
                   f.orders_rub, f.orders_units, f.impressions, f.clicks, f.carts,
                   f.orders_rub_available, f.orders_units_available, f.impressions_available,
                   f.clicks_available, f.carts_available, a.ad_impressions, a.ad_clicks,
                   a.ad_expense_rub, a.ad_orders_rub, a.ad_impressions_available,
                   a.ad_clicks_available, a.ad_expense_available, a.ad_orders_rub_available
            FROM funnel f FULL JOIN ads a USING (month_start) ORDER BY 1
        """, (year_start, fact_to, year_start, fact_to))
        actual = [dict(row) for row in cur.fetchall()]
        plans = []
        cur.execute("SELECT to_regclass('public.km_trade_sales_plan_monthly') AS relation")
        if marketplace == "ozon" and cur.fetchone()["relation"]:
            cur.execute("SELECT month_start, sales_plan_units AS plan_units, sales_plan_rub AS plan_revenue FROM public.km_trade_sales_plan_monthly WHERE month_start BETWEEN %s AND %s ORDER BY month_start", (year_start, year_end))
            plans = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM public.km_trade_media_plan_reference WHERE marketplace = %s", (marketplace,))
        row = cur.fetchone()
        references = dict(row) if row else {}
        cur.execute("""
            SELECT month_start, coefficient FROM public.km_trade_media_plan_coefficients
            WHERE marketplace = %s AND month_start BETWEEN %s AND %s
        """, (marketplace, year_start, year_end))
        coefficients = {row["month_start"]: number(row["coefficient"]) for row in cur.fetchall()}
    return actual, plans, references, coefficients


def media_plan_payload(config: dict[str, Any], client_key: str = "km_trade", marketplace: str = "ozon") -> dict[str, Any]:
    if marketplace not in {"ozon", "wb"}:
        return {"ok": True, "available": False, "marketplace": marketplace, "client": client_key,
                "message": "Для этой площадки пока не подключены план продаж и рекламная воронка медиаплана. Данные других площадок не подставляются."}
    with connect_km(config) as conn, conn.cursor() as cur:
        tables = [f"public.{marketplace}_funnel_daily", "public.ozon_adv_daily_raw" if marketplace == "ozon" else "public.mv_wb_adv_daily_by_article_category"]
        for table in tables:
            cur.execute("SELECT to_regclass(%s) AS relation", (table,))
            if not cur.fetchone()["relation"]:
                return {"ok": True, "available": False, "marketplace": marketplace, "client": client_key,
                        "message": "Для выбранной площадки ещё не загружены источники медиаплана."}
    _available_from, available_to = _available_range(config, marketplace)
    fact_to = min(available_to, sales_fact_cutoff_date())
    actual, approved_plans, references, coefficients = monthly_sources(config, fact_to.year, fact_to, marketplace)
    # Reuse the canonical rolling portfolio snapshot warmed by the VPS runtime.
    # A separate non-rolling query produced the same current/future media-plan
    # inputs but forced another 25–40 second full portfolio calculation.
    report_plans = sales_plan_rows_from_report(
        sales_forecast_payload(
            config,
            f"client={client_key}&marketplace={marketplace}&horizon=rolling",
        )
    )
    result = build_media_plan(anchor_date=fact_to, actual_rows=actual,
        approved_plans=report_plans or approved_plans, stored_references=references,
        stored_coefficients=coefficients, client_key=client_key)
    result["marketplace"] = marketplace
    result["methodology"]["ecpm"] = result["methodology"]["ecpm"].replace("Ozon", "WB" if marketplace == "wb" else "Ozon")
    if not actual:
        result.setdefault("warnings", []).append("Нет фактических данных выбранной площадки за текущий год.")
    return result


def save_media_plan_settings(
    config: dict[str, Any],
    payload: dict[str, Any],
    client_key: str = "km_trade",
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    marketplace = str(payload.get("marketplace") or "ozon").strip().lower()
    if marketplace not in {"ozon", "wb"}:
        raise ValueError("Для этой площадки сохранение медиаплана пока недоступно")
    reset = bool(payload.get("reset"))
    references = payload.get("references") if isinstance(payload.get("references"), dict) else {}
    coefficient_rows = payload.get("coefficients") if isinstance(payload.get("coefficients"), list) else []
    clean_references: dict[str, float | None] = {}
    for field in REFERENCE_FIELDS:
        raw = references.get(field)
        value = None if raw in (None, "") else number(raw)
        if value is not None and value < 0:
            raise ValueError(f"{field}: значение не может быть отрицательным")
        if field != "cpc_rub" and value is not None and value > 100:
            raise ValueError(f"{field}: значение должно быть от 0 до 100")
        clean_references[field] = value
    clean_coefficients = []
    for item in coefficient_rows:
        if not isinstance(item, dict):
            continue
        try:
            target_month = date.fromisoformat(str(item.get("month_start") or "")[:10]).replace(day=1)
        except ValueError as exc:
            raise ValueError("Некорректный месяц коэффициента") from exc
        raw = item.get("coefficient")
        value = number(raw) if raw not in (None, "") else 1.0
        if value < 0.1:
            raise ValueError("Коэффициент планирования должен быть не меньше 0,10")
        clean_coefficients.append((target_month, value))
    with connect_km(config) as conn, conn.cursor() as cur:
        ensure_schema(cur)
        if reset:
            cur.execute("DELETE FROM public.km_trade_media_plan_reference WHERE marketplace = %s", (marketplace,))
            cur.execute("DELETE FROM public.km_trade_media_plan_coefficients WHERE marketplace = %s", (marketplace,))
        else:
            cur.execute("""
                INSERT INTO public.km_trade_media_plan_reference (
                    marketplace, ctr_pct, click_to_cart_pct, cart_to_order_pct,
                    organic_share_pct, ad_ctr_pct, cpc_rub, tacos_pct, acos_pct, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (marketplace) DO UPDATE SET
                    ctr_pct = EXCLUDED.ctr_pct, click_to_cart_pct = EXCLUDED.click_to_cart_pct,
                    cart_to_order_pct = EXCLUDED.cart_to_order_pct,
                    organic_share_pct = EXCLUDED.organic_share_pct,
                    ad_ctr_pct = EXCLUDED.ad_ctr_pct,
                    cpc_rub = EXCLUDED.cpc_rub, tacos_pct = EXCLUDED.tacos_pct,
                    acos_pct = EXCLUDED.acos_pct,
                    updated_at = now()
            """, (marketplace, *(clean_references[field] for field in REFERENCE_FIELDS)))
            for target_month, coefficient in clean_coefficients:
                cur.execute("""
                    INSERT INTO public.km_trade_media_plan_coefficients
                        (marketplace, month_start, coefficient, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (marketplace, month_start) DO UPDATE SET
                        coefficient = EXCLUDED.coefficient, updated_at = now()
                """, (marketplace, target_month, coefficient))
        conn.commit()
    return media_plan_payload(config, client_key, marketplace)
