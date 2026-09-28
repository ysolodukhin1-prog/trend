from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs


PLAN_TABLE = "sportmaster_run_rate_plan"
MARKETPLACES = {
    "ozon": "Ozon",
    "wb": "WB",
    "total": "Итого",
}


def _normalize(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    return value


def _marketplace_from_query(query: str) -> str:
    value = parse_qs(query).get("marketplace", ["total"])[0].strip().lower()
    return value if value in MARKETPLACES else "total"


def _month_start(value: str | date | None) -> date | None:
    if not value:
        return None
    parsed = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return parsed.replace(day=1)


def _month_end(month: date) -> date:
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def _ensure_plan_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS public.{PLAN_TABLE} (
                plan_month date NOT NULL,
                marketplace text NOT NULL CHECK (marketplace IN ('ozon', 'wb', 'total')),
                orders_plan_qty numeric(20, 2) NOT NULL DEFAULT 0 CHECK (orders_plan_qty >= 0),
                revenue_plan_rub numeric(20, 2) NOT NULL DEFAULT 0 CHECK (revenue_plan_rub >= 0),
                promotion_plan_rub numeric(20, 2) NOT NULL DEFAULT 0 CHECK (promotion_plan_rub >= 0),
                updated_at timestamp without time zone NOT NULL DEFAULT now(),
                PRIMARY KEY (plan_month, marketplace)
            )
            """
        )
        cur.execute(
            f"""
            COMMENT ON TABLE public.{PLAN_TABLE}
            IS 'Ручные месячные планы Sportmaster для отчета Run-Rate'
            """
        )


def _resolve_month(cur, query: str) -> date:
    params = parse_qs(query)
    requested = _month_start(
        params.get("date_from", [""])[0].strip()
        or params.get("date_to", [""])[0].strip()
    )
    if requested:
        return requested
    cur.execute(
        f"""
        WITH candidates AS (
            SELECT max(report_date)::date AS value FROM public.mv_ozon_funnel_daily_summary_rollup
            UNION ALL
            SELECT max(report_date)::date FROM public.mv_wb_funnel_daily_summary_rollup
            UNION ALL
            SELECT max(plan_month)::date FROM public.{PLAN_TABLE}
        )
        SELECT date_trunc('month', coalesce(max(value), current_date))::date AS plan_month
        FROM candidates
        """
    )
    return cur.fetchone()["plan_month"]


def _plan_row(cur, month: date, marketplace: str) -> dict:
    cur.execute(
        f"""
        SELECT plan_month, marketplace, orders_plan_qty, revenue_plan_rub,
               promotion_plan_rub, updated_at
        FROM public.{PLAN_TABLE}
        WHERE plan_month = %s AND marketplace = %s
        """,
        (month, marketplace),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    return {
        "plan_month": month,
        "marketplace": marketplace,
        "orders_plan_qty": 0,
        "revenue_plan_rub": 0,
        "promotion_plan_rub": 0,
        "updated_at": None,
    }


def handle_filters(parsed, get_conn):
    with get_conn() as conn:
        _ensure_plan_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH bounds AS (
                    SELECT min(report_date)::date AS date_from, max(report_date)::date AS date_to
                    FROM (
                        SELECT report_date FROM public.mv_ozon_funnel_daily_summary_rollup
                        UNION ALL
                        SELECT report_date FROM public.mv_wb_funnel_daily_summary_rollup
                    ) rows
                ),
                fact_months AS (
                    SELECT generate_series(
                        date_trunc('month', date_from)::date,
                        date_trunc('month', date_to)::date,
                        interval '1 month'
                    )::date AS plan_month
                    FROM bounds
                    WHERE date_from IS NOT NULL AND date_to IS NOT NULL
                ),
                months AS (
                    SELECT plan_month FROM fact_months
                    UNION
                    SELECT plan_month FROM public.{PLAN_TABLE}
                )
                SELECT
                    (SELECT date_from FROM bounds) AS date_from,
                    (SELECT date_to FROM bounds) AS date_to,
                    coalesce(
                        json_agg(
                            json_build_object(
                                'month', plan_month,
                                'date_from', plan_month,
                                'date_to', (plan_month + interval '1 month - 1 day')::date
                            )
                            ORDER BY plan_month
                        ),
                        '[]'::json
                    ) AS months
                FROM months
                """
            )
            payload = dict(cur.fetchone())
    payload.update(
        {
            "category_names": [],
            "product_names": [],
            "abc_orders": [],
            "abc_sales": [],
            "abc_stock": [],
            "abc_combined": [],
            "marketplaces": [
                {"id": "ozon", "label": "Ozon"},
                {"id": "wb", "label": "WB"},
                {"id": "total", "label": "Итого"},
            ],
            "marketplace": _marketplace_from_query(parsed.query),
            "view": "Sportmaster funnel + product/media advertising",
            "report_kind": "sportmaster_run_rate",
        }
    )
    return _normalize(payload)


def _selected_facts_sql(marketplace: str) -> str:
    if marketplace == "total":
        return """
        SELECT
            report_date,
            'total'::text AS marketplace,
            bool_or(has_sales_fact) AS has_sales_fact,
            bool_or(has_promotion_fact) AS has_promotion_fact,
            sum(orders_qty) AS orders_qty,
            sum(revenue_rub) AS revenue_rub,
            sum(promotion_expense_rub) AS promotion_expense_rub
        FROM facts
        GROUP BY report_date
        """
    return """
        SELECT report_date, marketplace, has_sales_fact, has_promotion_fact,
               orders_qty, revenue_rub, promotion_expense_rub
        FROM facts
        WHERE marketplace = %s
    """


def _fetch_daily_rows(parsed, get_conn) -> tuple[list[dict], dict, date, str]:
    marketplace = _marketplace_from_query(parsed.query)
    params = parse_qs(parsed.query)
    requested_date_to = params.get("date_to", [""])[0].strip()
    with get_conn() as conn:
        _ensure_plan_table(conn)
        with conn.cursor() as cur:
            month = _resolve_month(cur, parsed.query)
            month_to = _month_end(month)
            if requested_date_to:
                month_to = min(month_to, date.fromisoformat(requested_date_to[:10]))
            plan = _plan_row(cur, month, marketplace)
            selected_sql = _selected_facts_sql(marketplace)
            query = f"""
                WITH requested AS (
                    SELECT %s::date AS date_from, %s::date AS date_to
                ),
                sales AS (
                    SELECT report_date, 'ozon'::text AS marketplace,
                           sum(ordered_units)::numeric AS orders_qty,
                           sum(ordered_amount_rub)::numeric AS revenue_rub
                    FROM public.mv_ozon_funnel_daily_summary_rollup, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                    UNION ALL
                    SELECT report_date, 'wb'::text AS marketplace,
                           sum(ordered_units)::numeric AS orders_qty,
                           sum(ordered_amount_rub)::numeric AS revenue_rub
                    FROM public.mv_wb_funnel_daily_summary_rollup, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                ),
                promotion_parts AS (
                    SELECT report_date, 'ozon'::text AS marketplace,
                           sum(coalesce(expense_rub, 0))::numeric AS expense_rub
                    FROM public.mv_ozon_adv_daily_by_article_category, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                    UNION ALL
                    SELECT report_date, 'wb'::text AS marketplace,
                           sum(coalesce(expense_rub, 0))::numeric AS expense_rub
                    FROM public.mv_wb_adv_daily_by_article_category, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                    UNION ALL
                    SELECT report_date, 'ozon'::text AS marketplace,
                           sum(coalesce(expense_rub, 0))::numeric AS expense_rub
                    FROM public.mv_ozon_media_adv_daily, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                    UNION ALL
                    SELECT report_date, 'wb'::text AS marketplace,
                           sum(coalesce(expense_rub, 0))::numeric AS expense_rub
                    FROM public.mv_wb_media_adv_campaign_daily, requested
                    WHERE report_date BETWEEN requested.date_from AND requested.date_to
                    GROUP BY report_date
                ),
                promotion AS (
                    SELECT report_date, marketplace, sum(expense_rub) AS promotion_expense_rub
                    FROM promotion_parts
                    GROUP BY report_date, marketplace
                ),
                facts AS (
                    SELECT
                        coalesce(s.report_date, p.report_date) AS report_date,
                        coalesce(s.marketplace, p.marketplace) AS marketplace,
                        s.report_date IS NOT NULL AS has_sales_fact,
                        p.report_date IS NOT NULL AS has_promotion_fact,
                        s.orders_qty,
                        s.revenue_rub,
                        p.promotion_expense_rub
                    FROM sales s
                    FULL JOIN promotion p USING (report_date, marketplace)
                ),
                selected AS (
                    {selected_sql}
                ),
                base AS (
                    SELECT
                        report_date,
                        marketplace,
                        CASE marketplace WHEN 'ozon' THEN 'Ozon' WHEN 'wb' THEN 'WB' ELSE 'Итого' END AS marketplace_label,
                        %s::date AS plan_month,
                        has_sales_fact,
                        has_promotion_fact,
                        orders_qty,
                        revenue_rub,
                        promotion_expense_rub,
                        %s::numeric AS orders_plan_qty,
                        %s::numeric AS revenue_plan_rub,
                        %s::numeric AS promotion_plan_rub,
                        extract(day from (%s::date + interval '1 month - 1 day'))::numeric AS days_in_month
                    FROM selected
                ),
                calculated AS (
                    SELECT
                        *,
                        orders_plan_qty / nullif(days_in_month, 0) AS orders_plan_daily_qty,
                        revenue_plan_rub / nullif(days_in_month, 0) AS revenue_plan_daily_rub,
                        promotion_plan_rub / nullif(days_in_month, 0) AS promotion_plan_daily_rub,
                        sum(coalesce(orders_qty, 0)) OVER (ORDER BY report_date) AS orders_cum_qty,
                        sum(coalesce(revenue_rub, 0)) OVER (ORDER BY report_date) AS revenue_cum_rub,
                        sum(coalesce(promotion_expense_rub, 0)) OVER (ORDER BY report_date) AS promotion_cum_rub
                    FROM base
                )
                SELECT
                    marketplace,
                    marketplace_label,
                    report_date,
                    plan_month,
                    has_sales_fact,
                    has_promotion_fact,
                    orders_qty,
                    revenue_rub AS orders_rub,
                    revenue_rub AS sales_rub,
                    promotion_expense_rub AS ad_spend_rub,
                    orders_plan_qty,
                    revenue_plan_rub AS sales_plan_rub,
                    promotion_plan_rub AS ad_spend_plan_rub,
                    orders_plan_daily_qty,
                    revenue_plan_daily_rub AS sales_plan_daily_rub,
                    promotion_plan_daily_rub AS ad_spend_plan_daily_rub,
                    orders_plan_daily_qty * extract(day from report_date) AS orders_plan_elapsed_qty,
                    revenue_plan_daily_rub * extract(day from report_date) AS sales_plan_elapsed_rub,
                    promotion_plan_daily_rub * extract(day from report_date) AS ad_spend_plan_elapsed_rub,
                    orders_cum_qty,
                    revenue_cum_rub AS sales_cum_rub,
                    promotion_cum_rub AS ad_spend_cum_rub,
                    CASE WHEN orders_plan_qty <> 0 THEN round(orders_cum_qty / orders_plan_qty * 100, 2) ELSE 0 END AS orders_month_plan_fact_pct,
                    CASE WHEN orders_plan_daily_qty <> 0 THEN round(orders_cum_qty / (orders_plan_daily_qty * extract(day from report_date)) * 100, 2) ELSE 0 END AS orders_elapsed_plan_fact_pct,
                    CASE WHEN revenue_plan_rub <> 0 THEN round(revenue_cum_rub / revenue_plan_rub * 100, 2) ELSE 0 END AS sales_month_plan_fact_pct,
                    CASE WHEN revenue_plan_daily_rub <> 0 THEN round(revenue_cum_rub / (revenue_plan_daily_rub * extract(day from report_date)) * 100, 2) ELSE 0 END AS sales_elapsed_plan_fact_pct,
                    CASE WHEN promotion_plan_rub <> 0 THEN round(promotion_cum_rub / promotion_plan_rub * 100, 2) ELSE 0 END AS ad_spend_budget_used_pct,
                    CASE WHEN promotion_plan_daily_rub <> 0 THEN round(promotion_cum_rub / (promotion_plan_daily_rub * extract(day from report_date)) * 100, 2) ELSE 0 END AS ad_spend_elapsed_budget_pct,
                    CASE WHEN revenue_rub <> 0 THEN round(promotion_expense_rub / revenue_rub * 100, 2) ELSE 0 END AS tacos_pct,
                    CASE WHEN revenue_cum_rub <> 0 THEN round(promotion_cum_rub / revenue_cum_rub * 100, 2) ELSE 0 END AS tacos_cum_pct
                FROM calculated
                ORDER BY report_date
            """
            values = [
                month,
                month_to,
            ]
            if marketplace != "total":
                values.append(marketplace)
            values.extend(
                [
                    month,
                    plan["orders_plan_qty"],
                    plan["revenue_plan_rub"],
                    plan["promotion_plan_rub"],
                    month,
                ]
            )
            cur.execute(query, values)
            rows = [dict(row) for row in cur.fetchall()]
    return _normalize(rows), _normalize(plan), month, marketplace


def handle_daily(parsed, get_conn):
    rows, _, _, _ = _fetch_daily_rows(parsed, get_conn)
    return {"rows": rows}


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _forecast(
    rows: list[dict],
    value_key: str,
    fact_flag: str,
    month: date,
) -> dict:
    fact_rows = [
        row
        for row in rows
        if row.get(fact_flag) and row.get("report_date")
    ]
    total = sum(_float(row.get(value_key)) for row in fact_rows)
    days_in_month = _month_end(month).day
    if not fact_rows:
        return {
            "fact": total,
            "as_of_date": "",
            "as_of_day": 0,
            "recent_start_day": 0,
            "days_with_fact": 0,
            "runrate": 0,
            "recent_runrate": 0,
        }
    as_of = max(date.fromisoformat(str(row["report_date"])[:10]) for row in fact_rows)
    as_of_day = as_of.day
    recent_start = max(month, as_of - timedelta(days=6))
    recent_total = sum(
        _float(row.get(value_key))
        for row in fact_rows
        if recent_start <= date.fromisoformat(str(row["report_date"])[:10]) <= as_of
    )
    recent_days = (as_of - recent_start).days + 1
    remaining_days = max(days_in_month - as_of_day, 0)
    return {
        "fact": total,
        "as_of_date": as_of.isoformat(),
        "as_of_day": as_of_day,
        "recent_start_day": recent_start.day,
        "days_with_fact": len({row["report_date"] for row in fact_rows}),
        "runrate": round(total / max(as_of_day, 1) * days_in_month),
        "recent_runrate": round(total + recent_total / max(recent_days, 1) * remaining_days),
    }


def _pct(value: float, plan: float) -> float:
    return round(value / plan * 100, 2) if plan else 0


def _source_freshness(cur, month: date, marketplace: str) -> list[dict]:
    month_to = _month_end(month)
    rows = [
        ("ozon", "Продажи Ozon", "mv_ozon_funnel_daily_summary_rollup"),
        ("wb", "Продажи WB", "mv_wb_funnel_daily_summary_rollup"),
        ("ozon", "Товарное продвижение Ozon", "mv_ozon_adv_daily_by_article_category"),
        ("wb", "Товарное продвижение WB", "mv_wb_adv_daily_by_article_category"),
        ("ozon", "Медийное продвижение Ozon", "mv_ozon_media_adv_daily"),
        ("wb", "Медийное продвижение WB", "mv_wb_media_adv_campaign_daily"),
    ]
    result = []
    for source_marketplace, label, relation in rows:
        if marketplace not in {"total", source_marketplace}:
            continue
        cur.execute(
            f"""
            SELECT min(report_date) AS date_from, max(report_date) AS date_to
            FROM public.{relation}
            WHERE report_date BETWEEN %s AND %s
            """,
            (month, month_to),
        )
        coverage = dict(cur.fetchone())
        result.append(
            {
                "marketplace": source_marketplace,
                "label": label,
                "date_from": coverage.get("date_from"),
                "date_to": coverage.get("date_to"),
            }
        )
    return result


def handle_scorecard(parsed, get_conn):
    rows, plan, month, marketplace = _fetch_daily_rows(parsed, get_conn)
    orders = _forecast(rows, "orders_qty", "has_sales_fact", month)
    revenue = _forecast(rows, "sales_rub", "has_sales_fact", month)
    promotion = _forecast(rows, "ad_spend_rub", "has_promotion_fact", month)
    orders_plan = _float(plan.get("orders_plan_qty"))
    revenue_plan = _float(plan.get("revenue_plan_rub"))
    promotion_plan = _float(plan.get("promotion_plan_rub"))
    with get_conn() as conn, conn.cursor() as cur:
        freshness = _source_freshness(cur, month, marketplace)
    row = {
        "report_kind": "sportmaster_run_rate",
        "marketplace": marketplace,
        "marketplace_label": MARKETPLACES[marketplace],
        "plan_month": month,
        "days_in_month": _month_end(month).day,
        "orders_plan_qty": orders_plan,
        "orders_qty": orders["fact"],
        "orders_plan_fact_pct": _pct(orders["fact"], orders_plan),
        "orders_runrate_qty": orders["runrate"],
        "orders_runrate_pct": _pct(orders["runrate"], orders_plan),
        "orders_recent_runrate_qty": orders["recent_runrate"],
        "orders_recent_runrate_pct": _pct(orders["recent_runrate"], orders_plan),
        "orders_as_of_date": orders["as_of_date"],
        "orders_as_of_day": orders["as_of_day"],
        "orders_recent_start_day": orders["recent_start_day"],
        "orders_days_with_fact": orders["days_with_fact"],
        "revenue_plan_rub": revenue_plan,
        "sales_plan_rub": revenue_plan,
        "revenue_rub": revenue["fact"],
        "orders_rub": revenue["fact"],
        "sales_rub": revenue["fact"],
        "sales_plan_fact_pct": _pct(revenue["fact"], revenue_plan),
        "sales_runrate_rub": revenue["runrate"],
        "sales_runrate_pct": _pct(revenue["runrate"], revenue_plan),
        "sales_recent_runrate_rub": revenue["recent_runrate"],
        "sales_recent_runrate_pct": _pct(revenue["recent_runrate"], revenue_plan),
        "sales_as_of_date": revenue["as_of_date"],
        "last_date": revenue["as_of_date"],
        "last_day": revenue["as_of_day"],
        "recent_start_day": revenue["recent_start_day"],
        "days_with_fact": revenue["days_with_fact"],
        "promotion_plan_rub": promotion_plan,
        "ad_spend_plan_rub": promotion_plan,
        "promotion_expense_rub": promotion["fact"],
        "ad_spend_rub": promotion["fact"],
        "ad_spend_plan_fact_pct": _pct(promotion["fact"], promotion_plan),
        "ad_spend_runrate_rub": promotion["runrate"],
        "ad_spend_runrate_pct": _pct(promotion["runrate"], promotion_plan),
        "ad_spend_recent_runrate_rub": promotion["recent_runrate"],
        "ad_spend_recent_runrate_pct": _pct(promotion["recent_runrate"], promotion_plan),
        "promotion_as_of_date": promotion["as_of_date"],
        "promotion_as_of_day": promotion["as_of_day"],
        "promotion_recent_start_day": promotion["recent_start_day"],
        "promotion_days_with_fact": promotion["days_with_fact"],
        "plan_updated_at": plan.get("updated_at"),
        "plan_rows": [
            {
                "metric_key": "orders_plan_qty",
                "label": "План по заказам",
                "unit": "шт",
                "value": orders_plan,
            },
            {
                "metric_key": "revenue_plan_rub",
                "label": "План по выручке",
                "unit": "руб",
                "value": revenue_plan,
            },
            {
                "metric_key": "promotion_plan_rub",
                "label": "План по расходам на продвижение",
                "unit": "руб",
                "value": promotion_plan,
            },
        ],
        "source_freshness": freshness,
    }
    return {"rows": [_normalize(row)]}


def handle_summary(parsed, get_conn):
    scorecard = handle_scorecard(parsed, get_conn)
    return dict((scorecard.get("rows") or [{}])[0])


def handle_monthly(parsed, get_conn):
    scorecard = handle_scorecard(parsed, get_conn)
    row = dict((scorecard.get("rows") or [{}])[0])
    if not row:
        return {"rows": []}
    row.update(
        {
            "date_from": row.get("plan_month"),
            "date_to": _normalize(_month_end(_month_start(row.get("plan_month")))),
            "days_with_fact": row.get("days_with_fact", 0),
            "sales_plan_rub": row.get("revenue_plan_rub", 0),
            "sales_rub": row.get("revenue_rub", 0),
            "ad_spend_plan_rub": row.get("promotion_plan_rub", 0),
            "ad_spend_rub": row.get("promotion_expense_rub", 0),
            "ad_spend_budget_used_pct": row.get("ad_spend_plan_fact_pct", 0),
            "tacos_plan_pct": _pct(
                _float(row.get("promotion_plan_rub")),
                _float(row.get("revenue_plan_rub")),
            ),
            "tacos_fact_pct": _pct(
                _float(row.get("promotion_expense_rub")),
                _float(row.get("revenue_rub")),
            ),
        }
    )
    return {"rows": [_normalize(row)]}


def _plan_value(payload: dict, key: str) -> Decimal:
    try:
        value = Decimal(str(payload.get(key, 0) or 0))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Некорректное значение {key}") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"{key} должен быть неотрицательным числом")
    return value


def save_plan(payload: dict, get_conn):
    if str(payload.get("client") or "").strip().lower() != "sportmaster":
        raise ValueError("Ручной Run-Rate план доступен только для Sportmaster")
    month = _month_start(payload.get("plan_month"))
    if not month:
        raise ValueError("Не указан месяц плана")
    marketplace = str(payload.get("marketplace") or "total").strip().lower()
    if marketplace not in MARKETPLACES:
        raise ValueError("Некорректный маркетплейс")
    values = {
        "orders_plan_qty": _plan_value(payload, "orders_plan_qty"),
        "revenue_plan_rub": _plan_value(payload, "revenue_plan_rub"),
        "promotion_plan_rub": _plan_value(payload, "promotion_plan_rub"),
    }
    with get_conn() as conn:
        _ensure_plan_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.{PLAN_TABLE} (
                    plan_month, marketplace, orders_plan_qty,
                    revenue_plan_rub, promotion_plan_rub, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (plan_month, marketplace) DO UPDATE SET
                    orders_plan_qty = excluded.orders_plan_qty,
                    revenue_plan_rub = excluded.revenue_plan_rub,
                    promotion_plan_rub = excluded.promotion_plan_rub,
                    updated_at = now()
                RETURNING plan_month, marketplace, orders_plan_qty,
                          revenue_plan_rub, promotion_plan_rub, updated_at
                """,
                (
                    month,
                    marketplace,
                    values["orders_plan_qty"],
                    values["revenue_plan_rub"],
                    values["promotion_plan_rub"],
                ),
            )
            saved = dict(cur.fetchone())
    return {"ok": True, "plan": _normalize(saved)}
