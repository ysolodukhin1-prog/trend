#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, multi-client morning sales radar."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


BI_PROJECT_DEFAULT = Path(r"D:\Codex\New project\ozon_category_dashboard")
MOSCOW_TZ = "Europe/Moscow"
XLSX_BUILDER = Path(__file__).with_name("build_daily_client_radar_xlsx.mjs")
XLSX_NODE_FALLBACK = Path(
    r"C:\Users\Solod\.cache\codex-runtimes\codex-primary-runtime"
    r"\dependencies\node\bin\node.exe"
)
SEVERITY = {
    "not_applicable": -1,
    "ok": 0,
    "watch": 1,
    "signal": 2,
    "critical": 3,
    "blocked": 4,
}
ADVERSE = {"watch", "signal", "critical", "blocked"}
ICONS = {
    "ok": "🟢",
    "watch": "🟡",
    "signal": "🟠",
    "critical": "🔴",
    "blocked": "⛔",
    "not_applicable": "⚪",
}


@dataclass(frozen=True)
class SourceTask:
    kind: str
    client: str
    label: str
    dashboard: str
    marketplace: str
    start: date
    cutoff: date

    @property
    def key(self) -> str:
        return f"{self.client}:{self.dashboard}:{self.marketplace}"


# metric_id: numerator, denominator, scale, minimum-sample field, minimum value
FUNNEL_SPECS = {
    "ordered_revenue_rub": ("ordered_amount_rub", None, 1, None, None),
    "ordered_units": ("ordered_units", None, 1, "ordered_units", 100),
    "impressions": ("impressions_total", None, 1, "impressions_total", 10_000),
    "impression_to_card_pct": (
        "card_visits",
        "impressions_total",
        100,
        "impressions_total",
        10_000,
    ),
    "card_to_cart_pct": (
        "cart_adds",
        "card_visits",
        100,
        "card_visits",
        1_000,
    ),
    "cart_to_order_pct": (
        "ordered_units",
        "cart_adds",
        100,
        "cart_adds",
        500,
    ),
    "ad_ctr_pct": (
        "adv_clicks",
        "adv_impressions",
        100,
        "adv_impressions",
        10_000,
    ),
    "ad_cpc_rub": (
        "adv_expense_rub",
        "adv_clicks",
        1,
        "adv_clicks",
        100,
    ),
    "tacos_pct": ("adv_expense_rub", "ordered_amount_rub", 100, None, None),
}
ADV_SPECS = {
    "ordered_revenue_rub": ("total_orders_amount_rub", None, 1, None, None),
    "ordered_units": ("total_orders_qty", None, 1, "total_orders_qty", 100),
    "ad_ctr_pct": ("clicks", "impressions", 100, "impressions", 10_000),
    "ad_cpc_rub": ("expense_rub", "clicks", 1, "clicks", 100),
    "tacos_pct": ("expense_rub", "total_orders_amount_rub", 100, None, None),
}


def root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def worst(*statuses: str) -> str:
    return max(statuses or ("ok",), key=lambda item: SEVERITY.get(item, -1))


def load_registry(path: Path) -> tuple[dict[str, Mapping[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["metrics"]}, payload["version"]


def threshold_status(
    contract: Mapping[str, Any],
    comparison: str,
    value: float | None,
) -> str:
    if value is None:
        return "not_applicable"
    result = "ok"
    operators = {
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
    }
    for threshold in contract.get("thresholds") or []:
        if threshold.get("comparison") != comparison:
            continue
        operator = operators.get(str(threshold.get("operator")))
        limit = as_number(threshold.get("value"))
        if operator and limit is not None and operator(value, limit):
            result = worst(result, str(threshold["status"]))
    return result


def normalize_rows(
    raw_rows: Sequence[Mapping[str, Any]], start: date, cutoff: date
) -> tuple[dict[date, Mapping[str, Any]], list[str]]:
    rows: dict[date, Mapping[str, Any]] = {}
    issues: list[str] = []
    for raw in raw_rows:
        day = as_date(raw.get("report_date"))
        if day is None:
            issues.append("строка без report_date")
        elif start <= day <= cutoff:
            if day in rows:
                issues.append(f"дубликат агрегированной даты {day.isoformat()}")
            else:
                rows[day] = raw
    return rows, issues


def dates_between(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=index)
        for index in range((end - start).days + 1)
    ]


def stage_issues(
    rows: Mapping[date, Mapping[str, Any]], dashboard: str, days: Sequence[date]
) -> tuple[list[str], list[str]]:
    fields = (
        ("impressions_total", "card_visits", "cart_adds", "ordered_units")
        if dashboard == "funnel"
        else ("impressions", "clicks", "added_to_cart", "orders_qty")
    )
    missing: list[str] = []
    incoherent: list[str] = []
    for day in days:
        row = rows.get(day)
        if row is None:
            continue
        values = [as_number(row.get(field)) for field in fields]
        absent = [field for field, value in zip(fields, values) if value is None]
        if absent:
            missing.append(f"{day.isoformat()}: {', '.join(absent)}")
            continue
        numeric = [float(value) for value in values if value is not None]
        if any(value < 0 for value in numeric):
            incoherent.append(f"{day.isoformat()}: отрицательный этап")
        for left, right, left_name, right_name in zip(
            numeric, numeric[1:], fields, fields[1:]
        ):
            if left == 0 and right > 0:
                incoherent.append(
                    f"{day.isoformat()}: {right_name}>0 при {left_name}=0"
                )
            elif left > 0 and right > left:
                incoherent.append(
                    f"{day.isoformat()}: {right_name}>{left_name}"
                )
    return missing[:20], incoherent[:20]


def latest_complete_analysis_date(
    rows: Mapping[date, Mapping[str, Any]], dashboard: str
) -> date | None:
    """Select the latest business day and ignore ad-only FULL OUTER rows."""
    if not rows:
        return None
    if dashboard != "funnel":
        return max(rows)
    fields = (
        "impressions_total",
        "card_visits",
        "cart_adds",
        "ordered_units",
        "ordered_amount_rub",
    )
    for day in sorted(rows, reverse=True):
        values = [as_number(rows[day].get(field)) for field in fields]
        if any(value is not None and value > 0 for value in values):
            return day
    return None


def assess_dq(
    rows: Mapping[date, Mapping[str, Any]],
    row_issues: Sequence[str],
    dashboard: str,
    cutoff: date,
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    expected = dates_between(cutoff - timedelta(days=13), cutoff)
    if not rows:
        return {
            "status": "blocked",
            "latest_date": None,
            "freshness_days": None,
            "freshness_status": "blocked",
            "coverage_14_pct": 0.0,
            "coverage_status": "critical",
            "missing_dates": [day.isoformat() for day in expected],
            "missing_stage_fields": [],
            "incoherent_stages": [],
            "row_issues": list(row_issues),
        }
    latest = max(rows)
    freshness = (cutoff - latest).days
    freshness_status = threshold_status(
        registry["source_freshness_days"], "actual_value", float(freshness)
    )
    loaded = sum(day in rows for day in expected)
    coverage = loaded / 14 * 100
    coverage_status = threshold_status(
        registry["day_coverage_pct"], "actual_value", coverage
    )
    missing_stages, incoherent = stage_issues(rows, dashboard, expected)
    stage_status = (
        "critical" if missing_stages else ("signal" if incoherent else "ok")
    )
    return {
        "status": worst(
            freshness_status,
            coverage_status,
            stage_status,
            "signal" if row_issues else "ok",
        ),
        "latest_date": latest.isoformat(),
        "freshness_days": freshness,
        "freshness_status": freshness_status,
        "coverage_14_pct": round(coverage, 2),
        "coverage_status": coverage_status,
        "missing_dates": [
            day.isoformat() for day in expected if day not in rows
        ],
        "missing_stage_fields": missing_stages,
        "incoherent_stages": incoherent,
        "row_issues": list(row_issues),
    }


def aggregate(
    rows: Sequence[Mapping[str, Any]],
    spec: tuple[str, str | None, float, str | None, float | None],
) -> float | None:
    numerator, denominator, scale, _, _ = spec
    top = [as_number(row.get(numerator)) for row in rows]
    if not rows or any(value is None for value in top):
        return None
    top_sum = sum(value for value in top if value is not None)
    if denominator is None:
        return top_sum
    bottom = [as_number(row.get(denominator)) for row in rows]
    if any(value is None for value in bottom):
        return None
    bottom_sum = sum(value for value in bottom if value is not None)
    return top_sum / bottom_sum * scale if bottom_sum > 0 else None


def sample_ok(
    groups: Sequence[Sequence[Mapping[str, Any]]],
    spec: tuple[str, str | None, float, str | None, float | None],
) -> bool:
    field, minimum = spec[3], spec[4]
    if any(not group for group in groups):
        return False
    if field is None or minimum is None:
        return True
    for group in groups:
        values = [as_number(row.get(field)) for row in group]
        if any(value is None for value in values):
            return False
        if sum(value for value in values if value is not None) < minimum:
            return False
    return True


def dependency_blocked(
    row: Mapping[str, Any] | None,
    spec: tuple[str, str | None, float, str | None, float | None],
    metric_id: str,
) -> bool:
    if row is None:
        return True
    numerator = as_number(row.get(spec[0]))
    denominator = as_number(row.get(spec[1])) if spec[1] else None
    if numerator is None:
        return True
    if spec[1] and (denominator is None or denominator <= 0) and numerator > 0:
        return True
    if metric_id == "impressions":
        impressions = as_number(row.get("impressions_total"))
        downstream = as_number(row.get("card_visits"))
        return (
            impressions is None
            or (impressions <= 0 and downstream is not None and downstream > 0)
        )
    return False


def comparison(
    rows: Mapping[date, Mapping[str, Any]],
    cutoff: date,
    spec: tuple[str, str | None, float, str | None, float | None],
    metric_id: str,
) -> dict[str, Any]:
    current = rows.get(cutoff)
    weekday_days = [cutoff - timedelta(days=7 * index) for index in range(1, 9)]
    weekday_rows = [rows[day] for day in weekday_days if day in rows]
    weekday_values = [aggregate([row], spec) for row in weekday_rows]
    same_reference = (
        statistics.median(value for value in weekday_values if value is not None)
        if len(weekday_rows) == 8 and all(value is not None for value in weekday_values)
        else None
    )
    same_current = aggregate([current], spec) if current else None
    same_sample = (
        current is not None
        and len(weekday_rows) == 8
        and same_reference is not None
        and sample_ok([[current], *[[row] for row in weekday_rows]], spec)
    )
    current_days = dates_between(cutoff - timedelta(days=6), cutoff)
    previous_days = dates_between(
        cutoff - timedelta(days=13), cutoff - timedelta(days=7)
    )
    current_rows = [rows[day] for day in current_days if day in rows]
    previous_rows = [rows[day] for day in previous_days if day in rows]
    rolling_current = aggregate(current_rows, spec) if len(current_rows) == 7 else None
    rolling_reference = (
        aggregate(previous_rows, spec) if len(previous_rows) == 7 else None
    )
    rolling_sample = (
        rolling_current is not None
        and rolling_reference is not None
        and sample_ok([current_rows, previous_rows], spec)
    )

    def change(actual: float | None, reference: float | None) -> float | None:
        if actual is None or reference is None:
            return None
        if metric_id == "tacos_pct":
            return actual - reference
        return (
            (actual - reference) / abs(reference) * 100
            if reference != 0
            else None
        )

    return {
        "same_weekday": {
            "current": same_current,
            "reference": same_reference,
            "change": change(same_current, same_reference),
            "observations": len(weekday_rows),
            "sample_ok": same_sample,
        },
        "rolling_7d": {
            "current": rolling_current,
            "reference": rolling_reference,
            "change": change(rolling_current, rolling_reference),
            "current_days": len(current_rows),
            "reference_days": len(previous_rows),
            "sample_ok": rolling_sample,
        },
    }


def analyze_source(
    task: SourceTask,
    payload: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raw_rows = []
    rows, row_issues = normalize_rows(raw_rows, task.start, task.cutoff)
    dq = assess_dq(rows, row_issues, task.dashboard, task.cutoff, registry)
    analysis_date = latest_complete_analysis_date(rows, task.dashboard)
    analysis_freshness = (
        (task.cutoff - analysis_date).days if analysis_date else None
    )
    analysis_freshness_status = (
        threshold_status(
            registry["source_freshness_days"],
            "actual_value",
            float(analysis_freshness),
        )
        if analysis_freshness is not None
        else "blocked"
    )
    dq["analysis_date"] = analysis_date.isoformat() if analysis_date else None
    dq["analysis_freshness_days"] = analysis_freshness
    dq["analysis_freshness_status"] = analysis_freshness_status
    dq["status"] = worst(dq["status"], analysis_freshness_status)
    specs = FUNNEL_SPECS if task.dashboard == "funnel" else ADV_SPECS
    advertising_fields = (
        ("adv_impressions", "adv_clicks", "adv_expense_rub")
        if task.dashboard == "funnel"
        else ("impressions", "clicks", "expense_rub")
    )
    advertising_available = any(
        (as_number(row.get(field)) or 0) > 0
        for row in rows.values()
        for field in advertising_fields
    )
    metrics: list[dict[str, Any]] = []
    for metric_id, spec in specs.items():
        values = comparison(rows, analysis_date or task.cutoff, spec, metric_id)
        comparison_type = (
            "absolute_change_pp"
            if metric_id == "tacos_pct"
            else "relative_change_pct"
        )
        same_status = (
            threshold_status(
                registry[metric_id],
                comparison_type,
                values["same_weekday"]["change"],
            )
            if values["same_weekday"]["sample_ok"]
            else "not_applicable"
        )
        rolling_status = (
            threshold_status(
                registry[metric_id],
                comparison_type,
                values["rolling_7d"]["change"],
            )
            if values["rolling_7d"]["sample_ok"]
            else "not_applicable"
        )
        if SEVERITY[dq["status"]] >= SEVERITY["critical"]:
            status, basis = "blocked", "data_quality"
        elif SEVERITY[same_status] >= SEVERITY[rolling_status]:
            status, basis = same_status, "same_weekday"
        else:
            status, basis = rolling_status, "rolling_7d"
        if analysis_date is None:
            status = "blocked"
            basis = "no_complete_analysis_date"
        elif dependency_blocked(rows.get(analysis_date), spec, metric_id):
            status = "blocked"
            basis = "stage_dependency_incoherent"
        if (
            metric_id in {"ad_ctr_pct", "ad_cpc_rub", "tacos_pct"}
            and not advertising_available
        ):
            status, basis = "not_applicable", "advertising_layer_unavailable"
        metrics.append(
            {
                "metric_id": metric_id,
                "name": registry[metric_id]["name_ru"],
                "unit": registry[metric_id]["unit"],
                "status": status,
                "basis": basis,
                "minimum_sample": registry[metric_id]["minimum_sample"],
                **values,
            }
        )
    metric_map = {item["metric_id"]: item for item in metrics}
    seven_days = [
        rows[day]
        for day in dates_between(
            (analysis_date or task.cutoff) - timedelta(days=6),
            analysis_date or task.cutoff,
        )
        if day in rows
    ]
    complete = len(seven_days) == 7
    spend_field = "adv_expense_rub" if task.dashboard == "funnel" else "expense_rub"
    headline = {
        "revenue_7d_rub": metric_map["ordered_revenue_rub"]["rolling_7d"]["current"],
        "revenue_change_pct": metric_map["ordered_revenue_rub"]["rolling_7d"]["change"],
        "units_7d": metric_map["ordered_units"]["rolling_7d"]["current"],
        "ad_spend_7d_rub": (
            aggregate(seven_days, (spend_field, None, 1, None, None))
            if complete and advertising_available
            else None
        ),
        "ad_ctr_7d_pct": metric_map["ad_ctr_pct"]["rolling_7d"]["current"],
        "ad_cpc_7d_rub": metric_map["ad_cpc_rub"]["rolling_7d"]["current"],
        "tacos_7d_pct": metric_map["tacos_pct"]["rolling_7d"]["current"],
    }
    return {
        "source_key": task.key,
        "dashboard": task.dashboard,
        "marketplace": task.marketplace,
        "analysis_date": analysis_date.isoformat() if analysis_date else None,
        "rows_count": len(rows),
        "availability": {"advertising": advertising_available},
        "dq": dq,
        "headline": headline,
        "metrics": metrics,
    }


def safe_error(exc: BaseException) -> str:
    text = str(exc).splitlines()[0].strip() or exc.__class__.__name__
    return re.sub(
        r"(?i)(password|token|secret|authorization)\s*[=:]\s*\S+",
        r"\1=<скрыто>",
        text,
    )[:220]


def load_bi(project: Path) -> Any:
    if not (project / "app.py").exists():
        raise FileNotFoundError(f"Не найден BI app.py: {project}")
    sys.path.insert(0, str(project))
    app = importlib.import_module("app")
    if getattr(app, "_daily_radar_configured", False):
        return app
    if (project / "konstex_dashboard_server.py").exists():
        handler_names = (
            "handle_planfact_daily",
            "handle_planfact_summary",
            "handle_planfact_monthly",
            "handle_planfact_scorecard",
        )
        base_handlers = {
            name: getattr(app, name, None) for name in handler_names
        }
        extension = importlib.import_module("konstex_dashboard_server")
        konstex_handlers = {
            name: getattr(extension, name, None) for name in handler_names
        }
        for name in handler_names:
            base_handler = base_handlers[name]
            konstex_handler = konstex_handlers[name]
            if not callable(base_handler) or not callable(konstex_handler):
                continue

            def routed_handler(
                parsed: Any,
                _base: Any = base_handler,
                _konstex: Any = konstex_handler,
            ) -> Any:
                handler = (
                    _konstex
                    if app.current_client_key() == "konstex"
                    else _base
                )
                return handler(parsed)

            setattr(app, name, routed_handler)
    original = app.read_db_config

    def read_only_config(client: str | None = None) -> dict[str, Any]:
        config = dict(original(client))
        options = str(config.get("options") or "").strip()
        additions = []
        if "default_transaction_read_only" not in options:
            additions.append("-c default_transaction_read_only=on")
        if "statement_timeout" not in options:
            additions.append("-c statement_timeout=30000")
        config["options"] = " ".join([options, *additions]).strip()
        return config

    app.read_db_config = read_only_config
    app._daily_radar_configured = True
    return app


def build_plan(
    app: Any, cutoff: date
) -> tuple[list[SourceTask], dict[str, dict[str, Any]]]:
    clients: dict[str, dict[str, Any]] = {}
    tasks: list[SourceTask] = []
    for key, config in app.ADMIN_CLIENTS.items():
        if config.get("status") != "active" or not config.get("show_in_dashboard"):
            continue
        label = str(config.get("label") or key)
        reports = set(config.get("reports") or [])
        marketplaces = [
            item for item in config.get("marketplaces") or [] if item in {"wb", "ozon"}
        ]
        clients[key] = {
            "key": key,
            "label": label,
            "status": "ok",
            "sources": [],
            "planfact": {
                "status": "not_available",
                "reason": "План/факт не подключён.",
            },
        }
        dashboard = "funnel" if "funnel" in reports else ("adv" if "adv" in reports else "")
        for marketplace in marketplaces if dashboard else []:
            tasks.append(
                SourceTask(
                    "daily",
                    key,
                    label,
                    dashboard,
                    marketplace,
                    cutoff - timedelta(days=69),
                    cutoff,
                )
            )
        if "planfact" in reports and hasattr(app, "handle_planfact_scorecard"):
            tasks.append(
                SourceTask(
                    "planfact",
                    key,
                    label,
                    "planfact",
                    "total",
                    cutoff.replace(day=1),
                    cutoff,
                )
            )
    return tasks, clients


def query(app: Any, task: SourceTask) -> Mapping[str, Any]:
    handler = (
        app.handle_planfact_scorecard
        if task.kind == "planfact"
        else (
            app.handle_funnel_daily
            if task.dashboard == "funnel"
            else app.handle_adv_daily
        )
    )
    return app.review_source_payload(
        task.client,
        handler,
        task.dashboard,
        task.marketplace,
        {
            "date_from": task.start.isoformat(),
            "date_to": task.cutoff.isoformat(),
        },
    )


def parse_planfact(
    payload: Mapping[str, Any],
    cutoff: date,
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"status": "not_available", "reason": "Нет строк план/факта."}
    row = next(
        (item for item in rows if item.get("marketplace") == "total"),
        rows[0],
    )
    last_date = as_date(row.get("last_date"))
    lag = (cutoff - last_date).days if last_date else None
    sales_plan = as_number(row.get("sales_plan_rub"))
    ad_plan = as_number(row.get("ad_spend_plan_rub"))
    return {
        "status": (
            threshold_status(
                registry["source_freshness_days"], "actual_value", float(lag)
            )
            if lag is not None
            else "watch"
        ),
        "last_date": last_date.isoformat() if last_date else None,
        "freshness_days": lag,
        "sales_rub": as_number(row.get("sales_rub")),
        "sales_plan_rub": sales_plan if sales_plan and sales_plan > 0 else None,
        "sales_plan_fact_pct": (
            as_number(row.get("sales_plan_fact_pct"))
            if sales_plan and sales_plan > 0
            else None
        ),
        "sales_runrate_pct": (
            as_number(row.get("sales_runrate_pct"))
            if sales_plan and sales_plan > 0
            else None
        ),
        "ad_spend_rub": as_number(row.get("ad_spend_rub")),
        "ad_spend_plan_rub": ad_plan if ad_plan and ad_plan > 0 else None,
        "ad_spend_runrate_pct": (
            as_number(row.get("ad_spend_runrate_pct"))
            if ad_plan and ad_plan > 0
            else None
        ),
    }


def make_state_records(clients: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for client in clients:
        for source in client["sources"]:
            result.append(
                {
                    "key": f"{source['source_key']}:dq",
                    "client": client["key"],
                    "client_label": client["label"],
                    "marketplace": source["marketplace"],
                    "kind": "dq",
                    "title": "Качество данных",
                    "status": source["dq"]["status"],
                    "current": source["dq"]["freshness_days"],
                    "reference": 1,
                    "change": None,
                    "change_unit": "дн.",
                }
            )
            for metric in source["metrics"]:
                if metric["status"] in {"blocked", "not_applicable"}:
                    continue
                values = (
                    metric["same_weekday"]
                    if metric["basis"] == "same_weekday"
                    else metric["rolling_7d"]
                )
                result.append(
                    {
                        "key": f"{source['source_key']}:{metric['metric_id']}",
                        "client": client["key"],
                        "client_label": client["label"],
                        "marketplace": source["marketplace"],
                        "kind": "metric",
                        "title": metric["name"],
                        "status": metric["status"],
                        "current": values["current"],
                        "reference": values["reference"],
                        "change": values["change"],
                        "change_unit": (
                            "п.п." if metric["metric_id"] == "tacos_pct" else "%"
                        ),
                    }
                )
    return result


def add_transitions(
    current: Sequence[Mapping[str, Any]], previous: Mapping[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    before = {
        item["key"]: item
        for item in (previous or {}).get("state_records") or []
    }
    states: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for raw in current:
        item = dict(raw)
        prior = before.get(item["key"])
        prior_status = str((prior or {}).get("status") or "ok")
        now_bad = item["status"] in ADVERSE
        was_bad = prior_status in ADVERSE
        if now_bad and not was_bad:
            transition = "NEW"
        elif now_bad and was_bad:
            transition = (
                "WORSENED"
                if SEVERITY[item["status"]] > SEVERITY[prior_status]
                else "PERSISTS"
            )
        elif not now_bad and was_bad:
            transition = "RECOVERED"
        else:
            transition = None
        item["transition"] = transition
        item["previous_status"] = prior_status if prior else None
        states.append(item)
        if transition:
            findings.append(item)
    findings.sort(
        key=lambda item: (
            {
                "WORSENED": 4,
                "NEW": 3,
                "PERSISTS": 2,
                "RECOVERED": 1,
            }.get(str(item["transition"]), 0),
            SEVERITY[item["status"]],
        ),
        reverse=True,
    )
    return states, findings


def fmt(value: Any, digits: int = 0) -> str:
    parsed = as_number(value)
    return (
        f"{parsed:,.{digits}f}".replace(",", " ")
        if parsed is not None
        else "н/д"
    )


def render_md(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Утренний радар клиентов — {report['cutoff_date']}",
        "",
        (
            f"Статус данных: **{report['data_status']}** · клиентов "
            f"{len(report['clients'])} · ошибок запросов "
            f"{report['summary']['query_errors']}."
        ),
        "",
        "## Исключения",
        "",
    ]
    priority = [
        item
        for item in report["findings"]
        if item["transition"] in {"NEW", "WORSENED"}
        and not (item["kind"] == "metric" and item["status"] == "blocked")
    ]
    persists = [
        item
        for item in report["findings"]
        if item["transition"] == "PERSISTS"
        and not (item["kind"] == "metric" and item["status"] == "blocked")
    ]
    if not priority and not persists:
        lines.append("- Новых или сохраняющихся исключений нет.")

    def append_finding(item: Mapping[str, Any]) -> None:
        detail = (
            f"лаг {fmt(item['current'])} дн."
            if item["kind"] == "dq"
            else (
                f"{fmt(item['current'], 2)} против {fmt(item['reference'], 2)}, "
                f"{fmt(item['change'], 1)} {item['change_unit']}"
            )
        )
        lines.append(
            f"- {ICONS[item['status']]} **{item['client_label']} · "
            f"{item['marketplace'].upper()}** — {item['title']}: {detail} "
            f"· `{item['transition']}`"
        )

    for item in priority[:10]:
        append_finding(item)
    if persists:
        lines += ["", f"Сохраняются без ухудшения: **{len(persists)}**.", ""]
        for item in persists[:3]:
            append_finding(item)
        if len(persists) > 3:
            lines.append(
                f"- Ещё {len(persists) - 3} повторяющихся сигналов — в JSON."
            )
    recovered = [
        item for item in report["findings"] if item["transition"] == "RECOVERED"
    ]
    if recovered:
        lines += ["", "## Восстановилось", ""]
        for item in recovered[:8]:
            lines.append(
                f"- 🟢 **{item['client_label']} · "
                f"{item['marketplace'].upper()}** — {item['title']}."
            )
    lines += ["", "## Клиенты", ""]
    for client in report["clients"]:
        lines += [f"### {ICONS[client['status']]} {client['label']}", ""]
        if not client["sources"]:
            lines.append("- Основной источник продаж не подключён.")
        for source in client["sources"]:
            head = source["headline"]
            lines.append(
                f"- **{source['marketplace'].upper()}:** выручка 7д "
                f"{fmt(head['revenue_7d_rub'])} ₽ "
                f"({fmt(head['revenue_change_pct'], 1)}% к предыдущим 7д); "
                f"заказы {fmt(head['units_7d'])}; реклама "
                f"{fmt(head['ad_spend_7d_rub'])} ₽, CTR "
                f"{fmt(head['ad_ctr_7d_pct'], 2)}%, CPC "
                f"{fmt(head['ad_cpc_7d_rub'], 2)} ₽, TACoS "
                f"{fmt(head['tacos_7d_pct'], 2)}%; DQ "
                f"{source['dq']['status']}; источник до "
                f"{source['dq']['latest_date'] or 'н/д'}, анализ до "
                f"{source.get('analysis_date') or 'н/д'}."
            )
        pf = client["planfact"]
        if pf["status"] in {"not_available", "error"}:
            lines.append(f"- План/факт: {pf.get('reason') or 'н/д'}")
        else:
            lines.append(
                f"- План/факт MTD: {fmt(pf['sales_rub'])} ₽ / "
                f"{fmt(pf['sales_plan_rub'])} ₽; run-rate "
                f"{fmt(pf['sales_runrate_pct'], 1)}%; данные до "
                f"{pf['last_date'] or 'н/д'}."
            )
        lines.append("")
    lines += [
        "Пропуски не заменяются нулями. День сравнивается с медианой 8 "
        "предыдущих таких же дней недели; тренд — полные 7 дней против 7.",
        "",
    ]
    return "\n".join(lines)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def resolve_node_executable(explicit: str = "") -> Path | None:
    candidates = [
        explicit,
        os.environ.get("DAILY_RADAR_NODE_EXE", ""),
        shutil.which("node") or "",
        str(XLSX_NODE_FALLBACK),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def build_xlsx_artifact(
    json_path: Path,
    xlsx_path: Path,
    *,
    node_executable: str = "",
    builder_path: Path = XLSX_BUILDER,
) -> None:
    node = resolve_node_executable(node_executable)
    if node is None:
        raise RuntimeError("Node.js не найден; Excel-артефакт не сформирован")
    if not builder_path.is_file():
        raise RuntimeError(f"XLSX builder не найден: {builder_path}")
    completed = subprocess.run(
        [
            str(node),
            str(builder_path),
            "--input",
            str(json_path),
            "--output",
            str(xlsx_path),
        ],
        cwd=str(builder_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = safe_error(
            RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        )
        raise RuntimeError(f"XLSX builder завершился ошибкой: {detail}")
    if not xlsx_path.is_file() or xlsx_path.stat().st_size == 0:
        raise RuntimeError("XLSX builder не создал непустой файл")


def portfolio_data_status(
    clients: Sequence[Mapping[str, Any]], query_errors: int
) -> tuple[str, int]:
    sources = [
        source for client in clients for source in client.get("sources") or []
    ]

    def usable(source: Mapping[str, Any]) -> bool:
        headline = source.get("headline") or {}
        return any(
            as_number(headline.get(key)) is not None
            for key in (
                "revenue_7d_rub",
                "units_7d",
                "ad_spend_7d_rub",
            )
        )

    usable_count = sum(usable(source) for source in sources)
    if not sources or usable_count == 0:
        return "BLOCKED", usable_count
    degraded = (
        query_errors > 0
        or usable_count < len(sources)
        or any(
            source.get("dq", {}).get("status") != "ok"
            for source in sources
        )
    )
    return ("PARTIAL" if degraded else "COMPLETE"), usable_count


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    cutoff = (
        date.fromisoformat(args.cutoff)
        if args.cutoff
        else datetime.now(ZoneInfo(MOSCOW_TZ)).date() - timedelta(days=1)
    )
    registry, registry_version = load_registry(Path(args.registry))
    app = load_bi(Path(args.bi_project))
    tasks, client_map = build_plan(app, cutoff)
    output_dir = Path(args.output_dir)
    previous = None
    previous_path = output_dir / "latest.json"
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
    print(
        f"ПЛАН: клиентов {len(client_map)}, запросов {len(tasks)}, "
        f"окно 70 дней, потоков до {args.workers}; PostgreSQL read-only.",
        flush=True,
    )
    errors = 0
    completed = 0
    workers = max(1, min(args.workers, len(tasks) or 1, 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(query, app, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            completed += 1
            count = 0
            error = ""
            try:
                payload = future.result()
                if task.kind == "daily":
                    source = analyze_source(task, payload, registry)
                    client_map[task.client]["sources"].append(source)
                    count = source["rows_count"]
                else:
                    client_map[task.client]["planfact"] = parse_planfact(
                        payload, cutoff, registry
                    )
                    count = len(payload.get("rows") or [])
            except Exception as exc:
                errors += 1
                error = safe_error(exc)
                if task.kind == "daily":
                    client_map[task.client]["sources"].append(
                        {
                            "source_key": task.key,
                            "dashboard": task.dashboard,
                            "marketplace": task.marketplace,
                            "analysis_date": None,
                            "rows_count": 0,
                            "dq": {
                                "status": "blocked",
                                "latest_date": None,
                                "freshness_days": None,
                                "error": error,
                            },
                            "headline": {
                                key: None
                                for key in (
                                    "revenue_7d_rub",
                                    "revenue_change_pct",
                                    "units_7d",
                                    "ad_spend_7d_rub",
                                    "ad_ctr_7d_pct",
                                    "ad_cpc_7d_rub",
                                    "tacos_7d_pct",
                                )
                            },
                            "metrics": [],
                        }
                    )
                else:
                    client_map[task.client]["planfact"] = {
                        "status": "error",
                        "reason": f"Необязательный блок недоступен: {error}",
                    }
            elapsed = max(time.monotonic() - started, 0.001)
            eta = elapsed / completed * (len(tasks) - completed)
            print(
                f"ПРОГРЕСС: {completed}/{len(tasks)} "
                f"({completed / max(len(tasks), 1) * 100:.0f}%) | "
                f"{task.label}/{task.marketplace}/{task.dashboard} | "
                f"строк {count}, ошибок {errors} | прошло {elapsed:.1f}с, "
                f"ETA {eta:.1f}с",
                flush=True,
            )
    clients = sorted(client_map.values(), key=lambda item: item["label"].casefold())
    for client in clients:
        client["sources"].sort(key=lambda item: item["marketplace"])
        client["status"] = (
            worst(*(source["dq"]["status"] for source in client["sources"]))
            if client["sources"]
            else "not_applicable"
        )
    states, findings = add_transitions(make_state_records(clients), previous)
    data_status, usable_sources = portfolio_data_status(clients, errors)
    report = {
        "version": "1.0.0",
        "generated_at": datetime.now(ZoneInfo(MOSCOW_TZ)).isoformat(
            timespec="seconds"
        ),
        "timezone": MOSCOW_TZ,
        "cutoff_date": cutoff.isoformat(),
        "lookback_days": 70,
        "registry_version": registry_version,
        "data_status": data_status,
        "summary": {
            "clients": len(clients),
            "daily_sources": sum(len(client["sources"]) for client in clients),
            "usable_daily_sources": usable_sources,
            "query_errors": errors,
            "active_findings": sum(
                item["transition"] != "RECOVERED" for item in findings
            ),
            "recovered": sum(
                item["transition"] == "RECOVERED" for item in findings
            ),
        },
        "clients": clients,
        "state_records": states,
        "findings": findings,
    }
    json_path = output_dir / "latest.json"
    md_path = output_dir / "latest.md"
    xlsx_path = output_dir / "latest.xlsx"
    write_atomic(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_atomic(md_path, render_md(report))
    xlsx_error = ""
    try:
        xlsx_path.unlink(missing_ok=True)
        build_xlsx_artifact(json_path, xlsx_path)
    except Exception as exc:
        xlsx_error = safe_error(exc)
        print(f"XLSX: не сформирован: {xlsx_error}", flush=True)
    files = f"{json_path}, {md_path}"
    if xlsx_path.is_file():
        files += f", {xlsx_path}"
    else:
        files += ", XLSX недоступен"
    print(
        f"ИТОГ: клиентов {len(clients)}, источников "
        f"{report['summary']['daily_sources']}, ошибок {errors}, "
        f"сигналов {report['summary']['active_findings']}; файлы "
        f"{files}; время {time.monotonic() - started:.1f}с; "
        "завершено полностью.",
        flush=True,
    )
    return report


def parser() -> argparse.ArgumentParser:
    root = root_dir()
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bi-project", default=str(BI_PROJECT_DEFAULT))
    result.add_argument(
        "--registry",
        default=str(root / "config" / "wb-screening" / "metric_registry.v1.json"),
    )
    result.add_argument(
        "--output-dir",
        default=str(root / "artifacts" / "wb-screening" / "daily-client-radar"),
    )
    result.add_argument("--cutoff", default="")
    result.add_argument("--workers", type=int, default=4)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.workers < 1:
        raise SystemExit("--workers должен быть положительным")
    try:
        run(args)
        return 0
    except Exception as exc:
        print(f"ИТОГ: отчёт не сформирован: {safe_error(exc)}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


