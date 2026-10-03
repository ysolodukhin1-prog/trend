from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse


METRIC_GROUPS = [
    {
        "id": "sales",
        "label": "Продажи",
        "metrics": [
            {
                "id": "sales",
                "label": "Продажи, ₽",
                "description": "Накопленные продажи за текущий отчётный месяц относительно месячного плана.",
                "plan": "sales_plan_rub",
                "fact": "sales_rub",
                "completion": "sales_plan_fact_pct",
                "series_fact": "sales_cum_rub",
                "series_plan": "sales_plan_elapsed_rub",
                "format": "currency",
                "status_mode": "elapsed",
            },
            {
                "id": "orders_7d",
                "label": "Заказы 7 дней, ₽",
                "description": "Сумма заказов за последнее сопоставимое окно до семи дней относительно предыдущего окна той же длины.",
                "format": "currency",
                "status_mode": "trend",
                "derived": "orders_7d",
                "plan_label": "Пред. окно",
            },
            {
                "id": "sales_daily",
                "label": "Продажи в день, ₽",
                "description": "Средние дневные продажи за доступные дни месяца относительно дневного темпа плана.",
                "format": "currency",
                "status_mode": "target",
                "derived": "sales_daily",
                "plan_label": "План/день",
            },
            {
                "id": "sales_progress",
                "label": "Выполнение плана, %",
                "description": "Доля месячного плана продаж, выполненная к текущей дате, относительно календарного прогресса месяца.",
                "format": "percent",
                "status_mode": "target",
                "derived": "sales_progress",
                "plan_label": "Календарь",
            },
        ],
    },
    {
        "id": "promotion",
        "label": "Продвижение",
        "metrics": [
            {
                "id": "ad_spend",
                "label": "Расходы на рекламу, ₽",
                "description": "Накопленные рекламные расходы за месяц относительно утверждённого бюджета.",
                "plan": "ad_spend_plan_rub",
                "fact": "ad_spend_rub",
                "completion": "ad_spend_plan_fact_pct",
                "series_fact": "ad_spend_cum_rub",
                "series_plan": "ad_spend_plan_elapsed_rub",
                "format": "currency",
                "status_mode": "budget",
            },
            {
                "id": "tacos",
                "label": "TACOS, %",
                "description": "Доля рекламных расходов в продажах: расходы на рекламу / продажи × 100%.",
                "format": "percent",
                "status_mode": "lower",
                "derived": "tacos",
                "plan_label": "Цель",
            },
            {
                "id": "ad_spend_daily",
                "label": "Реклама в день, ₽",
                "description": "Средние дневные рекламные расходы относительно дневного темпа бюджета.",
                "format": "currency",
                "status_mode": "budget_target",
                "derived": "ad_spend_daily",
                "plan_label": "Бюджет/день",
            },
            {
                "id": "budget_progress",
                "label": "Использование бюджета, %",
                "description": "Использованная доля месячного рекламного бюджета относительно календарного прогресса месяца.",
                "format": "percent",
                "status_mode": "budget_target",
                "derived": "budget_progress",
                "plan_label": "Календарь",
            },
        ],
    },
    {
        "id": "forecast",
        "label": "Прогноз к концу месяца",
        "metrics": [
            {
                "id": "sales_forecast",
                "label": "Прогноз продаж, ₽",
                "description": "Прогноз продаж к концу месяца по темпу последних доступных дней относительно месячного плана.",
                "plan": "sales_plan_rub",
                "fact": "sales_recent_runrate_rub",
                "completion": "sales_recent_runrate_pct",
                "series_fact": "sales_cum_rub",
                "series_plan": "sales_plan_elapsed_rub",
                "format": "currency",
                "status_mode": "target",
                "plan_label": "План",
            },
            {
                "id": "sales_gap",
                "label": "Разрыв продаж, ₽",
                "description": "Ожидаемое отклонение прогноза продаж от месячного плана: прогноз минус план.",
                "format": "currency",
                "status_mode": "target",
                "derived": "sales_gap",
                "plan_label": "Норма",
            },
            {
                "id": "ad_spend_forecast",
                "label": "Прогноз расходов, ₽",
                "description": "Прогноз рекламных расходов к концу месяца по темпу последних доступных дней относительно бюджета.",
                "plan": "ad_spend_plan_rub",
                "fact": "ad_spend_recent_runrate_rub",
                "completion": "ad_spend_recent_runrate_pct",
                "series_fact": "ad_spend_cum_rub",
                "series_plan": "ad_spend_plan_elapsed_rub",
                "format": "currency",
                "status_mode": "budget_target",
                "plan_label": "Бюджет",
            },
            {
                "id": "budget_gap",
                "label": "Резерв бюджета, ₽",
                "description": "Ожидаемый остаток бюджета: месячный бюджет минус прогноз рекламных расходов.",
                "format": "currency",
                "status_mode": "budget_target",
                "derived": "budget_gap",
                "plan_label": "Норма",
            },
        ],
    },
]

PORTFOLIO_CLIENT_HEAD = ("gloria_jeans", "sportmaster", "km_trade", "boiron")
PORTFOLIO_CLIENT_TAIL = ("toptop", "lera_nena", "konstex")


def _portfolio_client_order(client):
    key = client.get("key")
    if key in PORTFOLIO_CLIENT_HEAD:
        return 0, PORTFOLIO_CLIENT_HEAD.index(key)
    if key in PORTFOLIO_CLIENT_TAIL:
        return 2, PORTFOLIO_CLIENT_TAIL.index(key)
    return 1, str(client.get("label") or key or "").casefold()

SIGNAL_GROUPS = [
    {
        "id": "funnel",
        "label": "Воронка продаж",
        "metrics": [
            {"id": "funnel_orders", "label": "Заказы", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_revenue", "label": "Выручка", "format": "currency", "dashboard": "funnel"},
            {"id": "funnel_impressions", "label": "Показы", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_visits", "label": "Переходы в карточку", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_carts", "label": "Корзины", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_cancellations", "label": "Отмены", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_returns", "label": "Возвраты", "format": "integer", "dashboard": "funnel"},
            {"id": "funnel_buyout", "label": "Выкуп", "format": "percent", "dashboard": "funnel"},
            {"id": "funnel_cancel_rate", "label": "Отмены от заказов", "format": "percent", "dashboard": "funnel"},
        ],
    },
    {
        "id": "conversion",
        "label": "Конверсионность",
        "metrics": [
            {"id": "conversion_impression_visit", "label": "Показ -> карточка", "format": "percent", "dashboard": "funnel"},
            {"id": "conversion_visit_cart", "label": "Карточка -> корзина", "format": "percent", "dashboard": "funnel"},
            {"id": "conversion_cart_order", "label": "Корзина -> заказ", "format": "percent", "dashboard": "funnel"},
            {"id": "conversion_visit_order", "label": "Карточка -> заказ", "format": "percent", "dashboard": "funnel"},
            {"id": "conversion_avg_order", "label": "Средний заказ", "format": "currency", "dashboard": "funnel"},
        ],
    },
    {
        "id": "advertising",
        "label": "Реклама",
        "metrics": [
            {"id": "adv_impressions", "label": "Показы рекламы", "format": "integer", "dashboard": "adv"},
            {"id": "adv_clicks", "label": "Клики", "format": "integer", "dashboard": "adv"},
            {"id": "adv_carts", "label": "Корзины", "format": "integer", "dashboard": "adv"},
            {"id": "adv_orders", "label": "Заказы", "format": "integer", "dashboard": "adv"},
            {"id": "adv_revenue", "label": "Выручка с рекламы", "format": "currency", "dashboard": "adv"},
            {"id": "adv_spend", "label": "Расходы", "format": "currency", "dashboard": "adv"},
            {"id": "adv_ctr", "label": "CTR", "format": "percent", "dashboard": "adv"},
            {"id": "adv_click_cart", "label": "Клик -> корзина", "format": "percent", "dashboard": "adv"},
            {"id": "adv_cart_order", "label": "Корзина -> заказ", "format": "percent", "dashboard": "adv"},
            {"id": "adv_drr", "label": "ДРР", "format": "percent", "dashboard": "adv"},
            {"id": "adv_tacos", "label": "TACOS", "format": "percent", "dashboard": "adv"},
            {"id": "adv_cpc", "label": "CPC", "format": "currency", "dashboard": "adv"},
            {"id": "adv_cpa", "label": "CPA", "format": "currency", "dashboard": "adv"},
        ],
    },
    {
        "id": "organic",
        "label": "Органика",
        "metrics": [
            {"id": "organic_impressions", "label": "Органические показы", "format": "integer", "dashboard": "funnel"},
            {"id": "organic_visits", "label": "Органические переходы", "format": "integer", "dashboard": "funnel"},
            {"id": "organic_carts", "label": "Органические корзины", "format": "integer", "dashboard": "funnel"},
            {"id": "organic_orders", "label": "Органические заказы", "format": "integer", "dashboard": "funnel"},
            {"id": "organic_order_share", "label": "Доля органических заказов", "format": "percent", "dashboard": "funnel"},
        ],
    },
    {
        "id": "planfact",
        "label": "План/факт",
        "metrics": [
            {"id": "pf_sales", "label": "Продажи", "format": "currency", "dashboard": "planfact"},
            {"id": "pf_sales_progress", "label": "Выполнение плана", "format": "percent", "dashboard": "planfact"},
            {"id": "pf_sales_forecast", "label": "Прогноз продаж", "format": "currency", "dashboard": "planfact"},
            {"id": "pf_sales_gap", "label": "Разрыв к плану", "format": "currency", "dashboard": "planfact"},
            {"id": "pf_orders_trend", "label": "Заказы 7 дней к пред. окну", "format": "currency", "dashboard": "planfact"},
            {"id": "pf_budget_progress", "label": "Использование бюджета", "format": "percent", "dashboard": "planfact"},
        ],
    },
    {
        "id": "seo",
        "label": "SEO и карточки",
        "metrics": [
            {"id": "seo_avg_position", "label": "Средняя позиция", "format": "number", "dashboard": "seoMonitoring"},
            {"id": "seo_top10_share", "label": "Запросы в топ-10", "format": "percent", "dashboard": "seoMonitoring"},
            {"id": "seo_card_rating", "label": "Рейтинг карточки", "format": "rating", "dashboard": "seoMonitoring"},
            {"id": "seo_review_rating", "label": "Рейтинг отзывов", "format": "rating", "dashboard": "seoMonitoring"},
        ],
    },
    {
        "id": "inventory",
        "label": "Запасы",
        "metrics": [
            {"id": "stock_qty", "label": "Доступный остаток", "format": "integer", "dashboard": "inventoryHistory"},
            {"id": "stock_sku", "label": "SKU в остатке", "format": "integer", "dashboard": "inventoryHistory"},
            {"id": "stock_oos", "label": "SKU без остатка", "format": "integer", "dashboard": "inventoryHistory"},
            {"id": "stock_days_cover", "label": "Дней запаса", "format": "days", "dashboard": "inventoryHistory"},
        ],
    },
]

DEFAULT_HORIZON_DAYS = 28
HORIZON_OPTIONS = [7, 14, 28, 56]
CARD_METRIC_IDS = [metric["id"] for group in METRIC_GROUPS for metric in group["metrics"]]
MAX_SUPPORT_METRICS = 8
DEFAULT_NORTH_STAR_METRIC = "sales"
DEFAULT_SUPPORT_METRIC_IDS = [
    "orders_7d",
    "sales_daily",
    "sales_progress",
    "ad_spend",
    "tacos",
    "ad_spend_daily",
    "budget_progress",
    "sales_forecast",
]
PORTFOLIO_CONFIG_ENV = "PULSE_PORTFOLIO_CONFIG_PATH"
PORTFOLIO_SNAPSHOT_ENV = "PULSE_PORTFOLIO_SNAPSHOT_PATH"
_CONFIG_LOCK = threading.Lock()
_PAYLOAD_CACHE_LOCK = threading.Lock()
_PAYLOAD_CACHE = {}
PAYLOAD_CACHE_TTL_SECONDS = 300
REPORT_NAV_ORDER = [
    "planfact",
    "salesPlanning",
    "mediaPlan",
    "profitLoss",
    "unitEconomics",
    "adv",
    "mediaAdv",
    "funnel",
    "weeklyDynamics",
    "inventoryHistory",
    "seoMonitoring",
    "wbSearchQueries",
    "wbAdSearchQueries",
    "wbEntrance",
    "abc",
    "product",
    "sku",
    "reviews",
    "commercialRadar",
]


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


def _iso_date(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


def _tone(completion, mode, elapsed_pct=None, plan=None, fact=None):
    completion = _number(completion)
    if completion is None:
        return "missing"
    if mode == "lower":
        if plan is None or fact is None:
            return "missing"
        if fact <= plan:
            return "good"
        return "warning" if fact <= plan * 1.12 else "risk"
    if mode == "trend":
        return "good" if completion >= 100 else ("warning" if completion >= 90 else "risk")
    if mode in {"budget", "budget_target"}:
        ceiling = 105 if mode == "budget_target" else (_number(elapsed_pct) or 100) + 8
        return "good" if completion <= ceiling else ("warning" if completion <= ceiling + 12 else "risk")
    if mode == "elapsed":
        expected = _number(elapsed_pct)
        if expected is None:
            expected = 100
        return "good" if completion >= expected - 7 else ("warning" if completion >= expected - 18 else "risk")
    return "good" if completion >= 95 else ("warning" if completion >= 80 else "risk")


def _derived_tacos(score, daily_rows):
    plan_sales = _positive(score.get("sales_plan_rub"))
    plan_spend = _positive(score.get("ad_spend_plan_rub"))
    fact_sales = _positive(score.get("sales_rub"))
    fact_spend = _number(score.get("ad_spend_rub"))
    plan = plan_spend / plan_sales * 100 if plan_sales and plan_spend else None
    fact = fact_spend / fact_sales * 100 if fact_sales and fact_spend is not None else None
    completion = fact / plan * 100 if plan and fact is not None else None
    series = []
    for row in daily_rows:
        sales = _positive(row.get("sales_cum_rub"))
        spend = _number(row.get("ad_spend_cum_rub"))
        series.append(
            {
                "date": _iso_date(row.get("report_date")),
                "fact": spend / sales * 100 if sales and spend is not None else None,
                "plan": plan,
            }
        )
    return plan, fact, completion, series


def _daily_average(score, daily_rows, fact_key, plan_key, total_plan_key):
    last_day = _positive(score.get("last_day"))
    days_in_month = _positive(score.get("days_in_month"))
    fact_total = _number(score.get(fact_key))
    plan_total = _positive(score.get(total_plan_key))
    fact = fact_total / last_day if fact_total is not None and last_day else None
    plan = plan_total / days_in_month if plan_total is not None and days_in_month else None
    completion = fact / plan * 100 if fact is not None and plan else None
    series = [
        {
            "date": _iso_date(row.get("report_date")),
            "fact": _number(row.get(fact_key.replace("_rub", "_rub"))),
            "plan": _number(row.get(plan_key)),
        }
        for row in daily_rows
    ]
    return plan, fact, completion, series


def _orders_7d(daily_rows):
    rows = [row for row in daily_rows if _number(row.get("orders_rub")) is not None]
    comparable_days = min(7, len(rows) // 2)
    current_rows = rows[-comparable_days:] if comparable_days else []
    previous_rows = rows[-2 * comparable_days:-comparable_days] if comparable_days else []
    fact = sum(_number(row.get("orders_rub")) or 0 for row in current_rows) if current_rows else None
    plan = sum(_number(row.get("orders_rub")) or 0 for row in previous_rows) if previous_rows else None
    completion = fact / plan * 100 if fact is not None and plan and plan > 0 else None
    baseline = plan / len(previous_rows) if plan is not None and previous_rows else None
    series = [
        {"date": _iso_date(row.get("report_date")), "fact": _number(row.get("orders_rub")), "plan": baseline}
        for row in rows
    ]
    return plan, fact, completion, series


def _progress_metric(score, daily_rows, fact_key, series_fact_key):
    last_day = _positive(score.get("last_day"))
    days_in_month = _positive(score.get("days_in_month"))
    plan = last_day / days_in_month * 100 if last_day and days_in_month else None
    fact = _number(score.get(fact_key))
    completion = fact / plan * 100 if fact is not None and plan else None
    series = []
    for row in daily_rows:
        report_date = row.get("report_date")
        try:
            day = report_date.day if isinstance(report_date, (date, datetime)) else datetime.fromisoformat(str(report_date)).day
        except (TypeError, ValueError):
            day = None
        calendar = day / days_in_month * 100 if day and days_in_month else None
        series.append({"date": _iso_date(report_date), "fact": _number(row.get(series_fact_key)), "plan": calendar})
    return plan, fact, completion, series


def _gap_metric(score, daily_rows, forecast_key, plan_key, series_fact_key, budget=False):
    forecast = _number(score.get(forecast_key))
    target = _positive(score.get(plan_key))
    days_in_month = _positive(score.get("days_in_month"))
    fact = (target - forecast) if budget and forecast is not None and target is not None else ((forecast - target) if forecast is not None and target is not None else None)
    completion = forecast / target * 100 if forecast is not None and target else None
    series = []
    for row in daily_rows:
        current = _number(row.get(series_fact_key))
        report_date = row.get("report_date")
        try:
            day = report_date.day if isinstance(report_date, (date, datetime)) else datetime.fromisoformat(str(report_date)).day
        except (TypeError, ValueError):
            day = None
        projected = current / day * days_in_month if current is not None and day and days_in_month else None
        gap = (target - projected) if budget and projected is not None and target is not None else ((projected - target) if projected is not None and target is not None else None)
        series.append({"date": _iso_date(row.get("report_date")), "fact": gap, "plan": 0})
    return (0 if fact is not None else None), fact, completion, series


def _metric_payload(metric, score, daily_rows, elapsed_pct):
    derived = metric.get("derived")
    if derived == "tacos":
        plan, fact, completion, series = _derived_tacos(score, daily_rows)
    elif derived == "orders_7d":
        plan, fact, completion, series = _orders_7d(daily_rows)
    elif derived == "sales_daily":
        plan, fact, completion, series = _daily_average(score, daily_rows, "sales_rub", "sales_plan_daily_rub", "sales_plan_rub")
    elif derived == "ad_spend_daily":
        plan, fact, completion, series = _daily_average(score, daily_rows, "ad_spend_rub", "ad_spend_plan_daily_rub", "ad_spend_plan_rub")
    elif derived == "sales_progress":
        plan, fact, completion, series = _progress_metric(score, daily_rows, "sales_plan_fact_pct", "sales_month_plan_fact_pct")
    elif derived == "budget_progress":
        plan, fact, completion, series = _progress_metric(score, daily_rows, "ad_spend_plan_fact_pct", "ad_spend_budget_used_pct")
    elif derived == "sales_gap":
        plan, fact, completion, series = _gap_metric(score, daily_rows, "sales_recent_runrate_rub", "sales_plan_rub", "sales_cum_rub")
    elif derived == "budget_gap":
        plan, fact, completion, series = _gap_metric(score, daily_rows, "ad_spend_recent_runrate_rub", "ad_spend_plan_rub", "ad_spend_cum_rub", budget=True)
    else:
        plan = _positive(score.get(metric.get("plan")))
        fact = _number(score.get(metric.get("fact")))
        completion = _number(score.get(metric.get("completion"))) if plan is not None else None
        series = [
            {
                "date": _iso_date(row.get("report_date")),
                "fact": _number(row.get(metric.get("series_fact"))),
                "plan": _number(row.get(metric.get("series_plan"))),
            }
            for row in daily_rows
        ]
    series = [point for point in series if point["date"] and (point["fact"] is not None or point["plan"] is not None)]
    return {
        "id": metric["id"],
        "plan": plan,
        "fact": fact,
        "completion": completion,
        "tone": _tone(completion, metric.get("status_mode"), elapsed_pct, plan, fact),
        "series": series[-62:],
    }


def _sum_present(rows, key):
    values = [_number(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _ratio(numerator, denominator):
    numerator = _number(numerator)
    denominator = _number(denominator)
    return round(numerator / denominator * 100, 2) if numerator is not None and denominator else None


def _signal_tone(metric_id, value):
    value = _number(value)
    if value is None:
        return "missing"
    if metric_id == "funnel_buyout":
        return "good" if value >= 80 else ("warning" if value >= 65 else "risk")
    if metric_id == "funnel_cancel_rate":
        return "good" if value <= 10 else ("warning" if value <= 20 else "risk")
    if metric_id == "adv_ctr":
        return "good" if value >= 2 else ("warning" if value >= 1 else "risk")
    if metric_id in {"adv_drr", "adv_tacos"}:
        return "good" if value <= 15 else ("warning" if value <= 25 else "risk")
    if metric_id == "stock_days_cover":
        return "good" if 21 <= value <= 60 else ("warning" if 14 <= value <= 90 else "risk")
    if metric_id in {"seo_card_rating", "seo_review_rating"}:
        return "good" if value >= 4.5 else ("warning" if value >= 4 else "risk")
    return "neutral"


def _source_rows(app, client_key, reports, report_id, handler_name, dashboard=None, marketplace=None):
    if report_id not in reports or not hasattr(app, handler_name) or not hasattr(app, "review_source_payload"):
        return [], "Источник не подключён"
    handler = getattr(app, handler_name)
    if hasattr(app, "client_marketplaces_payload"):
        marketplaces = [str(item.get("id") or "") for item in app.client_marketplaces_payload(client_key) if item.get("id")]
    else:
        marketplaces = app.review_marketplaces_for_report(client_key, report_id) if hasattr(app, "review_marketplaces_for_report") else ["ozon"]
    if marketplace:
        marketplaces = [item for item in marketplaces if item == marketplace]
    rows = []
    errors = []
    for marketplace in marketplaces:
        try:
            payload = app.review_source_payload(client_key, handler, dashboard or report_id, marketplace)
            if isinstance(payload, dict):
                rows.append({**payload, "_marketplace": marketplace})
        except Exception as exc:
            errors.append(type(exc).__name__)
    if rows:
        return rows, ""
    return [], "Нет витрины или данных за текущий месяц" if errors else "Источник не подключён"


def _signal(metric_id, value, client_key, dashboard, source, *, marketplace=None, reason="", tone=None, trend="", plan=None, completion=None, series=None):
    link_params = {"client": client_key, "dashboard": dashboard}
    if marketplace:
        link_params["marketplace"] = marketplace
    return {
        "id": metric_id,
        "fact": _number(value),
        "plan": _number(plan),
        "completion": _number(completion),
        "tone": tone or _signal_tone(metric_id, value),
        "trend": trend,
        "source": source,
        "reason": reason if value is None else "",
        "dashboard_url": "?" + urlencode(link_params),
        "series": list(series or [])[-62:],
    }


def _build_signal_payload(app, client, planfact_metrics, marketplace=None):
    key = client["key"]
    reports = client.get("reports") or []
    signals = {}

    def put(metric_id, value, dashboard, source, **kwargs):
        signals[metric_id] = _signal(metric_id, value, key, dashboard, source, marketplace=marketplace, **kwargs)

    planfact_map = {
        "pf_sales": "sales",
        "pf_sales_progress": "sales_progress",
        "pf_sales_forecast": "sales_forecast",
        "pf_sales_gap": "sales_gap",
        "pf_orders_trend": "orders_7d",
        "pf_budget_progress": "budget_progress",
    }
    for signal_id, metric_id in planfact_map.items():
        metric = planfact_metrics.get(metric_id) or {}
        trend = ""
        if signal_id == "pf_orders_trend" and _number(metric.get("completion")) is not None:
            trend = "improving" if _number(metric.get("completion")) >= 103 else ("worsening" if _number(metric.get("completion")) <= 97 else "stable")
        put(
            signal_id,
            metric.get("fact"),
            "planfact",
            "Plan/Fact",
            reason="Нет Plan/Fact за текущий период",
            tone=metric.get("tone") or "missing",
            trend=trend,
            plan=metric.get("plan"),
            completion=metric.get("completion"),
            series=metric.get("series"),
        )

    funnel_rows, funnel_reason = _source_rows(app, key, reports, "funnel", "handle_funnel_summary", marketplace=marketplace)
    orders = _sum_present(funnel_rows, "ordered_units")
    revenue = _sum_present(funnel_rows, "ordered_amount_rub")
    impressions = _sum_present(funnel_rows, "impressions_total")
    visits = _sum_present(funnel_rows, "card_visits")
    carts = _sum_present(funnel_rows, "cart_adds")
    upper_available = bool(funnel_rows) and not ((orders or 0) > 0 and not any((row.get("impressions_total") or row.get("card_visits") or row.get("cart_adds")) for row in funnel_rows))
    lifecycle_available = bool(funnel_rows) and not ((orders or 0) > 0 and not any((row.get("bought_units") or row.get("delivered_units") or row.get("returned_units") or row.get("cancelled_units")) for row in funnel_rows))
    cancellations = _sum_present(funnel_rows, "cancelled_units") if lifecycle_available else None
    returns = _sum_present(funnel_rows, "returned_units") if lifecycle_available else None
    bought = _sum_present(funnel_rows, "cohort_bought_units" if marketplace == "wb" else "bought_units") if lifecycle_available else None
    put("funnel_orders", orders, "funnel", "Воронка", reason=funnel_reason)
    put("funnel_revenue", revenue, "funnel", "Воронка", reason=funnel_reason)
    put("funnel_impressions", impressions if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Верх воронки источник не передал")
    put("funnel_visits", visits if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Переходы источник не передал")
    put("funnel_carts", carts if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Корзины источник не передал")
    put("funnel_cancellations", cancellations, "funnel", "Воронка", reason=funnel_reason or "Отмены источник не передал")
    put("funnel_returns", returns, "funnel", "Воронка", reason=funnel_reason or "Возвраты источник не передал")
    put("funnel_buyout", _ratio(bought, orders), "funnel", "Воронка", reason=funnel_reason or "Нет данных о выкупе")
    put("funnel_cancel_rate", _ratio(cancellations, orders), "funnel", "Воронка", reason=funnel_reason or "Нет данных об отменах")
    put("conversion_impression_visit", _ratio(visits, impressions) if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Нет показов и переходов")
    put("conversion_visit_cart", _ratio(carts, visits) if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Нет переходов и корзин")
    put("conversion_cart_order", _ratio(orders, carts) if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Нет корзин")
    put("conversion_visit_order", _ratio(orders, visits) if upper_available else None, "funnel", "Воронка", reason=funnel_reason or "Нет переходов")
    put("conversion_avg_order", (revenue / orders if revenue is not None and orders else None), "funnel", "Воронка", reason=funnel_reason or "Нет заказов")

    adv_rows, adv_reason = _source_rows(app, key, reports, "adv", "handle_adv_summary", marketplace=marketplace)
    adv_impressions = _sum_present(adv_rows, "impressions")
    adv_clicks = _sum_present(adv_rows, "clicks")
    adv_spend = _sum_present(adv_rows, "expense_rub")
    adv_carts_available = any(bool(row.get("added_to_cart_available")) for row in adv_rows)
    adv_carts = _sum_present(adv_rows, "added_to_cart") if adv_carts_available else None
    adv_orders = sum(
        max(_number(row.get("orders_qty")) or 0, (_number(row.get("direct_orders_qty")) or 0) + (_number(row.get("indirect_orders_qty")) or 0))
        for row in adv_rows
    ) if adv_rows else None
    adv_revenue = sum(
        max(_number(row.get("orders_amount_rub")) or 0, (_number(row.get("direct_orders_amount_rub")) or 0) + (_number(row.get("indirect_orders_amount_rub")) or 0))
        for row in adv_rows
    ) if adv_rows else None
    put("adv_impressions", adv_impressions, "adv", "Реклама", reason=adv_reason)
    put("adv_clicks", adv_clicks, "adv", "Реклама", reason=adv_reason)
    put("adv_carts", adv_carts, "adv", "Реклама", reason=adv_reason or "API не передал корзины")
    put("adv_orders", adv_orders, "adv", "Реклама", reason=adv_reason)
    put("adv_revenue", adv_revenue, "adv", "Реклама", reason=adv_reason)
    put("adv_spend", adv_spend, "adv", "Реклама", reason=adv_reason)
    put("adv_ctr", _ratio(adv_clicks, adv_impressions), "adv", "Реклама", reason=adv_reason or "Нет показов")
    put("adv_click_cart", _ratio(adv_carts, adv_clicks), "adv", "Реклама", reason=adv_reason or "API не передал корзины")
    put("adv_cart_order", _ratio(adv_orders, adv_carts), "adv", "Реклама", reason=adv_reason or "API не передал корзины")
    put("adv_drr", _ratio(adv_spend, adv_revenue), "adv", "Реклама", reason=adv_reason or "Нет рекламной выручки")
    put("adv_tacos", _ratio(adv_spend, revenue), "adv", "Реклама", reason=adv_reason or "Нет общей выручки")
    put("adv_cpc", (adv_spend / adv_clicks if adv_spend is not None and adv_clicks else None), "adv", "Реклама", reason=adv_reason or "Нет кликов")
    put("adv_cpa", (adv_spend / adv_orders if adv_spend is not None and adv_orders else None), "adv", "Реклама", reason=adv_reason or "Нет рекламных заказов")

    organic_impressions = _sum_present(funnel_rows, "organic_impressions") if upper_available else None
    organic_visits = _sum_present(funnel_rows, "organic_card_visits") if upper_available else None
    organic_carts = _sum_present(funnel_rows, "organic_cart_adds") if upper_available else None
    organic_orders = _sum_present(funnel_rows, "organic_orders")
    put("organic_impressions", organic_impressions, "funnel", "Воронка", reason=funnel_reason or "Органические показы не переданы")
    put("organic_visits", organic_visits, "funnel", "Воронка", reason=funnel_reason or "Органические переходы не переданы")
    put("organic_carts", organic_carts, "funnel", "Воронка", reason=funnel_reason or "Органические корзины не переданы")
    put("organic_orders", organic_orders, "funnel", "Воронка", reason=funnel_reason)
    put("organic_order_share", _ratio(organic_orders, orders), "funnel", "Воронка", reason=funnel_reason or "Нет заказов")

    stock_rows, stock_reason = _source_rows(app, key, reports, "abc", "handle_summary", "abc", marketplace=marketplace)
    put("stock_qty", _sum_present(stock_rows, "total_stock_qty"), "inventoryHistory", "ABC / остатки", reason=stock_reason)
    put("stock_sku", _sum_present(stock_rows, "sku_count"), "inventoryHistory", "ABC / остатки", reason=stock_reason)
    put("stock_oos", None, "inventoryHistory", "История запасов", reason="Метрика будет доступна после подключения мониторинга OOS")
    put("stock_days_cover", None, "inventoryHistory", "История запасов", reason="Метрика будет доступна после подключения спроса к запасам")

    for metric_id in ("seo_avg_position", "seo_top10_share", "seo_card_rating", "seo_review_rating"):
        put(metric_id, None, "seoMonitoring", "SEO", reason="Агрегат по портфелю ещё не подключён")
    return signals


def _client_payload(app, client, marketplace="total"):
    key = client["key"]
    reports = client.get("reports") or []
    dashboard = "planfact" if "planfact" in reports else app.preferred_client_dashboard(reports)
    result = {
        "key": key,
        "label": client.get("label") or key,
        "marketplace": marketplace,
        "dashboard_url": "?" + urlencode({"client": key, "dashboard": dashboard}),
        "period": None,
        "source": "План/факт не подключён",
        "has_planfact": False,
        "metrics": {},
        "signals": {},
        "error": None,
    }
    if "planfact" not in reports:
        result["signals"] = _build_signal_payload(app, client, {}, marketplace)
        return result

    today = date.today()
    month_start = today.replace(day=1)
    query = urlencode({
        "client": key,
        "marketplace": marketplace,
        "date_from": month_start.isoformat(),
        "date_to": today.isoformat(),
    })
    parsed_scorecard = urlparse(f"/api/planfact-scorecard?{query}")
    parsed_daily = urlparse(f"/api/planfact-daily?{query}")
    client_token = app.CURRENT_CLIENT.set(key)
    try:
        score_rows = (app.handle_planfact_scorecard(parsed_scorecard) or {}).get("rows") or []
        daily_rows = (app.handle_planfact_daily(parsed_daily) or {}).get("rows") or []
    except Exception as exc:
        result["error"] = type(exc).__name__
        result["signals"] = _build_signal_payload(app, client, {}, marketplace)
        return result
    finally:
        app.CURRENT_CLIENT.reset(client_token)

    if not score_rows:
        result["signals"] = _build_signal_payload(app, client, {}, marketplace)
        return result
    score = score_rows[0]
    score_period = _iso_date(score.get("plan_month"))
    if score_period:
        daily_rows = [row for row in daily_rows if _iso_date(row.get("plan_month")) == score_period]
    last_day = _number(score.get("last_day") or score.get("orders_as_of_day") or score.get("promotion_as_of_day"))
    days_in_month = _number(score.get("days_in_month"))
    elapsed_pct = last_day / days_in_month * 100 if last_day and days_in_month else None
    result["has_planfact"] = True
    result["period"] = score_period or None
    result["source"] = "Plan/Fact · БД клиента"
    for group in METRIC_GROUPS:
        for metric in group["metrics"]:
            result["metrics"][metric["id"]] = _metric_payload(metric, score, daily_rows, elapsed_pct)
    result["signals"] = _build_signal_payload(app, client, result["metrics"], marketplace)
    return result


def _client_marketplace_ids(app, client):
    key = client["key"]
    if hasattr(app, "client_marketplaces_payload"):
        values = [str(item.get("id") or "") for item in app.client_marketplaces_payload(key)]
    else:
        values = list(client.get("marketplaces") or [])
    return [marketplace for marketplace in ("wb", "ozon") if marketplace in values]


def _report_navigation(app, clients):
    label_by_id = {item["id"]: item["label"] for item in app.ADMIN_REPORT_CATALOG}
    first_client_by_report = {}
    for client in clients:
        for report_id in client.get("reports") or []:
            first_client_by_report.setdefault(report_id, client["key"])
    ordered_ids = [report_id for report_id in REPORT_NAV_ORDER if report_id in first_client_by_report]
    ordered_ids.extend(
        report_id
        for report_id in label_by_id
        if report_id in first_client_by_report and report_id not in ordered_ids
    )
    return [
        {
            "id": report_id,
            "label": label_by_id.get(report_id, report_id),
            "client": first_client_by_report[report_id],
            "url": "?" + urlencode({"client": first_client_by_report[report_id], "dashboard": report_id}),
        }
        for report_id in ordered_ids
    ]


def _config_path():
    configured = str(os.environ.get(PORTFOLIO_CONFIG_ENV) or "").strip()
    return Path(configured) if configured else Path(__file__).resolve().with_name(".pulse_portfolio_config.json")


def _snapshot_path():
    configured = str(os.environ.get(PORTFOLIO_SNAPSHOT_ENV) or "").strip()
    return Path(configured) if configured else Path(__file__).resolve().with_name(".pulse_portfolio_snapshot.json")


def _load_snapshot(scope, client_keys):
    path = _snapshot_path()
    try:
        root = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        return None
    entry = (root.get("scopes") or {}).get(scope) if isinstance(root, dict) else None
    if not isinstance(entry, dict) or tuple(entry.get("client_keys") or []) != tuple(client_keys):
        return None
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) and payload.get("ok") else None


def _save_snapshot(scope, client_keys, payload):
    path = _snapshot_path()
    with _PAYLOAD_CACHE_LOCK:
        try:
            root = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError, TypeError):
            root = {}
        if not isinstance(root, dict):
            root = {}
        root["version"] = 1
        root.setdefault("scopes", {})[scope] = {
            "client_keys": list(client_keys),
            "payload": payload,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(root, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def _config_scope(app):
    access_user = app.CURRENT_ACCESS_USER.get() or {}
    if access_user.get("user_id"):
        return f"user:{access_user['user_id']}"
    username = str(access_user.get("username") or "").strip().lower()
    if username:
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]
        return f"identity:{digest}"
    return "public"


def _default_monitoring_config(client_rows):
    preferred = next((client for client in client_rows if client.get("key") == "gloria_jeans"), None)
    preferred = preferred or next((client for client in client_rows if client.get("has_planfact")), None)
    preferred = preferred or (client_rows[0] if client_rows else None)
    clients = []
    if preferred:
        clients.append(
            {
                "key": preferred["key"],
                "north_star": DEFAULT_NORTH_STAR_METRIC,
                "support": list(DEFAULT_SUPPORT_METRIC_IDS),
            }
        )
    return {"clients": clients}


def _normalize_monitoring_config(payload, client_rows, strict=False):
    available_clients = {client["key"] for client in client_rows}
    source_clients = payload.get("clients") if isinstance(payload, dict) else None
    if not isinstance(source_clients, list):
        if strict:
            raise ValueError("Поле clients должно быть массивом")
        return _default_monitoring_config(client_rows)

    normalized = []
    used_clients = set()
    for raw in source_clients:
        if not isinstance(raw, dict):
            if strict:
                raise ValueError("Настройка клиента должна быть объектом")
            continue
        key = str(raw.get("key") or "").strip()
        if key not in available_clients or key in used_clients:
            if strict:
                raise ValueError("Клиент недоступен или уже добавлен")
            continue
        north_star = str(raw.get("north_star") or DEFAULT_NORTH_STAR_METRIC).strip()
        if north_star not in CARD_METRIC_IDS:
            if strict:
                raise ValueError("Неизвестная North Star-метрика")
            north_star = DEFAULT_NORTH_STAR_METRIC
        raw_support = raw.get("support") or []
        if not isinstance(raw_support, list):
            if strict:
                raise ValueError("Поддерживающие метрики должны быть массивом")
            raw_support = []
        support = []
        for metric_id in raw_support:
            metric_id = str(metric_id or "").strip()
            if metric_id not in CARD_METRIC_IDS or metric_id == north_star or metric_id in support:
                if strict and metric_id not in CARD_METRIC_IDS:
                    raise ValueError("Неизвестная поддерживающая метрика")
                continue
            support.append(metric_id)
        if len(support) > MAX_SUPPORT_METRICS:
            if strict:
                raise ValueError(f"Можно выбрать не более {MAX_SUPPORT_METRICS} поддерживающих метрик")
            support = support[:MAX_SUPPORT_METRICS]
        normalized.append({"key": key, "north_star": north_star, "support": support})
        used_clients.add(key)
    return {"clients": normalized}


def load_monitoring_config(app, client_rows):
    path = _config_path()
    scope = _config_scope(app)
    with _CONFIG_LOCK:
        try:
            root = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError, TypeError):
            root = {}
    saved = (root.get("scopes") or {}).get(scope) if isinstance(root, dict) else None
    return _normalize_monitoring_config(saved or _default_monitoring_config(client_rows), client_rows)


def save_monitoring_config(app, payload):
    clients = [client for client in app.dashboard_clients_payload() if client.get("status") != "paused"]
    client_rows = [{"key": client["key"], "has_planfact": "planfact" in (client.get("reports") or [])} for client in clients]
    normalized = _normalize_monitoring_config(payload, client_rows, strict=True)
    path = _config_path()
    scope = _config_scope(app)
    with _CONFIG_LOCK:
        try:
            root = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError, TypeError):
            root = {}
        if not isinstance(root, dict):
            root = {}
        root["version"] = 1
        root.setdefault("scopes", {})[scope] = normalized
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return {"ok": True, "config": normalized}


def build_payload(app, force=False):
    clients = sorted(
        (client for client in app.dashboard_clients_payload() if client.get("status") != "paused"),
        key=_portfolio_client_order,
    )
    scope = _config_scope(app)
    client_keys = tuple(client["key"] for client in clients)
    cache_key = (scope, client_keys)
    if not force:
        with _PAYLOAD_CACHE_LOCK:
            cached = _PAYLOAD_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < PAYLOAD_CACHE_TTL_SECONDS:
                return cached[1]
        snapshot = _load_snapshot(scope, client_keys)
        if snapshot:
            with _PAYLOAD_CACHE_LOCK:
                _PAYLOAD_CACHE[cache_key] = (time.monotonic(), snapshot)
            return snapshot
    board_tasks = [
        (client, marketplace)
        for client in clients
        for marketplace in _client_marketplace_ids(app, client)
    ]
    workers = min(12, max(1, len(board_tasks)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pulse-portfolio") as executor:
        board_rows = list(executor.map(lambda item: _client_payload(app, item[0], item[1]), board_tasks))
    boards = [
        {"marketplace": marketplace, "label": label, "clients": [row for row in board_rows if row["marketplace"] == marketplace]}
        for marketplace, label in (("wb", "Wildberries"), ("ozon", "Ozon"))
    ]
    client_rows = [next((row for row in board_rows if row["key"] == client["key"]), {
        "key": client["key"], "label": client.get("label") or client["key"], "marketplace": None,
        "dashboard_url": "?" + urlencode({"client": client["key"], "dashboard": app.preferred_client_dashboard(client.get("reports") or [])}),
        "period": None, "source": "Источник не подключён", "has_planfact": False, "metrics": {}, "signals": {}, "error": None,
    }) for client in clients]
    available = sum(1 for client in client_rows if client["has_planfact"])
    periods = sorted({client["period"] for client in board_rows if client.get("period")})
    payload = {
        "ok": True,
        "title": "Портфель клиентов",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "clients": len(client_rows),
            "with_planfact": available,
            "coverage_pct": round(available / len(client_rows) * 100) if client_rows else 0,
            "periods": periods,
        },
        "default_horizon_days": DEFAULT_HORIZON_DAYS,
        "horizon_options": HORIZON_OPTIONS,
        "card_metric_ids": CARD_METRIC_IDS,
        "metric_groups": METRIC_GROUPS,
        "signal_groups": SIGNAL_GROUPS,
        "marketplace_boards": boards,
        "reports": _report_navigation(app, clients),
        "clients": client_rows,
        "config": load_monitoring_config(app, client_rows),
        "max_support_metrics": MAX_SUPPORT_METRICS,
        "methodology": "Сигнальная доска читает текущий месяц из действующих Plan/Fact, воронки, рекламы и ABC-витрин каждой клиентской БД. Мягкая заливка показывает пороговый статус; серый фон означает отсутствие источника или агрегата. Неподключённые показатели не заменяются нулями.",
    }
    with _PAYLOAD_CACHE_LOCK:
        _PAYLOAD_CACHE[cache_key] = (time.monotonic(), payload)
    _save_snapshot(scope, client_keys, payload)
    return payload


def render_page(static_dir: Path):
    styles = (Path(static_dir) / "pulse_portfolio.css").read_text(encoding="utf-8")
    script = (Path(static_dir) / "pulse_portfolio.js").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PULSE</title>
  <style>{styles}</style>
</head>
<body>
  <div class="pulse-shell">
    <aside class="pulse-rail" aria-label="Навигация PULSE">
      <a class="pulse-logo" href="/glory/" aria-label="PULSE · Портфель клиентов">
        <svg class="pulse-logo-mark" viewBox="0 0 46 46" aria-hidden="true" focusable="false">
          <circle cx="23" cy="23" r="23" fill="#ff4747"/>
          <rect class="pulse-logo-bar" x="8" y="18" width="4" height="10" rx="2" fill="#fff"/>
          <rect class="pulse-logo-bar" x="14.5" y="14" width="4" height="18" rx="2" fill="#fff"/>
          <rect class="pulse-logo-bar" x="21" y="10" width="4" height="26" rx="2" fill="#fff"/>
          <rect class="pulse-logo-bar" x="27.5" y="14" width="4" height="18" rx="2" fill="#fff"/>
          <rect class="pulse-logo-bar" x="34" y="18" width="4" height="10" rx="2" fill="#fff"/>
        </svg>
      </a>
      <nav class="rail-nav" aria-label="Разделы">
        <a class="rail-action is-active" href="/glory/" title="Общий отчёт" aria-label="Общий отчёт">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="7" height="7" rx="2"/><rect x="14" y="4" width="7" height="7" rx="2"/><rect x="3" y="15" width="7" height="6" rx="2"/><rect x="14" y="15" width="7" height="6" rx="2"/></svg>
          <span class="rail-label">Общий</span>
        </a>
        <div class="rail-divider" aria-hidden="true"></div>
        <div id="reportRailLinks" class="rail-report-list" aria-label="Отчёты PULSE">
          <span class="rail-loading" aria-hidden="true"></span>
        </div>
        <a class="rail-action rail-seo-bot" href="http://127.0.0.1:8765/?client=gloria_jeans" target="_blank" rel="noopener" title="Семантика, генерация SEO" aria-label="Семантика, генерация SEO">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/></svg>
          <span class="rail-label">Семантика</span>
        </a>
        <a class="rail-action rail-admin" href="?dashboard=admin" title="Администрирование" aria-label="Администрирование">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v1H7a3 3 0 0 0-3 3v6a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-6a3 3 0 0 0-3-3h-2V6a3 3 0 0 0-3-3Z"/><path d="M9 7V6a3 3 0 0 1 6 0v1M9 13h6"/></svg>
          <span class="rail-label">Админ</span>
        </a>
      </nav>
    </aside>

    <main class="portfolio" aria-labelledby="portfolioTitle">
      <header class="portfolio-head">
        <div class="head-copy">
          <div class="pulse-wordmark">PULSE</div>
          <div>
            <p class="eyebrow">ОБЩИЙ КОНТУР · КЛИЕНТСКИЙ ПОРТФЕЛЬ</p>
            <h1 id="portfolioTitle">Портфель клиентов</h1>
          </div>
        </div>
        <div class="head-actions">
          <label class="horizon-control" for="horizonDays" hidden><span>Горизонт</span><select id="horizonDays" aria-label="Горизонт динамики"></select></label>
          <span id="refreshStatus" class="refresh-status" aria-live="polite">Обновление…</span>
          <button id="refreshPortfolio" class="icon-button" type="button" title="Обновить данные" aria-label="Обновить данные">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.34 5.66"/><path d="M20 4v7h-7"/></svg>
          </button>
        </div>
      </header>

      <section class="clients-panel" aria-labelledby="clientsTitle">
        <div class="clients-toolbar">
          <div>
            <p class="eyebrow">СИГНАЛЬНАЯ ДОСКА · ТЕКУЩИЙ МЕСЯЦ</p>
            <h2 id="clientsTitle">Аккаунты и метрики</h2>
          </div>
          <div class="toolbar-actions">
            <div class="legend" aria-label="Легенда статусов">
              <span><i class="dot good"></i>в норме</span>
              <span><i class="dot warning"></i>внимание</span>
              <span><i class="dot risk"></i>риск</span>
              <span><i class="dot neutral"></i>без порога</span>
              <span><i class="dot missing"></i>нет данных</span>
            </div>
            <button id="addClient" class="primary-button" type="button" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>Добавить клиента</button>
          </div>
        </div>
        <div id="clientGrid" class="signal-board" aria-live="polite">
          <div class="loading-state">Загружаем клиентские витрины…</div>
        </div>
        <p id="methodology" class="methodology"></p>
      </section>
    </main>
  </div>

  <dialog id="chartDialog" class="chart-dialog" aria-labelledby="chartTitle">
    <div class="dialog-head">
      <div><p id="chartClient" class="eyebrow"></p><h2 id="chartTitle">Динамика метрики</h2></div>
      <button id="closeChart" class="icon-button" type="button" title="Закрыть" aria-label="Закрыть"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    </div>
    <div id="chartSummary" class="chart-summary"></div>
    <div id="chartCanvas" class="chart-canvas"></div>
    <div class="chart-legend"><span><i class="line actual"></i>Факт</span><span><i class="line plan"></i>План</span></div>
  </dialog>

  <dialog id="monitoringDialog" class="monitoring-dialog" aria-labelledby="monitoringTitle">
    <form id="monitoringForm" method="dialog">
      <div class="dialog-head">
        <div><p class="eyebrow">НАСТРОЙКА МОНИТОРИНГА</p><h2 id="monitoringTitle">Добавить клиента</h2></div>
        <button id="closeMonitoring" class="icon-button" type="button" title="Закрыть" aria-label="Закрыть"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      </div>
      <div class="editor-body">
        <label class="field-label" for="monitoringClient">Клиент<select id="monitoringClient" required></select></label>
        <section class="metric-picker-section" aria-labelledby="northStarTitle">
          <div class="picker-head"><div><p class="eyebrow">ГЛАВНЫЙ СИГНАЛ</p><h3 id="northStarTitle">North Star-метрика</h3></div><span>1 метрика · большой график</span></div>
          <div id="northStarOptions" class="metric-options north-star-options"></div>
        </section>
        <section class="metric-picker-section" aria-labelledby="supportTitle">
          <div class="picker-head"><div><p class="eyebrow">ДРАЙВЕРЫ</p><h3 id="supportTitle">Поддерживающие метрики</h3></div><span id="supportCount">0 / 8</span></div>
          <div id="supportMetricOptions" class="metric-options support-options"></div>
        </section>
      </div>
      <footer class="dialog-actions">
        <span id="configStatus" class="config-status" aria-live="polite"></span>
        <button id="cancelMonitoring" class="secondary-button" type="button">Отмена</button>
        <button class="primary-button" type="submit">Сохранить</button>
      </footer>
    </form>
  </dialog>

  <script>{script}</script>
</body>
</html>"""
