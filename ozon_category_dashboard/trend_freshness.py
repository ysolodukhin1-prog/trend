"""Fail-closed freshness checks for every report-view stage in TREND admin."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

import psycopg2
from psycopg2 import sql

from trend_views import VIEW_GROUPS, VIEW_STATE_TABLE


PROBES = {
    "wb_view_funnel": (("mv_wb_funnel_daily_by_article_category", "report_date", "daily"),),
    "wb_view_abc": (("mv_wb_abc_product_orders_base", "report_date", "daily"),),
    "wb_view_rollups": (("mv_wb_funnel_daily_summary_rollup", "report_date", "daily"),),
    "wb_view_product_ads": (("mv_wb_adv_daily_by_article_category", "report_date", "daily"),),
    "wb_view_search_ads": (("mv_wb_ad_search_query_daily", "report_date", "daily"),),
    "wb_view_media": (("mv_wb_media_adv_campaign_daily", "report_date", "daily"),),
    "ozon_view_funnel": (("mv_ozon_funnel_daily_by_article_category", "report_date", "daily"),),
    "ozon_view_abc": (("mv_ozon_abc_product_orders_base", "report_date", "daily"),),
    "ozon_view_rollups": (("mv_ozon_funnel_daily_summary_rollup", "report_date", "daily"),),
    "ozon_view_product_ads": (("mv_ozon_adv_daily_by_article_category", "report_date", "daily"),),
    "ozon_view_planfact": (("mv_planfact_daily", "report_date", "daily"),),
    "ozon_view_media": (("mv_ozon_media_adv_daily", "report_date", "daily"),),
    "yandex_view_funnel": (("yandex_mart_funnel_daily", "metric_date", "daily"),),
    "yandex_view_orders": (
        ("mv_pulse_yandex_orders_daily_v1", "order_date", "daily"),
        ("mv_pulse_yandex_sku_orders_daily_v1", "order_date", "daily"),
    ),
    "yandex_view_finance": (
        ("mv_pulse_yandex_services_daily_v1", "service_date", "daily"),
        ("mv_pulse_yandex_transactions_daily_v1", "transaction_date", "daily"),
        ("mv_pulse_yandex_marketing_daily_v1", "metric_date", "daily"),
        ("mv_pulse_yandex_realization_v1", "event_date", "closed_month"),
    ),
    "yandex_view_stocks": (("mv_pulse_yandex_stocks_v1", "snapshot_date", "snapshot"),),
    "yandex_view_quality": (("mv_pulse_yandex_data_coverage_v1", "requested_date_to", "daily"),),
}

DEPENDENCIES = {
    "wb_view_funnel": ("wb_funnel",),
    "wb_view_abc": ("wb_orders_sales", "wb_stock_current", "wb_catalog"),
    "wb_view_rollups": ("wb_funnel",),
    "wb_view_product_ads": ("wb_advertising",),
    "wb_view_search_ads": ("wb_search",),
    "wb_view_media": ("wb_media",),
    "ozon_view_funnel": ("ozon_funnel",),
    "ozon_view_abc": ("ozon_funnel", "ozon_stock", "ozon_assortment"),
    "ozon_view_assortment_quality": ("ozon_funnel", "ozon_stock", "ozon_assortment"),
    "ozon_view_rollups": ("ozon_funnel",),
    "ozon_view_product_ads": ("ozon_advertising",),
    "ozon_view_planfact": ("ozon_finance", "wb_finance"),
    "ozon_view_media": ("ozon_media",),
    "yandex_view_funnel": ("yandex_orders", "yandex_order_stats", "yandex_sales_funnel"),
    "yandex_view_orders": ("yandex_orders", "yandex_order_stats", "yandex_returns"),
    "yandex_view_finance": (
        "yandex_payments", "yandex_services", "yandex_boost_sales", "yandex_boost_shows",
        "yandex_banners", "yandex_shelves", "yandex_realization",
    ),
    "yandex_view_stocks": ("yandex_stocks",),
    "yandex_view_quality": (
        "yandex_orders", "yandex_order_stats", "yandex_returns", "yandex_sales_funnel",
        "yandex_payments", "yandex_services", "yandex_stocks",
    ),
}


def expected_date(mode: str, today: date | None = None) -> date:
    today = today or date.today()
    if mode == "snapshot":
        return today
    if mode == "closed_month":
        return today.replace(day=1) - timedelta(days=1)
    return today - timedelta(days=1)


def _value(row):
    if not row:
        return None
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def inspect_group(conn, key: str, today: date | None = None) -> dict:
    today = today or date.today()
    relations = VIEW_GROUPS[key][2]
    checks = []
    missing_relations = []
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout='15000ms'")
        for relation in relations:
            cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
            if not _value(cur.fetchone()):
                missing_relations.append(relation)
        cur.execute("SELECT to_regclass(%s)", (f"public.{VIEW_STATE_TABLE}",))
        ledger_present = bool(_value(cur.fetchone()))
        ledger_date = None
        refreshed_at = None
        if ledger_present:
            cur.execute(
                sql.SQL("SELECT coverage_date,refreshed_at FROM public.{} WHERE group_key=%s").format(
                    sql.Identifier(VIEW_STATE_TABLE)
                ),
                (key,),
            )
            row = cur.fetchone()
            if row:
                if isinstance(row, dict):
                    ledger_date, refreshed_at = row.get("coverage_date"), row.get("refreshed_at")
                else:
                    ledger_date, refreshed_at = row
        for relation, column, mode in PROBES.get(key, ()):
            target = expected_date(mode, today)
            if relation in missing_relations:
                maximum = None
            else:
                cur.execute(
                    sql.SQL("SELECT max({})::date FROM public.{}").format(
                        sql.Identifier(column), sql.Identifier(relation)
                    )
                )
                maximum = _as_date(_value(cur.fetchone()))
            checks.append({
                "relation": relation,
                "date_column": column,
                "expected_to": str(target),
                "actual_to": str(maximum) if maximum else None,
                "missing_days": max(0, (target - maximum).days) if maximum else None,
                "fresh": bool(maximum and maximum >= target),
            })
    target = max((expected_date(mode, today) for _, _, mode in PROBES.get(key, ())), default=today - timedelta(days=1))
    physical_fresh = bool(checks) and all(item["fresh"] for item in checks)
    ledger_fresh = bool(ledger_date and _as_date(ledger_date) >= target)
    return {
        "expected_to": str(target),
        "ledger_to": str(ledger_date) if ledger_date else None,
        "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "missing_relations": missing_relations,
        "checks": checks,
        "fresh": not missing_relations and (physical_fresh or (not checks and ledger_fresh)),
        "verified_by": "physical_dates" if physical_fresh else ("refresh_ledger" if not checks and ledger_fresh else None),
    }


def _dependency_state(tasks: list[dict], client: str, key: str) -> dict:
    wanted = {"api_" + item for item in DEPENDENCIES.get(key, ())}
    dependencies = [task for task in tasks if task.get("client") == client and task.get("key") in wanted]
    missing = sum(int(task.get("missing_days") or 0) for task in dependencies)
    limited = [task.get("row_label") or task.get("report") for task in dependencies if task.get("initial_status") == "limited"]
    missing_sources = [task.get("row_label") or task.get("report") for task in dependencies if int(task.get("missing_days") or 0) > 0]
    return {"missing_days": missing, "limited": limited, "missing_sources": missing_sources}


def extend_plan(app, plan):
    tasks = [deepcopy(task) for task in plan["tasks"]]
    for client in plan["clients"]:
        with psycopg2.connect(**app.read_db_config(client["key"])) as conn:
            for task in [item for item in tasks if item.get("client") == client["key"] and item.get("stage") == "views"]:
                key = str(task.get("key", "")).removeprefix("api_")
                dependencies = _dependency_state(tasks, client["key"], key)
                try:
                    state = inspect_group(conn, key)
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    task.update(
                        initial_status="queued",
                        initial_progress_text="Свежесть неизвестна",
                        cell_label="Свежесть неизвестна",
                        initial_detail="Проверка витрины недоступна; запуск повторит проверку и пересчёт.",
                        inspection_error=type(exc).__name__,
                        coverage_kind="view",
                    )
                    continue
                checks = state["checks"]
                known_lags = [item["missing_days"] for item in checks if item["missing_days"] is not None]
                lag = max(known_lags, default=None)
                if state["missing_relations"]:
                    status, label = "queued", "Нет витрины"
                elif state["fresh"] and not dependencies["missing_days"] and not dependencies["limited"]:
                    status, label = "ok", "Актуально"
                elif state["fresh"]:
                    status = "limited"
                    label = f"Источник: нет {dependencies['missing_days']} дн." if dependencies["missing_days"] else "Источник ограничен"
                else:
                    status = "queued"
                    label = f"Витрина: нет {lag} дн." if lag else "Пересчитать витрину"
                actual = ", ".join(
                    f"{item['relation']}: {item['actual_to'] or 'нет даты'}" for item in checks
                ) or "дата витрины фиксируется после первого пересчёта"
                source_note = "; источники с пропусками: " + ", ".join(dependencies["missing_sources"]) if dependencies["missing_sources"] else ""
                if dependencies["limited"]:
                    source_note += "; ограничения: " + ", ".join(dependencies["limited"])
                task.update(
                    initial_status=status,
                    initial_progress_text=label,
                    cell_label=label,
                    initial_detail=(
                        f"Ожидается по {state['expected_to']}; фактически {actual}{source_note}. "
                        "Пропуск источника не считается нулём."
                    ),
                    coverage_kind="view",
                    expected_to=state["expected_to"],
                    view_missing_days=lag,
                    source_missing_days=dependencies["missing_days"],
                    source_limited=dependencies["limited"],
                    view_checks=checks,
                    missing_relations=state["missing_relations"],
                    refreshed_at=state["refreshed_at"],
                    verified_by=state["verified_by"],
                )
    return app.admin_all_clients_row_major_plan(plan["clients"], tasks)
