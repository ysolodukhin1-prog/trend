"""Source-backed Yandex Market adapter for the generic Plan/Fact dashboard.

Plans stay missing until a Yandex planning contract exists. Actual orders,
delivered sales and advertising spend come from the analytical Yandex layer.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from urllib.parse import parse_qs


def is_yandex(query: str) -> bool:
    return (parse_qs(query).get("marketplace") or [""])[0].strip().lower() == "yandex_market"


def _period(query: str) -> tuple[date, date]:
    params = parse_qs(query)
    today = date.today()
    start = date.fromisoformat((params.get("date_from") or [today.replace(day=1).isoformat()])[0])
    end = date.fromisoformat((params.get("date_to") or [today.isoformat()])[0])
    if start > end or (end - start).days > 400:
        raise ValueError("Date range must be 0..400 days")
    return start, end


def _number(value):
    return float(value) if value is not None else None


def _ratio(value, denominator):
    return round(float(value) / float(denominator) * 100, 2) if value is not None and denominator else None


def _source_ready(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.yandex_orders_daily') IS NOT NULL AS ready")
        row = cur.fetchone()
        return bool((row.get("ready") if isinstance(row, dict) else row[0]))


def _marketplaces(app):
    labels = {"ozon": "Ozon", "wb": "WB", "yandex_market": "Яндекс Маркет", "total": "Итого"}
    configured = app.ADMIN_CLIENTS[app.current_client_key()].get("marketplaces") or []
    result = [{"id": key, "label": labels[key]} for key in configured if key in labels]
    return result or [{"id": "yandex_market", "label": labels["yandex_market"]}]


def filters(app, parsed):
    with app.get_conn() as conn:
        if not _source_ready(conn):
            return {"date_from": None, "date_to": None, "months": [], "marketplace": "yandex_market",
                    "marketplaces": _marketplaces(app), "product_names": [], "data_status": "unavailable",
                    "data_message": "Источник Яндекс Маркета не установлен"}
        with conn.cursor() as cur:
            cur.execute("""
                WITH dates AS (
                    SELECT order_date AS day FROM yandex_orders_daily WHERE client_key=%s
                    UNION SELECT metric_date FROM yandex_funnel_daily WHERE client_key=%s
                    UNION SELECT metric_date FROM yandex_marketing_daily WHERE client_key=%s)
                SELECT min(day) AS date_from, max(day) AS date_to FROM dates
            """, (app.current_client_key(),) * 3)
            row = dict(cur.fetchone())
    first, last = row.get("date_from"), row.get("date_to")
    months = []
    if first and last:
        cursor, finish = first.replace(day=1), last.replace(day=1)
        while cursor <= finish:
            month_end = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
            months.append({"month": cursor.isoformat(), "date_from": cursor.isoformat(), "date_to": month_end.isoformat()})
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"date_from": app.normalize_value(first), "date_to": app.normalize_value(last), "months": months,
            "marketplace": "yandex_market", "marketplaces": _marketplaces(app), "product_names": [],
            "category_names": [], "data_status": "partial",
            "data_message": "Яндекс Маркет: факт из заказов, воронки и рекламы; планы пока не заданы"}


def _daily(app, parsed):
    start, end = _period(parsed.query)
    client = app.current_client_key()
    with app.get_conn() as conn:
        if not _source_ready(conn):
            return [], "unavailable", "Источник Яндекс Маркета не установлен"
        with conn.cursor() as cur:
            cur.execute("""
                WITH days AS (
                    SELECT order_date AS report_date FROM yandex_orders_daily
                    WHERE client_key=%s AND order_date BETWEEN %s AND %s
                    UNION SELECT metric_date FROM yandex_funnel_daily
                    WHERE client_key=%s AND metric_date BETWEEN %s AND %s
                    UNION SELECT metric_date FROM yandex_marketing_daily
                    WHERE client_key=%s AND metric_date BETWEEN %s AND %s
                ), orders AS (
                    SELECT order_date AS report_date,
                        CASE WHEN sum(orders_with_payment)=sum(orders)
                             THEN sum(buyer_payment + coalesce(subsidy, 0)) END AS orders_rub
                    FROM yandex_orders_daily
                    WHERE client_key=%s AND order_date BETWEEN %s AND %s AND currency='RUR'
                    GROUP BY order_date
                ), sales AS (
                    SELECT metric_date AS report_date,
                        CASE WHEN count(delivered_amount)>0 THEN sum(delivered_amount) END AS sales_rub
                    FROM yandex_funnel_daily
                    WHERE client_key=%s AND metric_date BETWEEN %s AND %s GROUP BY metric_date
                ), ads AS (
                    SELECT metric_date AS report_date,
                        CASE WHEN sum(rows_with_actual_cost)=sum(source_rows) THEN sum(actual_cost) END AS ad_spend_rub
                    FROM yandex_marketing_daily
                    WHERE client_key=%s AND metric_date BETWEEN %s AND %s GROUP BY metric_date
                ), actual AS (
                    SELECT d.report_date,o.orders_rub,s.sales_rub,a.ad_spend_rub FROM days d
                    LEFT JOIN orders o USING(report_date) LEFT JOIN sales s USING(report_date)
                    LEFT JOIN ads a USING(report_date)
                )
                SELECT 'yandex_market' AS marketplace,'Яндекс Маркет' AS marketplace_label,
                    report_date,date_trunc('month',report_date)::date AS plan_month,
                    orders_rub,sales_rub,ad_spend_rub,
                    NULL::numeric AS sales_plan_rub,NULL::numeric AS ad_spend_plan_rub,
                    NULL::numeric AS sales_plan_daily_rub,NULL::numeric AS ad_spend_plan_daily_rub,
                    NULL::numeric AS sales_plan_elapsed_rub,NULL::numeric AS ad_spend_plan_elapsed_rub,
                    sum(orders_rub) OVER (PARTITION BY date_trunc('month',report_date) ORDER BY report_date) AS orders_cum_rub,
                    sum(sales_rub) OVER (PARTITION BY date_trunc('month',report_date) ORDER BY report_date) AS sales_cum_rub,
                    sum(ad_spend_rub) OVER (PARTITION BY date_trunc('month',report_date) ORDER BY report_date) AS ad_spend_cum_rub,
                    NULL::numeric AS sales_month_plan_fact_pct,NULL::numeric AS sales_elapsed_plan_fact_pct,
                    NULL::numeric AS ad_spend_budget_used_pct,NULL::numeric AS ad_spend_elapsed_budget_pct,
                    100.0*ad_spend_rub/nullif(sales_rub,0) AS tacos_pct,
                    100.0*sum(ad_spend_rub) OVER (PARTITION BY date_trunc('month',report_date) ORDER BY report_date)
                      /nullif(sum(sales_rub) OVER (PARTITION BY date_trunc('month',report_date) ORDER BY report_date),0) AS tacos_cum_pct
                FROM actual ORDER BY report_date
            """, (client, start, end, client, start, end, client, start, end,
                    client, start, end, client, start, end, client, start, end))
            rows = app.normalize_rows(cur.fetchall())
    return rows, "partial", "Яндекс Маркет: заказы — оплата покупателя с субсидией; продажи — доставленная сумма; планы отсутствуют"


def daily(app, parsed):
    rows, status, message = _daily(app, parsed)
    for row in rows:
        row.update(data_status=status, data_message=message)
    return {"rows": rows, "data_status": status, "data_message": message}


def _sum_present(rows, key):
    values = [_number(row.get(key)) for row in rows if row.get(key) is not None]
    return round(sum(values), 2) if values else None


def _score_row(rows, start: date, end: date, status: str, message: str):
    sales, orders, ads = (_sum_present(rows, key) for key in ("sales_rub", "orders_rub", "ad_spend_rub"))
    actual = [row for row in rows if any(row.get(key) is not None for key in ("orders_rub", "sales_rub", "ad_spend_rub"))]
    last = max((date.fromisoformat(str(row["report_date"])[:10]) for row in actual), default=None)
    days_in_month = calendar.monthrange(end.year, end.month)[1]
    sales_days = sum(row.get("sales_rub") is not None for row in rows)
    ad_days = sum(row.get("ad_spend_rub") is not None for row in rows)
    sales_runrate = round(sales / sales_days * days_in_month, 2) if sales is not None and sales_days else None
    ad_runrate = round(ads / ad_days * days_in_month, 2) if ads is not None and ad_days else None
    recent_start = max(start, last - timedelta(days=4)) if last else None
    recent = [row for row in rows if last and recent_start <= date.fromisoformat(str(row["report_date"])[:10]) <= last]
    recent_sales, recent_ads = _sum_present(recent, "sales_rub"), _sum_present(recent, "ad_spend_rub")
    recent_sales_days = sum(row.get("sales_rub") is not None for row in recent)
    recent_ad_days = sum(row.get("ad_spend_rub") is not None for row in recent)
    remaining = max(days_in_month - (last.day if last else 0), 0)
    sales_recent = round((sales or 0) + recent_sales / recent_sales_days * remaining, 2) if sales is not None and recent_sales is not None and recent_sales_days else None
    ad_recent = round((ads or 0) + recent_ads / recent_ad_days * remaining, 2) if ads is not None and recent_ads is not None and recent_ad_days else None
    return {"marketplace": "yandex_market", "marketplace_label": "Яндекс Маркет",
            "plan_month": end.replace(day=1).isoformat(), "last_date": last.isoformat() if last else None,
            "last_day": last.day if last else None, "recent_start_day": recent_start.day if recent_start else None,
            "days_in_month": days_in_month, "days_with_fact": len(actual), "orders_rub": orders,
            "sales_rub": sales, "ad_spend_rub": ads, "sales_plan_rub": None, "ad_spend_plan_rub": None,
            "sales_plan_fact_pct": None, "ad_spend_plan_fact_pct": None,
            "sales_runrate_rub": sales_runrate, "sales_runrate_pct": None,
            "sales_recent_runrate_rub": sales_recent, "sales_recent_runrate_pct": None,
            "ad_spend_runrate_rub": ad_runrate, "ad_spend_runrate_pct": None,
            "ad_spend_recent_runrate_rub": ad_recent, "ad_spend_recent_runrate_pct": None,
            "tacos_fact_pct": _ratio(ads, sales), "tacos_plan_pct": None,
            "data_status": status, "data_message": message}


def scorecard(app, parsed):
    start, end = _period(parsed.query)
    rows, status, message = _daily(app, parsed)
    return {"rows": [_score_row(rows, start, end, status, message)] if rows else [], "data_status": status, "data_message": message}


def summary(app, parsed):
    start, end = _period(parsed.query)
    rows, status, message = _daily(app, parsed)
    return _score_row(rows, start, end, status, message) if rows else {"data_status": status, "data_message": message}


def monthly(app, parsed):
    start, end = _period(parsed.query)
    rows, status, message = _daily(app, parsed)
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row["plan_month"])[:10], []).append(row)
    result = []
    for month, items in sorted(grouped.items()):
        month_start = date.fromisoformat(month)
        month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
        score = _score_row(items, month_start, month_end, status, message)
        result.append({"marketplace": "yandex_market", "marketplace_label": "Яндекс Маркет", "plan_month": month,
                       "date_from": max(start, month_start).isoformat(), "date_to": min(end, month_end).isoformat(),
                       "days_with_fact": score["days_with_fact"], "orders_rub": score["orders_rub"],
                       "sales_rub": score["sales_rub"], "ad_spend_rub": score["ad_spend_rub"],
                       "sales_plan_rub": None, "ad_spend_plan_rub": None, "sales_plan_fact_pct": None,
                       "ad_spend_budget_used_pct": None, "tacos_plan_pct": None, "tacos_fact_pct": score["tacos_fact_pct"],
                       "data_status": status, "data_message": message})
    return {"rows": result, "data_status": status, "data_message": message}
