#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the UI-ready plan/fact funnel matrix from a reviewed source snapshot.

The pure :func:`build_matrix_payload` function is deliberately independent from
the BI runtime.  :func:`build_live_matrix` is the thin read-only adapter used by
the dashboard endpoint; it reuses the existing screening collector and then
passes the resulting snapshot through the same pure transformation.
"""

from __future__ import annotations

import calendar
import json
import math
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from scripts.wb_screening.build_daily_client_radar import (
    MOSCOW_TZ,
    SourceTask,
    analyze_source,
    as_date,
    query,
    safe_error,
)
from scripts.wb_screening.collect_plan_fact_funnel import (
    client_label,
    current_plan,
    inventory_payload,
    json_value,
    merge_daily_rows,
    planfact_payload,
    shift_month,
)


MATRIX_VERSION = "1.3.0"
SEVERITY = {
    "not_applicable": -1,
    "no_data": 0,
    "no_plan": 0,
    "ok": 0,
    "limited": 1,
    "immature": 1,
    "watch": 1,
    "signal": 2,
    "critical": 3,
    "blocked": 4,
}
ADDITIVE_AGGREGATIONS = {"sum"}
RATIO_AGGREGATIONS = {"ratio", "ratio_complement", "ratio_1000"}
SUPPORTED_AGGREGATIONS = (
    ADDITIVE_AGGREGATIONS
    | RATIO_AGGREGATIONS
    | {"snapshot", "metadata", "none"}
)
GUIDE_FIELDS = (
    "display_name",
    "short_label",
    "short_description",
    "business_meaning",
    "calculation_logic",
    "interpretation",
    "source_requirement",
)
ORGANIC_LIMITED_IDS = {
    "paid_impression_share_pct",
    "organic_impressions",
    "organic_card_visits",
    "organic_cart_adds",
    "organic_orders",
    "organic_ctr_pct",
    "organic_card_to_cart_pct",
    "organic_cart_to_order_pct",
    "organic_impression_to_order_pct",
    "organic_card_to_order_pct",
    "organic_order_share_pct",
}
IMMATURE_IDS = {
    "bought_units_raw",
    "raw_buyout_rate_pct",
    "raw_cancellation_rate_pct",
}
EXACT_DAILY_PLAN_FIELDS = {
    "sales_plan_rub": "pf_sales_plan_daily_rub",
    "ad_spend_plan_rub": "pf_ad_spend_plan_daily_rub",
}
OZON_UNAVAILABLE_METRICS = {
    "profit_after_ads_rub": "Нет полной себестоимости и финансовых начислений Ozon.",
    "missed_profit_rub": "Нет подтверждённой маржинальной прибыли для оценки потери.",
    "stock_sufficiency_pct": "Нет подтверждённых поставок, safety stock и остатка плана.",
    "stock_cover_days": "Нет зрелого Ozon sales rate для расчёта покрытия.",
    "bought_units_raw": "Ozon funnel handler не предоставляет единицы выкупа.",
    "mature_buyout_rate_pct": "Нет equal-lag когорты Ozon заказ → выкуп.",
    "raw_buyout_rate_pct": "Нули bought_units в общем handler являются заглушкой Ozon.",
    "cancellation_rate_pct": "Нет зрелой когорты отмен Ozon.",
    "raw_cancellation_rate_pct": "Нули cancelled_units являются заглушкой Ozon.",
    "local_orders_pct": "Нет подтверждённого признака локального заказа Ozon.",
    "avg_delivery_time_days": "Нет сопоставимого источника сроков доставки Ozon.",
    "ad_cpm_bid_rub": "История CPM-ставок Ozon не подключена.",
    "price_index_pct": "Нет проверенной сопоставимой рыночной цены Ozon.",
    "review_rating": "История рейтинга Ozon не подключена к радару.",
    "duplicate_share_pct": "Дедупликационный показатель источника пока не рассчитан.",
    "outcome_maturity_coverage_pct": "Нет реестра зрелости исходов Ozon.",
}
OZON_STRICT_FUNNEL_METRICS = {
    "impressions",
    "card_visits",
    "cart_adds",
    "ordered_units",
    "average_order_value_rub",
    "impression_to_card_pct",
    "card_to_cart_pct",
    "cart_to_order_pct",
    "impression_to_order_pct",
    "card_to_order_pct",
    "paid_impression_share_pct",
    *ORGANIC_LIMITED_IDS,
}
OZON_PRIMARY_FUNNEL_FIELDS = {
    "impressions_total",
    "impressions_search_catalog",
    "card_visits",
    "cart_adds",
    "ordered_units",
    "ordered_amount_rub",
}
OZON_ORGANIC_FIELDS = {
    "organic_impressions",
    "organic_card_visits",
    "organic_cart_adds",
    "organic_orders",
}
OZON_AD_FIELDS = {
    "adv_impressions",
    "adv_clicks",
    "adv_cart_adds",
    "adv_orders",
    "adv_orders_amount_rub",
    "adv_expense_rub",
}
OZON_SYNTHETIC_ZERO_FIELDS = {
    "bought_units",
    "bought_amount_rub",
    "cancelled_units",
    "cancelled_amount_rub",
    "favorites_adds",
    "wb_club_ordered_units",
    "wb_club_bought_units",
    "wb_club_cancelled_units",
    "wb_club_ordered_amount_rub",
    "wb_club_bought_amount_rub",
    "wb_club_cancelled_amount_rub",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: Any) -> date | None:
    return as_date(value)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _calendar_days(start: date, end: date) -> Iterable[date]:
    if end < start:
        return
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def _rows_in_window(
    rows: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
) -> list[Mapping[str, Any]]:
    selected: list[tuple[date, Mapping[str, Any]]] = []
    for row in rows:
        day = _date(row.get("report_date"))
        if day is not None and start <= day <= end:
            selected.append((day, row))
    selected.sort(key=lambda item: item[0])
    return [row for _, row in selected]


def _nullable_sum(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [
        parsed
        for row in rows
        if (parsed := _number(row.get(field))) is not None
    ]
    return sum(values) if values else None


def aggregate_metric(
    metric: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    metadata_values: Mapping[str, Any] | None = None,
) -> float | None:
    """Aggregate one configured metric without replacing missing values by zero."""

    aggregation = str(metric.get("aggregation") or "")
    if aggregation not in SUPPORTED_AGGREGATIONS:
        raise ValueError(
            f"Unsupported aggregation {aggregation!r} for {metric.get('id')!r}"
        )
    if aggregation == "none":
        return None
    if aggregation == "metadata":
        return _number((metadata_values or {}).get(str(metric.get("metadata_key"))))
    if aggregation == "sum":
        return _nullable_sum(rows, str(metric.get("field") or ""))
    if aggregation == "snapshot":
        field = str(metric.get("field") or "")
        for row in reversed(rows):
            value = _number(row.get(field))
            if value is not None:
                return value
        return None

    numerator = _nullable_sum(rows, str(metric.get("numerator_field") or ""))
    denominator = _nullable_sum(rows, str(metric.get("denominator_field") or ""))
    if numerator is None or denominator in (None, 0):
        return None
    ratio = numerator / denominator
    if aggregation == "ratio_complement":
        return 1 - ratio
    if aggregation == "ratio_1000":
        return 1000 * ratio
    return ratio


def _registry_map(
    registry: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    if isinstance(registry, Mapping) and isinstance(registry.get("metrics"), list):
        return (
            {str(item["id"]): item for item in registry["metrics"]},
            str(registry.get("version")) if registry.get("version") else None,
        )
    if isinstance(registry, Mapping):
        contracts = {
            str(key): value
            for key, value in registry.items()
            if isinstance(value, Mapping)
        }
        return contracts, None
    return {str(item["id"]): item for item in registry}, None


def _validated_metric_guides(
    report_config: Mapping[str, Any],
    metric_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    raw = report_config.get("metric_guides")
    if not isinstance(raw, Mapping):
        raise ValueError("Report config must include loaded metric_guides")
    expected = set(metric_ids)
    actual = {str(key) for key in raw}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValueError(
            "Metric guide coverage mismatch: "
            f"missing={missing or 'none'}, extra={extra or 'none'}"
        )
    result: dict[str, dict[str, str]] = {}
    for metric_id in metric_ids:
        source = raw.get(metric_id)
        if not isinstance(source, Mapping):
            raise ValueError(f"Metric guide {metric_id!r} must be an object")
        guide: dict[str, str] = {}
        for field in GUIDE_FIELDS:
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Metric guide {metric_id!r} has empty {field!r}"
                )
            guide[field] = value.strip()
        result[metric_id] = guide
    return result


def _formula(metric: Mapping[str, Any]) -> str:
    aggregation = str(metric.get("aggregation"))
    if aggregation in {"sum", "snapshot"}:
        return f"{aggregation}({metric.get('field')})"
    if aggregation == "ratio":
        return (
            f"sum({metric.get('numerator_field')}) / "
            f"sum({metric.get('denominator_field')})"
        )
    if aggregation == "ratio_complement":
        return (
            f"1 - sum({metric.get('numerator_field')}) / "
            f"sum({metric.get('denominator_field')})"
        )
    if aggregation == "ratio_1000":
        return (
            f"1000 × sum({metric.get('numerator_field')}) / "
            f"sum({metric.get('denominator_field')})"
        )
    if aggregation == "metadata":
        return str(metric.get("metadata_key"))
    return "Недоступно по текущему набору источников."


def _metric_fields(metric: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("field", "numerator_field", "denominator_field"):
        value = metric.get(key)
        if value:
            fields.append(str(value))
    if metric.get("metadata_key"):
        fields.append(str(metric["metadata_key"]))
    return fields


def _sanitize_marketplace_rows(
    marketplace: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove known marketplace placeholders before any metric calculation."""

    normalized_marketplace = marketplace.strip().lower()
    sanitized: list[dict[str, Any]] = []
    funnel_missing_dates: list[str] = []
    funnel_gap_details: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        if normalized_marketplace == "ozon":
            for field in OZON_SYNTHETIC_ZERO_FIELDS:
                row.pop(field, None)
            primary = [_number(source.get(field)) for field in OZON_PRIMARY_FUNNEL_FIELDS]
            has_primary_fields = any(field in source for field in OZON_PRIMARY_FUNNEL_FIELDS)
            companion_positive = any(
                (_number(source.get(field)) or 0) > 0
                for field in OZON_AD_FIELDS | {"pf_orders_rub"}
            )
            ad_only_placeholder = companion_positive and not any(
                value is not None and value > 0 for value in primary
            )
            if not has_primary_fields or ad_only_placeholder:
                day = _date(source.get("report_date"))
                if day is not None:
                    funnel_missing_dates.append(day.isoformat())
                    funnel_gap_details.append(
                        {
                            "date": day.isoformat(),
                            "reason_code": (
                                "primary_funnel_placeholder"
                                if ad_only_placeholder
                                else "primary_funnel_missing"
                            ),
                            "unusable_fields": sorted(OZON_PRIMARY_FUNNEL_FIELDS),
                            "companion_ad_data_available": companion_positive,
                        }
                    )
                for field in OZON_PRIMARY_FUNNEL_FIELDS | OZON_ORGANIC_FIELDS:
                    row.pop(field, None)
        sanitized.append(row)
    return sanitized, {
        "profile": normalized_marketplace,
        "funnel_missing_dates": sorted(set(funnel_missing_dates)),
        "funnel_gap_details": sorted(
            funnel_gap_details,
            key=lambda item: str(item.get("date") or ""),
        ),
    }


def _strict_window_complete(
    metric: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
) -> bool:
    fields = [
        field
        for field in _metric_fields(metric)
        if field != str(metric.get("metadata_key") or "")
    ]
    if not fields:
        return True
    by_day = {
        day: row
        for row in rows
        if (day := _date(row.get("report_date"))) is not None
    }
    for day in _calendar_days(start, end):
        row = by_day.get(day)
        if row is None or any(_number(row.get(field)) is None for field in fields):
            return False
    return True


def _aggregate_window(
    metric: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
    metadata_values: Mapping[str, Any],
    strict: bool,
    allow_partial: bool = False,
) -> tuple[float | None, bool | None]:
    selected = _rows_in_window(rows, start, end)
    complete = (
        _strict_window_complete(metric, selected, start, end)
        if strict
        else None
    )
    if complete is False:
        if not allow_partial:
            return None, False
        fields = [
            field
            for field in _metric_fields(metric)
            if field != str(metric.get("metadata_key") or "")
        ]
        complete_rows = [
            row
            for row in selected
            if all(_number(row.get(field)) is not None for field in fields)
        ]
        return aggregate_metric(metric, complete_rows, metadata_values), False
    return aggregate_metric(metric, selected, metadata_values), complete


def _marketplace_checks(
    marketplace: str,
    rows: Sequence[Mapping[str, Any]],
    analysis_date: date,
    base: Mapping[str, Any],
) -> dict[str, Any]:
    if marketplace != "ozon":
        return dict(base)
    expected = list(_calendar_days(analysis_date - timedelta(days=13), analysis_date))
    by_day = {
        day: row
        for row in rows
        if (day := _date(row.get("report_date"))) is not None
    }
    complete_days = [
        day
        for day in expected
        if day in by_day
        and all(
            _number(by_day[day].get(field)) is not None
            for field in OZON_PRIMARY_FUNNEL_FIELDS
        )
    ]
    coverage = len(complete_days) / len(expected) if expected else 0
    if coverage < 0.9:
        status = "critical"
    elif coverage < 0.95:
        status = "signal"
    elif coverage < 1:
        status = "watch"
    else:
        status = "ok"
    return {
        **dict(base),
        "funnel_complete_14_days": len(complete_days),
        "funnel_expected_14_days": len(expected),
        "funnel_coverage_14_fraction": round(coverage, 4),
        "funnel_coverage_status": status,
    }


def _source_contract(
    marketplace: str,
    metric: Mapping[str, Any],
) -> dict[str, Any]:
    fields = _metric_fields(metric)
    if marketplace == "ozon":
        objects: list[str] = []
        field_mapping: dict[str, str] = {}
        adv_aliases = {
            "adv_impressions": "impressions",
            "adv_clicks": "clicks",
            "adv_cart_adds": "added_to_cart",
            "adv_orders": "orders_qty",
            "adv_orders_amount_rub": "orders_amount_rub",
            "adv_expense_rub": "expense_rub",
        }
        for field in fields:
            if field.startswith("pf_"):
                obj = "mv_planfact_daily"
                canonical = field[3:]
            elif field.startswith("inventory_"):
                obj = "inventory_history_daily"
                canonical = field.removeprefix("inventory_")
            elif field in OZON_ORGANIC_FIELDS:
                dependencies = [
                    "mv_ozon_funnel_daily_by_article_category",
                    "mv_ozon_adv_daily_by_article_category",
                ]
                for dependency in dependencies:
                    if dependency not in objects:
                        objects.append(dependency)
                field_mapping[field] = (
                    "greatest(ozon_funnel_total - ozon_attributed_adv, 0)"
                )
                continue
            elif field in OZON_AD_FIELDS:
                obj = "mv_ozon_adv_daily_by_article_category"
                canonical = adv_aliases.get(field, field)
            elif metric.get("aggregation") == "metadata":
                obj = "screening_data_quality"
                canonical = field
            else:
                obj = "mv_ozon_funnel_daily_by_article_category"
                canonical = field
            if obj not in objects:
                objects.append(obj)
            field_mapping[field] = f"{obj}.{canonical}"
        return {
            "objects": objects,
            "field_mapping": field_mapping,
            "formula": _formula(metric),
            "provenance_status": "verified_read_only",
            "marketplace_profile": "ozon_v1",
        }

    return {
        "objects": [
            (
                "mv_planfact_daily"
                if any(field.startswith("pf_") for field in fields)
                else (
                    "inventory_history_daily"
                    if any(field.startswith("inventory_") for field in fields)
                    else "mv_wb_funnel_daily_by_article_category"
                )
            )
        ] if fields else [],
        "field_mapping": {field: field for field in fields},
        "formula": _formula(metric),
        "provenance_status": "verified_read_only",
        "marketplace_profile": "wb_v1",
    }


def _availability(
    marketplace: str,
    metric_id: str,
    fact: float | None,
    strict_complete: bool | None,
) -> dict[str, Any]:
    if marketplace == "ozon" and metric_id in OZON_UNAVAILABLE_METRICS:
        return {
            "status": "unavailable",
            "supported_by_schema": False,
            "reason": OZON_UNAVAILABLE_METRICS[metric_id],
        }
    if strict_complete is False:
        return {
            "status": "dq_partial",
            "supported_by_schema": True,
            "reason": "Показан частичный факт только по дням с полным Ozon funnel.",
        }
    if metric_id in ORGANIC_LIMITED_IDS:
        return {
            "status": "limited",
            "supported_by_schema": True,
            "reason": "Органика получена вычитанием атрибутированной рекламы.",
        }
    return {
        "status": "available" if fact is not None else "no_current_value",
        "supported_by_schema": True,
        "reason": None if fact is not None else "Нет значения на согласованном cutoff.",
    }

def _normalize_exact_plan(metric: Mapping[str, Any], value: Any) -> float | None:
    result = _number(value)
    if result is None:
        return None
    # Client plan feeds expose percentage fields in percentage points (6.6%),
    # while every matrix calculation stores percentages as fractions (0.066).
    if metric.get("unit") == "percent":
        return result / 100
    return result


def _plan_for_metric(
    metric: Mapping[str, Any],
    current_plan: Mapping[str, Any],
    previous_values: Sequence[Mapping[str, Any]],
) -> tuple[float | None, dict[str, Any]]:
    client_field = metric.get("client_plan_field")
    if client_field:
        exact = _normalize_exact_plan(metric, current_plan.get(str(client_field)))
        if exact is not None:
            return exact, {
                "type": "exact_client",
                "provenance": f"current_plan.{client_field}",
                "provisional": False,
            }

    history = [
        value
        for item in previous_values
        if (value := _number(item.get("value"))) is not None
    ]
    if history:
        return float(statistics.median(history)), {
            "type": "history_median",
            "provenance": "median(previous_3_month_facts)",
            "provisional": True,
            "sample_months": len(history),
        }

    default = _number(metric.get("default_plan"))
    if default is not None:
        return default, {
            "type": str(metric.get("default_plan_type") or "guardrail"),
            "provenance": "report_config.default_plan",
            "provisional": True,
        }
    return None, {
        "type": "unavailable",
        "provenance": None,
        "provisional": True,
    }


def _plan_mtd(
    metric: Mapping[str, Any],
    monthly_plan: float | None,
    plan_info: Mapping[str, Any],
    current_rows: Sequence[Mapping[str, Any]],
    elapsed_days: int,
    days_in_month: int,
) -> float | None:
    if monthly_plan is None:
        return None
    if metric.get("aggregation") != "sum":
        return monthly_plan
    if plan_info.get("type") == "exact_client":
        daily_field = EXACT_DAILY_PLAN_FIELDS.get(
            str(metric.get("client_plan_field") or "")
        )
        if daily_field:
            exact_elapsed = _nullable_sum(current_rows, daily_field)
            if exact_elapsed is not None:
                return exact_elapsed
    if elapsed_days <= 0 or days_in_month <= 0:
        return None
    return monthly_plan * elapsed_days / days_in_month


def _run_rate(
    metric: Mapping[str, Any],
    fact_mtd: float | None,
    elapsed_days: int,
    days_in_month: int,
) -> float | None:
    if fact_mtd is None or elapsed_days <= 0:
        return None
    aggregation = str(metric.get("aggregation"))
    if aggregation == "sum":
        return fact_mtd * days_in_month / elapsed_days
    if aggregation in RATIO_AGGREGATIONS:
        return fact_mtd
    return None


def _ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return left / right


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _comparison_values(
    metric: Mapping[str, Any],
    fact: float,
    plan: float | None,
) -> dict[str, float | None]:
    actual = fact * 100 if metric.get("unit") == "percent" else fact
    relative = (
        (fact - plan) / abs(plan) * 100
        if plan not in (None, 0)
        else None
    )
    absolute_pp = (
        (fact - plan) * 100
        if metric.get("unit") == "percent" and plan is not None
        else (fact - plan if plan is not None else None)
    )
    ratio_to_plan = fact / plan if plan not in (None, 0) else None
    return {
        "actual_value": actual,
        "relative_change_pct": relative,
        "absolute_change_pp": absolute_pp,
        "ratio_to_target": ratio_to_plan,
        "ratio_to_margin": ratio_to_plan,
        "delta_days": fact - plan if plan is not None else None,
    }


def _threshold_matches(
    threshold: Mapping[str, Any],
    value: float,
) -> bool:
    operator = str(threshold.get("operator") or "")
    lower = _number(threshold.get("value"))
    if lower is None:
        return False
    if operator == "outside_range":
        upper = _number(threshold.get("upper_value"))
        return upper is not None and (value < lower or value > upper)
    operations = {
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
    }
    comparison = operations.get(operator)
    return bool(comparison and comparison(value, lower))


def _status_result(
    code: str,
    basis: str,
    explanation: str,
    *,
    dq_status: str,
    matched_threshold: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": SEVERITY.get(code, 0),
        "basis": basis,
        "explanation": explanation,
        "dq_status": dq_status,
        "matched_threshold": dict(matched_threshold) if matched_threshold else None,
    }


def _business_status(
    metric: Mapping[str, Any],
    fact: float | None,
    plan: float | None,
    registry_contract: Mapping[str, Any] | None,
    dq_status: str,
) -> dict[str, Any]:
    metric_id = str(metric.get("id") or "")
    if fact is None:
        return _status_result(
            "no_data",
            "availability",
            "Для метрики нет подтверждённого значения; пропуск не заменён нулём.",
            dq_status=dq_status,
        )
    if metric_id in IMMATURE_IDS:
        return _status_result(
            "immature",
            "maturity",
            "Сырой post-order показатель не является equal-lag зрелой когортой.",
            dq_status=dq_status,
        )
    if metric_id in ORGANIC_LIMITED_IDS:
        return _status_result(
            "limited",
            "attribution",
            "Органика рассчитана вычитанием рекламной атрибуции и имеет ограничение.",
            dq_status=dq_status,
        )

    comparisons = _comparison_values(metric, fact, plan)
    thresholds = list((registry_contract or {}).get("thresholds") or [])
    applicable = False
    matched: list[Mapping[str, Any]] = []
    for threshold in thresholds:
        comparison_name = str(threshold.get("comparison") or "")
        value = comparisons.get(comparison_name)
        if value is None:
            continue
        applicable = True
        if _threshold_matches(threshold, value):
            matched.append(threshold)
    if applicable:
        if not matched:
            return _status_result(
                "ok",
                "registry_threshold",
                "Сигнальные пороги реестра не пробиты.",
                dq_status=dq_status,
            )
        worst = max(
            matched,
            key=lambda item: SEVERITY.get(str(item.get("status")), -1),
        )
        return _status_result(
            str(worst.get("status") or "watch"),
            "registry_threshold",
            str(worst.get("rationale") or "Пробит порог реестра."),
            dq_status=dq_status,
            matched_threshold=worst,
        )

    if plan is None:
        return _status_result(
            "no_plan",
            "reference",
            "Нет клиентского плана, исторической базы или guardrail.",
            dq_status=dq_status,
        )

    direction = str(metric.get("direction") or "")
    if direction == "higher_is_better":
        ratio = _ratio(fact, plan)
        if ratio is None:
            code = "ok" if fact >= plan else "signal"
        elif ratio < 0.8:
            code = "critical"
        elif ratio < 0.9:
            code = "signal"
        elif ratio < 1:
            code = "watch"
        else:
            code = "ok"
    elif direction == "lower_is_better":
        if plan == 0:
            code = "ok" if fact <= 0 else "signal"
        else:
            ratio = fact / plan
            if ratio > 1.35:
                code = "critical"
            elif ratio > 1.2:
                code = "signal"
            elif ratio > 1:
                code = "watch"
            else:
                code = "ok"
    elif direction == "target_range":
        if plan == 0:
            code = "ok" if fact == 0 else "signal"
        else:
            deviation = abs(fact - plan) / abs(plan)
            if deviation > 0.35:
                code = "critical"
            elif deviation > 0.2:
                code = "signal"
            elif deviation > 0.1:
                code = "watch"
            else:
                code = "ok"
    else:
        code = "not_applicable"
    return _status_result(
        code,
        "directional_fallback",
        "Статус рассчитан по направлению метрики относительно выбранного плана.",
        dq_status=dq_status,
    )


def _status_with_dq_override(
    metric: Mapping[str, Any],
    fact: float | None,
    plan: float | None,
    registry_contract: Mapping[str, Any] | None,
    dq_status: str,
) -> dict[str, Any]:
    business = _business_status(
        metric,
        fact,
        plan,
        registry_contract,
        dq_status,
    )
    if dq_status not in {"critical", "blocked"}:
        return business
    return {
        **_status_result(
            dq_status,
            "data_quality_override",
            (
                "Критическое качество данных блокирует подтверждённую "
                "бизнес-интерпретацию."
            ),
            dq_status=dq_status,
        ),
        "business_status": business["code"],
    }


def _trend(
    metric: Mapping[str, Any],
    rows_by_day: Mapping[date, Mapping[str, Any]],
    analysis_date: date,
    metadata_values: Mapping[str, Any],
    trend_days: int,
    *,
    include_metadata: bool = True,
) -> list[dict[str, Any]]:
    start = analysis_date - timedelta(days=trend_days - 1)
    result: list[dict[str, Any]] = []
    for day in _calendar_days(start, analysis_date):
        raw = rows_by_day.get(day)
        if metric.get("aggregation") == "metadata":
            value = (
                aggregate_metric(metric, [], metadata_values)
                if include_metadata and day == analysis_date
                else None
            )
        else:
            value = aggregate_metric(metric, [raw], {}) if raw else None
        result.append({"date": day.isoformat(), "value": value})
    return result


GAP_FIELD_LABELS_RU = {
    "impressions_total": "показы товаров",
    "impressions_search_catalog": "показы в поиске и каталоге",
    "card_visits": "переходы в карточку товара",
    "cart_adds": "добавления в корзину",
    "ordered_units": "заказанные единицы",
    "ordered_amount_rub": "сумма оформленных заказов",
}


def _metric_gap_contract(
    metric: Mapping[str, Any],
    marketplace: str,
    current: Sequence[Mapping[str, Any]],
    previous: Sequence[Mapping[str, Any]],
    marketplace_checks: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe missing chart points without turning them into business zeroes."""

    current_missing = [
        str(point.get("date"))
        for point in current
        if point.get("date") and _number(point.get("value")) is None
    ]
    previous_missing = [
        str(point.get("date"))
        for point in previous
        if point.get("date") and _number(point.get("value")) is None
    ]
    details_by_date = {
        str(item.get("date")): dict(item)
        for item in marketplace_checks.get("funnel_gap_details") or []
        if item.get("date")
    }
    affected = [
        details_by_date[day]
        for day in current_missing
        if day in details_by_date
    ]
    reason_code = "no_gap"
    title = "Дневной ряд полный"
    explanation = "В текущем 28-дневном окне пропусков нет."
    unusable_fields: list[str] = []
    if current_missing:
        reason_code = "metric_value_missing"
        title = "Есть пропуски в дневном ряду"
        explanation = (
            "Отдельные дневные значения отсутствуют в источнике и не заменены нулём."
        )
    if affected and marketplace == "ozon":
        reason_codes = {str(item.get("reason_code") or "") for item in affected}
        reason_code = (
            "primary_funnel_placeholder"
            if "primary_funnel_placeholder" in reason_codes
            else "primary_funnel_missing"
        )
        title = "Неполная общая воронка Ozon"
        explanation = (
            "Ozon не передал надёжные значения общей воронки, хотя рекламный "
            "контур за эти даты присутствует. Нули-заглушки исключены из расчёта "
            "и показаны как пропуски."
        )
        unusable_fields = sorted(
            {
                str(field)
                for item in affected
                for field in item.get("unusable_fields") or []
            }
        )
    current_count = len(current)
    previous_count = len(previous)
    return {
        "has_gap": bool(current_missing or previous_missing),
        "reason_code": reason_code,
        "title": title,
        "explanation": explanation,
        "current": {
            "expected_days": current_count,
            "available_days": current_count - len(current_missing),
            "coverage_pct": (
                round((current_count - len(current_missing)) / current_count * 100, 1)
                if current_count
                else None
            ),
            "missing_dates": current_missing,
        },
        "previous": {
            "expected_days": previous_count,
            "available_days": previous_count - len(previous_missing),
            "coverage_pct": (
                round((previous_count - len(previous_missing)) / previous_count * 100, 1)
                if previous_count
                else None
            ),
            "missing_dates": previous_missing,
        },
        "unusable_fields": [
            {"field": field, "label": GAP_FIELD_LABELS_RU.get(field, field)}
            for field in unusable_fields
        ],
        "comparison_reliable": not current_missing and not previous_missing,
        "impact": (
            "Сравнение 7 к 7 дням и прогноз имеют пониженную надёжность."
            if current_missing or previous_missing
            else "Разрывов, влияющих на сравнение периодов, нет."
        ),
    }


def _weekly_ytd(
    metric: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    analysis_date: date,
    metadata_values: Mapping[str, Any],
    strict: bool,
    *,
    unavailable: bool = False,
) -> list[dict[str, Any]]:
    """Return calendar-week facts from 1 January through the cutoff."""

    cursor = date(analysis_date.year, 1, 1)
    result: list[dict[str, Any]] = []
    while cursor <= analysis_date:
        week_end = min(cursor + timedelta(days=6 - cursor.weekday()), analysis_date)
        value, complete = _aggregate_window(
            metric,
            rows,
            cursor,
            week_end,
            metadata_values if week_end == analysis_date else {},
            strict,
            allow_partial=True,
        )
        if unavailable:
            value = None
            complete = None
        result.append(
            {
                "week_start": cursor.isoformat(),
                "week_end": week_end.isoformat(),
                "value": value,
                "days": (week_end - cursor).days + 1,
                "is_partial": cursor.weekday() != 0 or week_end.weekday() != 6,
                "complete": complete,
            }
        )
        cursor = week_end + timedelta(days=1)
    return result


def _forecast_unavailable(
    reason: str,
    *,
    history_days: int = 0,
    history_months: int = 0,
    requested_months: int = 12,
) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "method": "trend_x_month_week_x_weekday",
        "requested_history_months": requested_months,
        "history_days_used": history_days,
        "history_months_used": history_months,
        "limited_history": history_months < requested_months,
        "confidence": "unavailable",
        "warning": reason,
        "coefficients": {"week_of_month": {}, "weekday": {}},
        "daily_7d": [],
        "weekly_4w": [],
    }


def _normalized_median_factors(
    grouped: Mapping[int, Sequence[float]],
) -> dict[int, float]:
    raw = {
        key: float(statistics.median(values))
        for key, values in grouped.items()
        if values
    }
    if not raw:
        return {}
    normalizer = statistics.fmean(raw.values()) or 1.0
    return {
        key: min(4.0, max(0.25, value / normalizer))
        for key, value in raw.items()
    }


def _linear_trend(
    points: Sequence[tuple[date, float]],
) -> tuple[date, float, float]:
    recent = list(points[-28:])
    origin = recent[0][0]
    xs = [float((day - origin).days) for day, _ in recent]
    ys = [float(value) for _, value in recent]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    slope = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        / denominator
        if denominator
        else 0.0
    )
    scale = max(abs(statistics.median(ys)), abs(ys[-1]), 1e-9)
    slope = min(scale * 0.2, max(-scale * 0.2, slope))
    intercept = y_mean - slope * x_mean
    return origin, intercept, slope


def _metric_forecast(
    metric: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    analysis_date: date,
    metadata_values: Mapping[str, Any],
    dq_status: str,
    *,
    unavailable: bool = False,
    requested_months: int = 12,
) -> dict[str, Any]:
    """Forecast seven days and four full calendar weeks from observed history."""

    aggregation = str(metric.get("aggregation") or "")
    if unavailable or aggregation in {"metadata", "none"}:
        return _forecast_unavailable(
            "Для этой метрики нет сопоставимого ежедневного ряда.",
            requested_months=requested_months,
        )
    month_start = date(analysis_date.year, analysis_date.month, 1)
    window_start = shift_month(month_start, -(max(1, requested_months) - 1))
    observations: list[tuple[date, float]] = []
    for row in rows:
        day = _date(row.get("report_date"))
        if day is None or day < window_start or day > analysis_date:
            continue
        value = aggregate_metric(metric, [row], metadata_values)
        if value is None:
            continue
        numeric = float(value)
        if math.isfinite(numeric):
            observations.append((day, numeric))
    observations.sort(key=lambda item: item[0])
    months = sorted({(day.year, day.month) for day, _ in observations})
    if len(observations) < 7:
        return _forecast_unavailable(
            "Нужно минимум 7 валидных дневных значений.",
            history_days=len(observations),
            history_months=len(months),
            requested_months=requested_months,
        )

    month_values: dict[tuple[int, int], list[float]] = {}
    for day, value in observations:
        month_values.setdefault((day.year, day.month), []).append(value)
    month_levels = {
        key: statistics.fmean(values)
        for key, values in month_values.items()
        if values and abs(statistics.fmean(values)) > 1e-12
    }
    week_samples: dict[int, list[float]] = {}
    ratios: list[tuple[date, float]] = []
    for day, value in observations:
        level = month_levels.get((day.year, day.month))
        if level is None:
            continue
        ratio = value / level
        week_number = min(5, (day.day - 1) // 7 + 1)
        ratios.append((day, ratio))
        week_samples.setdefault(week_number, []).append(ratio)
    week_factors = _normalized_median_factors(week_samples)
    weekday_samples: dict[int, list[float]] = {}
    for day, ratio in ratios:
        week_number = min(5, (day.day - 1) // 7 + 1)
        week_factor = week_factors.get(week_number, 1.0)
        weekday_samples.setdefault(day.weekday() + 1, []).append(
            ratio / week_factor
        )
    weekday_factors = _normalized_median_factors(weekday_samples)

    deseasonalized = []
    for day, value in observations:
        week_number = min(5, (day.day - 1) // 7 + 1)
        seasonal = week_factors.get(week_number, 1.0) * weekday_factors.get(
            day.weekday() + 1, 1.0
        )
        deseasonalized.append((day, value / seasonal if seasonal else value))
    origin, intercept, slope = _linear_trend(deseasonalized)

    def future_value(day: date) -> float:
        week_number = min(5, (day.day - 1) // 7 + 1)
        seasonal = week_factors.get(week_number, 1.0) * weekday_factors.get(
            day.weekday() + 1, 1.0
        )
        trend_value = intercept + slope * (day - origin).days
        result = max(0.0, trend_value * seasonal)
        if str(metric.get("unit") or "") == "score_5":
            result = min(5.0, result)
        return float(result)

    daily = []
    for offset in range(1, 8):
        day = analysis_date + timedelta(days=offset)
        week_number = min(5, (day.day - 1) // 7 + 1)
        daily.append(
            {
                "date": day.isoformat(),
                "value": future_value(day),
                "week_of_month": week_number,
                "weekday": day.weekday() + 1,
            }
        )

    days_until_next_monday = 7 - analysis_date.weekday()
    first_week_start = analysis_date + timedelta(days=days_until_next_monday)
    weekly = []
    for offset in range(4):
        week_start = first_week_start + timedelta(days=offset * 7)
        week_end = week_start + timedelta(days=6)
        week_values = [
            future_value(week_start + timedelta(days=day_offset))
            for day_offset in range(7)
        ]
        value = (
            sum(week_values)
            if aggregation in ADDITIVE_AGGREGATIONS
            else statistics.fmean(week_values)
        )
        weekly.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "value": float(value),
                "days": 7,
                "is_partial": False,
            }
        )

    history_months = len(months)
    if history_months >= requested_months and len(observations) >= 180:
        confidence = "high"
    elif history_months >= 6 and len(observations) >= 84:
        confidence = "medium"
    else:
        confidence = "low"
    warning_parts = []
    if history_months < requested_months:
        warning_parts.append(
            f"История ограничена: {history_months} из {requested_months} мес."
        )
    if dq_status in {"critical", "blocked"}:
        confidence = "low"
        warning_parts.append(
            "Качество данных ограничивает надёжность прогноза."
        )
    warning = " ".join(warning_parts) or "Использовано 12 месяцев истории."
    return {
        "available": True,
        "reason": None,
        "method": "trend_x_month_week_x_weekday",
        "requested_history_months": requested_months,
        "history_start": observations[0][0].isoformat(),
        "history_end": observations[-1][0].isoformat(),
        "history_days_used": len(observations),
        "history_months_used": history_months,
        "limited_history": history_months < requested_months,
        "confidence": confidence,
        "warning": warning,
        "coefficients": {
            "week_of_month": {
                str(key): value for key, value in sorted(week_factors.items())
            },
            "weekday": {
                str(key): value for key, value in sorted(weekday_factors.items())
            },
        },
        "daily_7d": daily,
        "weekly_4w": weekly,
    }


def _reference_contract(
    metric: Mapping[str, Any],
    plan_month: float | None,
    plan_info: Mapping[str, Any],
    days_in_month: int,
) -> dict[str, Any]:
    labels = {
        "exact_client": "План клиента",
        "history_median": "Исторический ориентир",
        "guardrail": "Guardrail реестра",
        "category_benchmark": "Категорийный benchmark",
        "comparable_client_history": "История сопоставимых клиентов",
    }
    reference_type = str(plan_info.get("type") or "unavailable")
    aggregation = str(metric.get("aggregation") or "")
    daily_value: float | None = None
    weekly_value: float | None = None
    if plan_month is not None:
        if aggregation in ADDITIVE_AGGREGATIONS and days_in_month > 0:
            daily_value = plan_month / days_in_month
            weekly_value = daily_value * 7
        elif aggregation in RATIO_AGGREGATIONS | {"snapshot", "metadata"}:
            daily_value = plan_month
            weekly_value = plan_month
    return {
        "available": daily_value is not None,
        "type": reference_type,
        "label": labels.get(reference_type, "Норматив не задан"),
        "source": plan_info.get("provenance"),
        "provisional": bool(plan_info.get("provisional", True)),
        "monthly_value": plan_month,
        "daily_value": daily_value,
        "weekly_value": weekly_value,
        "weekly_basis": (
            "monthly_plan_divided_by_calendar_days_times_7"
            if aggregation in ADDITIVE_AGGREGATIONS and daily_value is not None
            else "same_level_as_monthly_reference"
            if daily_value is not None
            else None
        ),
    }


def _average_non_null(points: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    values = [
        value
        for point in points
        if (value := _number(point.get("value"))) is not None
    ]
    return (sum(values) / len(values), len(values)) if values else (None, 0)


def _trend_insight(
    metric: Mapping[str, Any],
    current: Sequence[Mapping[str, Any]],
    reference: Mapping[str, Any],
    status: Mapping[str, Any],
    stability_band: float,
) -> dict[str, Any]:
    latest_point = next(
        (point for point in reversed(current) if _number(point.get("value")) is not None),
        None,
    )
    latest_value = _number((latest_point or {}).get("value"))
    reference_value = _number(reference.get("daily_value"))
    deviation = _delta(latest_value, reference_value)
    deviation_ratio = (
        deviation / abs(reference_value)
        if deviation is not None and reference_value not in (None, 0)
        else None
    )
    recent_average, recent_points = _average_non_null(current[-7:])
    previous_average, previous_points = _average_non_null(current[-14:-7])
    change_ratio = (
        (recent_average - previous_average) / abs(previous_average)
        if recent_average is not None and previous_average not in (None, 0)
        else None
    )
    direction = str(metric.get("direction") or "")
    dynamics_code = "insufficient_data"
    dynamics_label = "Динамика не определена"
    if change_ratio is not None and recent_points >= 3 and previous_points >= 3:
        if abs(change_ratio) < stability_band:
            dynamics_code, dynamics_label = "stable", "Стабильно"
        elif direction == "higher_is_better":
            dynamics_code = "improving" if change_ratio > 0 else "worsening"
            dynamics_label = "Улучшается" if change_ratio > 0 else "Ухудшается"
        elif direction == "lower_is_better":
            dynamics_code = "worsening" if change_ratio > 0 else "improving"
            dynamics_label = "Ухудшается" if change_ratio > 0 else "Улучшается"
        elif direction == "target_range" and reference_value is not None:
            recent_distance = abs(recent_average - reference_value)
            previous_distance = abs(previous_average - reference_value)
            distance_delta = recent_distance - previous_distance
            tolerance = max(abs(reference_value) * stability_band, 1e-12)
            if abs(distance_delta) < tolerance:
                dynamics_code, dynamics_label = "stable", "Стабильно относительно норматива"
            elif distance_delta < 0:
                dynamics_code, dynamics_label = "improving", "Приближается к нормативу"
            else:
                dynamics_code, dynamics_label = "worsening", "Удаляется от норматива"
        else:
            dynamics_code = "growing" if change_ratio > 0 else "falling"
            dynamics_label = "Растёт" if change_ratio > 0 else "Снижается"

    code = str(status.get("code") or "no_data")
    basis = str(status.get("basis") or "")
    if basis == "data_quality_override":
        state_code, state_label = "dq_limited", "Вывод ограничен качеством данных"
    elif code == "ok":
        state_code, state_label = "in_norm", "В норме"
    elif code == "no_plan":
        state_code, state_label = "no_reference", "Норматив не задан"
    elif code == "no_data":
        state_code, state_label = "no_data", "Недостаточно данных"
    elif code in {"not_applicable", "immature", "limited"}:
        state_code, state_label = "limited", "Требует осторожной интерпретации"
    else:
        state_code, state_label = "out_of_norm", "Не в норме"
    return {
        "latest_date": (latest_point or {}).get("date"),
        "latest_value": latest_value,
        "reference_value": reference_value,
        "deviation": deviation,
        "deviation_ratio": deviation_ratio,
        "status_scope": "month_to_date",
        "state_code": state_code,
        "state_label": state_label,
        "dynamics_code": dynamics_code,
        "dynamics_label": dynamics_label,
        "recent_7d_average": recent_average,
        "previous_7d_average": previous_average,
        "recent_points": recent_points,
        "previous_points": previous_points,
        "change_ratio": change_ratio,
        "stability_band": stability_band,
        "conclusion": f"{state_label}. {dynamics_label}.",
    }


def _column_contract(
    previous_months: Sequence[date],
    current_month: date,
) -> list[dict[str, Any]]:
    columns = [
        {
            "id": f"fact_{month:%Y_%m}",
            "label": f"Факт {month:%Y-%m}",
            "kind": "previous_fact",
            "month": month.isoformat(),
        }
        for month in previous_months
    ]
    columns.extend(
        [
            {
                "id": "plan_month",
                "label": f"План {current_month:%Y-%m}",
                "kind": "plan",
            },
            {"id": "plan_mtd", "label": "План MTD", "kind": "plan_mtd"},
            {"id": "fact_mtd", "label": "Факт MTD", "kind": "fact_mtd"},
            {
                "id": "plan_fact_ratio",
                "label": "План-факт, %",
                "kind": "ratio",
            },
            {"id": "run_rate", "label": "Run Rate", "kind": "run_rate"},
            {
                "id": "run_rate_ratio",
                "label": "Run Rate / план, %",
                "kind": "ratio",
            },
            {"id": "trend_28d", "label": "Последние 28 дней", "kind": "trend"},
            {"id": "status", "label": "Статус", "kind": "status"},
        ]
    )
    return columns


def _health_band(
    score: float | None,
    bands: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if score is None:
        return {"code": "unavailable", "label": "Нет оценки"}
    ordered = sorted(
        bands,
        key=lambda item: float(item.get("minimum") or 0),
        reverse=True,
    )
    for band in ordered:
        if score >= float(band.get("minimum") or 0):
            return {
                "code": str(band.get("code") or "unavailable"),
                "label": str(band.get("label") or "Нет оценки"),
            }
    return {"code": "unavailable", "label": "Нет оценки"}


def _health_score_contract(
    report_config: Mapping[str, Any],
    report_tree: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    dq_status: str,
) -> dict[str, Any]:
    config = dict(report_config.get("health_score") or {})
    points = {
        str(code): float(value)
        for code, value in (
            config.get("status_points")
            or {"ok": 100, "watch": 75, "signal": 50, "critical": 0}
        ).items()
    }
    bands = list(
        config.get("bands")
        or [
            {"minimum": 85, "code": "healthy", "label": "В норме"},
            {"minimum": 70, "code": "watch", "label": "Есть риски"},
            {"minimum": 50, "code": "signal", "label": "Нужна коррекция"},
            {"minimum": 0, "code": "critical", "label": "Критично"},
        ]
    )
    blocking_dq = set(config.get("dq_blocking_statuses") or ["critical", "blocked"])
    dq_blocked = dq_status in blocking_dq
    rows_by_id = {str(row.get("id") or ""): row for row in rows}

    def score_ids(metric_ids: Sequence[str]) -> dict[str, Any]:
        selected = [rows_by_id[item] for item in metric_ids if item in rows_by_id]
        fact_count = sum(
            row.get("values", {}).get("fact_mtd") is not None for row in selected
        )
        evaluated = [
            row
            for row in selected
            if row.get("values", {}).get("fact_mtd") is not None
            and str(row.get("status", {}).get("code") or "") in points
            and str(row.get("status", {}).get("basis") or "")
            != "data_quality_override"
        ]
        score = None
        if evaluated and not dq_blocked:
            score = round(
                statistics.mean(
                    points[str(row.get("status", {}).get("code"))]
                    for row in evaluated
                ),
                1,
            )
        contract_count = len(selected)
        return {
            "score": score,
            "band": _health_band(score, bands),
            "evaluated_metric_count": len(evaluated),
            "fact_metric_count": fact_count,
            "contract_metric_count": contract_count,
            "coverage_pct": round(
                len(evaluated) / contract_count * 100,
                1,
            ) if contract_count else 0.0,
            "status_counts": dict(
                sorted(
                    Counter(
                        str(row.get("status", {}).get("code") or "no_data")
                        for row in selected
                    ).items()
                )
            ),
        }

    blocks: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any], parent_id: str | None = None) -> dict[str, Any]:
        node_id = str(node.get("id") or "")
        children = [visit(child, node_id) for child in node.get("children") or []]
        metric_ids = [str(item) for item in node.get("metric_ids") or []]
        if children:
            contract_count = sum(item["contract_metric_count"] for item in children)
            evaluated_count = sum(item["evaluated_metric_count"] for item in children)
            fact_count = sum(item["fact_metric_count"] for item in children)
            child_scores = [item["score"] for item in children if item["score"] is not None]
            score = (
                round(statistics.mean(child_scores), 1)
                if child_scores and not dq_blocked
                else None
            )
            status_counts = Counter()
            for item in children:
                status_counts.update(item["status_counts"])
            scored = {
                "score": score,
                "band": _health_band(score, bands),
                "evaluated_metric_count": evaluated_count,
                "fact_metric_count": fact_count,
                "contract_metric_count": contract_count,
                "coverage_pct": round(
                    evaluated_count / contract_count * 100,
                    1,
                ) if contract_count else 0.0,
                "status_counts": dict(sorted(status_counts.items())),
            }
        else:
            scored = score_ids(metric_ids)
        block = {
            "id": node_id,
            "name": str(node.get("name") or node_id),
            "parent_id": parent_id,
            "kind": "group" if parent_id is None else "branch",
            **scored,
        }
        blocks.append(block)
        return block

    roots = [visit(node) for node in report_tree]
    weights = {
        str(key): float(value)
        for key, value in (config.get("total_weights") or {}).items()
    }
    weighted_roots = [root for root in roots if weights.get(root["id"], 0) > 0]
    total_score = None
    if (
        weighted_roots
        and not dq_blocked
        and all(root["score"] is not None for root in weighted_roots)
    ):
        weight_sum = sum(weights[root["id"]] for root in weighted_roots)
        if weight_sum > 0:
            total_score = round(
                sum(root["score"] * weights[root["id"]] for root in weighted_roots)
                / weight_sum,
                1,
            )
    contract_count = sum(root["contract_metric_count"] for root in roots)
    evaluated_count = sum(root["evaluated_metric_count"] for root in roots)
    fact_count = sum(root["fact_metric_count"] for root in roots)
    reliability = "blocked" if dq_blocked else (
        "ready" if contract_count and evaluated_count / contract_count >= 0.7 else "limited"
    )
    return {
        "version": str(config.get("version") or "1.0.0"),
        "scale": {"minimum": 0, "maximum": 100},
        "status_points": points,
        "excluded_statuses": list(
            config.get("excluded_statuses")
            or ["no_data", "no_plan", "not_applicable", "limited", "immature"]
        ),
        "methodology": {
            "metric_aggregation": "mean_of_evaluable_metric_points",
            "diagnostics_aggregation": "equal_mean_of_available_child_blocks",
            "total_weights": weights,
            "missing_values": "excluded_from_score_and_disclosed_in_coverage",
        },
        "reliability": {
            "status": reliability,
            "dq_status": dq_status,
            "reason": (
                "Критическое качество данных блокирует достоверный Health Score."
                if dq_blocked
                else "Score рассчитан только по метрикам с фактом и применимым нормативом."
            ),
        },
        "total": {
            "score": total_score,
            "band": _health_band(total_score, bands),
            "evaluated_metric_count": evaluated_count,
            "fact_metric_count": fact_count,
            "contract_metric_count": contract_count,
            "coverage_pct": round(
                evaluated_count / contract_count * 100,
                1,
            ) if contract_count else 0.0,
        },
        "blocks": blocks,
    }


def build_matrix_payload(
    snapshot: Mapping[str, Any],
    report_config: Mapping[str, Any],
    registry: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Transform a reviewed snapshot into one deterministic BI response."""

    metrics = list(report_config.get("metrics") or [])
    metric_ids = [str(metric.get("id") or "") for metric in metrics]
    if not metrics or any(not item for item in metric_ids):
        raise ValueError("Report config must contain non-empty metric ids")
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("Report config contains duplicate metric ids")
    metric_guides = _validated_metric_guides(report_config, metric_ids)
    report_tree = _validated_tree(report_config, metric_ids)

    analysis_date = _date(snapshot.get("analysis_date"))
    if analysis_date is None:
        raise ValueError("Snapshot analysis_date is required")
    current_month = _date(snapshot.get("current_month")) or _month_start(analysis_date)
    current_month = _month_start(current_month)
    current_end = min(analysis_date, _month_end(current_month))
    if current_end < current_month:
        raise ValueError("analysis_date precedes current_month")

    history_months = int(report_config.get("history_months") or 3)
    trend_days = int(report_config.get("trend_days") or 28)
    forecast_history_months = int(
        report_config.get("forecast_history_months") or 12
    )
    stability_band = float(report_config.get("trend_stability_band_pct") or 2) / 100
    previous_months = [
        shift_month(current_month, delta)
        for delta in range(-history_months, 0)
    ]
    marketplace = str(snapshot.get("marketplace") or "").strip().lower()
    source_rows = [
        row
        for row in snapshot.get("daily_rows") or []
        if isinstance(row, Mapping)
        and (day := _date(row.get("report_date"))) is not None
        and day <= analysis_date
    ]
    raw_rows, marketplace_checks_base = _sanitize_marketplace_rows(
        marketplace,
        source_rows,
    )
    raw_rows.sort(key=lambda row: _date(row.get("report_date")) or date.min)
    rows_by_day = {
        day: row
        for row in raw_rows
        if (day := _date(row.get("report_date"))) is not None
    }
    current_rows = _rows_in_window(raw_rows, current_month, current_end)
    metadata_values = dict(snapshot.get("metadata_values") or {})
    current_plan = dict(snapshot.get("current_plan") or {})
    marketplace_checks = _marketplace_checks(
        marketplace,
        raw_rows,
        analysis_date,
        marketplace_checks_base,
    )
    if marketplace == "ozon":
        metadata_values["coverage_14_pct_fraction"] = marketplace_checks[
            "funnel_coverage_14_fraction"
        ]
    registry_by_id, registry_version = _registry_map(registry)
    dq_status = str(
        (snapshot.get("source_dq") or {}).get("status")
        or snapshot.get("source_status")
        or "blocked"
    )
    elapsed_days = (current_end - current_month).days + 1
    days_in_month = calendar.monthrange(current_month.year, current_month.month)[1]
    available_fields = {
        str(key)
        for row in raw_rows
        for key, value in row.items()
        if key != "report_date" and value is not None
    } | set(metadata_values)

    result_rows: list[dict[str, Any]] = []
    for index, metric in enumerate(metrics):
        metric_id = str(metric["id"])
        guide = metric_guides[metric_id]
        strict = (
            marketplace == "ozon"
            and metric_id in OZON_STRICT_FUNNEL_METRICS
        )
        previous_values: list[dict[str, Any]] = []
        for month in previous_months:
            previous_value, _ = _aggregate_window(
                metric,
                raw_rows,
                month,
                _month_end(month),
                {},
                strict,
            )
            if marketplace == "ozon" and metric_id in OZON_UNAVAILABLE_METRICS:
                previous_value = None
            previous_values.append(
                {
                    "month": month.isoformat(),
                    "value": previous_value,
                }
            )

        plan_month, plan_info = _plan_for_metric(
            metric,
            current_plan,
            previous_values,
        )
        plan_mtd = _plan_mtd(
            metric,
            plan_month,
            plan_info,
            current_rows,
            elapsed_days,
            days_in_month,
        )
        fact_mtd, strict_complete = _aggregate_window(
            metric,
            raw_rows,
            current_month,
            current_end,
            metadata_values,
            strict,
            allow_partial=True,
        )
        if marketplace == "ozon" and metric_id in OZON_UNAVAILABLE_METRICS:
            fact_mtd = None
            strict_complete = None
        partial_fact = strict_complete is False and fact_mtd is not None
        run_rate = (
            None
            if partial_fact
            else _run_rate(
                metric,
                fact_mtd,
                elapsed_days,
                days_in_month,
            )
        )
        registry_id = str(metric.get("registry_id") or metric.get("id"))
        registry_contract = registry_by_id.get(registry_id)
        metric_dq_status = "critical" if strict_complete is False else dq_status
        status = _status_with_dq_override(
            metric,
            fact_mtd,
            plan_mtd,
            registry_contract,
            metric_dq_status,
        )
        fields = _metric_fields(metric)
        availability = _availability(
            marketplace,
            metric_id,
            fact_mtd,
            strict_complete,
        )
        source_contract = _source_contract(marketplace, metric)
        current_trend = _trend(
            metric,
            rows_by_day,
            analysis_date,
            metadata_values,
            trend_days,
        )
        previous_trend = _trend(
            metric,
            rows_by_day,
            analysis_date - timedelta(days=trend_days),
            metadata_values,
            trend_days,
            include_metadata=False,
        )
        unavailable = marketplace == "ozon" and metric_id in OZON_UNAVAILABLE_METRICS
        weekly_ytd = _weekly_ytd(
            metric,
            raw_rows,
            analysis_date,
            metadata_values,
            strict,
            unavailable=unavailable,
        )
        forecast = _metric_forecast(
            metric,
            raw_rows,
            analysis_date,
            metadata_values,
            metric_dq_status,
            unavailable=unavailable,
            requested_months=forecast_history_months,
        )
        previous_forecast = _metric_forecast(
            metric,
            raw_rows,
            analysis_date - timedelta(days=trend_days),
            metadata_values,
            metric_dq_status,
            unavailable=unavailable,
            requested_months=forecast_history_months,
        )
        forecast["previous_period"] = {
            key: previous_forecast.get(key)
            for key in (
                "available",
                "reason",
                "method",
                "history_start",
                "history_end",
                "history_days_used",
                "history_months_used",
                "limited_history",
                "confidence",
                "warning",
                "daily_7d",
            )
        }
        reference = _reference_contract(
            metric,
            plan_month,
            plan_info,
            days_in_month,
        )
        insight = _trend_insight(
            metric,
            current_trend,
            reference,
            status,
            stability_band,
        )
        data_gaps = _metric_gap_contract(
            metric,
            marketplace,
            current_trend,
            previous_trend,
            marketplace_checks,
        )
        result_rows.append(
            {
                "index": index,
                "id": metric_id,
                "registry_id": registry_id if registry_contract else None,
                "name": guide["display_name"],
                "section": str(metric.get("section") or "Без раздела"),
                "unit": str(metric.get("unit") or ""),
                "direction": str(metric.get("direction") or ""),
                "aggregation": str(metric.get("aggregation") or ""),
                "values": {
                    "previous_months": previous_values,
                    "plan_month": plan_month,
                    "plan_mtd": plan_mtd,
                    "fact_mtd": fact_mtd,
                    "delta_to_plan_mtd": (
                        None if partial_fact else _delta(fact_mtd, plan_mtd)
                    ),
                    "plan_fact_ratio": (
                        None if partial_fact else _ratio(fact_mtd, plan_mtd)
                    ),
                    "run_rate": run_rate,
                    "run_rate_ratio": (
                        None
                        if partial_fact
                        else _ratio(run_rate, plan_month)
                    ),
                },
                "trend_28d": current_trend,
                "trend_previous_28d": previous_trend,
                "weekly_ytd": weekly_ytd,
                "forecast": forecast,
                "chart_reference": reference,
                "chart_insight": insight,
                "data_gaps": data_gaps,
                "status": status,
                "plan": {
                    "value": plan_month,
                    "marketplace": marketplace,
                    "source_objects": source_contract["objects"],
                    **plan_info,
                },
                "source": {
                    **source_contract,
                    "fields": fields,
                    "available_fields": [
                        field
                        for field in fields
                        if field in available_fields
                        or field in metadata_values
                    ],
                    "cutoff": analysis_date.isoformat(),
                },
                "availability": availability,
                "guide": dict(guide),
                "contract": {
                    "formula": (
                        str(registry_contract.get("formula"))
                        if registry_contract
                        and registry_contract.get("formula")
                        else _formula(metric)
                    ),
                    "thresholds": list(
                        (registry_contract or {}).get("thresholds") or []
                    ),
                    "minimum_sample": (
                        registry_contract or {}
                    ).get("minimum_sample"),
                    "maturity": (registry_contract or {}).get("maturity"),
                    "limitations": list(
                        (registry_contract or {}).get("limitations") or []
                    ),
                    "note": metric.get("note"),
                },
            }
        )

    section_names = list(dict.fromkeys(row["section"] for row in result_rows))
    status_counts = Counter(row["status"]["code"] for row in result_rows)
    unsupported_rows = [
        row
        for row in result_rows
        if not row["availability"]["supported_by_schema"]
    ]
    fact_rows = [
        row for row in result_rows if row["values"]["fact_mtd"] is not None
    ]
    partial_rows = [
        row
        for row in result_rows
        if row["availability"]["status"] == "dq_partial"
    ]
    complete_fact_rows = [
        row for row in fact_rows if row not in partial_rows
    ]
    health_score = _health_score_contract(
        report_config,
        report_tree,
        result_rows,
        dq_status,
    )
    payload = {
        "version": MATRIX_VERSION,
        "title": str(report_config.get("title") or "План-факт воронки продаж"),
        "client": snapshot.get("client"),
        "client_label": snapshot.get("client_label") or snapshot.get("client"),
        "marketplace": snapshot.get("marketplace"),
        "month": current_month.isoformat(),
        "analysis_date": analysis_date.isoformat(),
        "requested_cutoff": snapshot.get("requested_cutoff"),
        "generated_at": snapshot.get("generated_at"),
        "report_config_version": report_config.get("version"),
        "metric_guide_version": report_config.get("metric_guide_version"),
        "registry_version": snapshot.get("registry_version") or registry_version,
        "percentage_scale": {
            "storage": "fraction",
            "minimum": 0,
            "maximum": 1,
            "display_multiplier": 100,
        },
        "metric_count": len(result_rows),
        "section_count": len(section_names),
        "history_months": history_months,
        "forecast_history_months": forecast_history_months,
        "trend_days": trend_days,
        "weekly_ytd_start": date(analysis_date.year, 1, 1).isoformat(),
        "week_starts_on": "monday",
        "trend_stability_band_pct": stability_band * 100,
        "elapsed_days": elapsed_days,
        "days_in_month": days_in_month,
        "columns": _column_contract(previous_months, current_month),
        "tree": report_tree,
        "health_score": health_score,
        "oos_diagnostic": dict(snapshot.get("oos_diagnostic") or {}),
        "scope": dict(snapshot.get("scope") or {}),
        "sections": [
            {
                "name": name,
                "metric_ids": [
                    row["id"] for row in result_rows if row["section"] == name
                ],
            }
            for name in section_names
        ],
        "rows": result_rows,
        "summary": {
            "status_counts": dict(sorted(status_counts.items())),
            "source_status": snapshot.get("source_status"),
            "primary_source_error": snapshot.get("primary_source_error"),
            "optional_errors": list(snapshot.get("optional_errors") or []),
        },
        "coverage": {
            "contract_metric_count": len(result_rows),
            "supported_metric_count": len(result_rows) - len(unsupported_rows),
            "unsupported_metric_count": len(unsupported_rows),
            "fact_metric_count": len(fact_rows),
            "null_fact_metric_count": len(result_rows) - len(fact_rows),
            "complete_fact_metric_count": len(complete_fact_rows),
            "partial_fact_metric_count": len(partial_rows),
            "dq_blocked_metric_count": (
                len(result_rows)
                if dq_status in {"critical", "blocked"}
                else 0
            ),
            "unsupported_metrics": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "reason": row["availability"]["reason"],
                }
                for row in unsupported_rows
            ],
        },
        "data_quality": {
            **dict(snapshot.get("source_dq") or {}),
            "marketplace_checks": marketplace_checks,
        },
        "sources": list(snapshot.get("sources") or []),
    }
    payload["summary"]["coverage"] = dict(payload["coverage"])
    payload["meta"] = {
        key: payload[key]
        for key in (
            "version",
            "title",
            "client",
            "client_label",
            "marketplace",
            "month",
            "analysis_date",
            "requested_cutoff",
            "generated_at",
            "report_config_version",
            "metric_guide_version",
            "registry_version",
            "metric_count",
            "section_count",
            "history_months",
            "forecast_history_months",
            "trend_days",
            "weekly_ytd_start",
            "week_starts_on",
            "trend_stability_band_pct",
            "elapsed_days",
            "days_in_month",
        )
    }
    payload["meta"]["percentage_scale"] = dict(payload["percentage_scale"])
    payload["meta"]["coverage"] = dict(payload["coverage"])
    return payload


def _coerce_date(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    raw = str(value)
    if len(raw) == 7:
        raw += "-01"
    parsed = _date(raw)
    if parsed is None:
        raise ValueError(f"Invalid date: {value!r}")
    return parsed


def _load_contracts(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config_dir = root / "config" / "wb-screening"
    report = json.loads(
        (config_dir / "plan_fact_funnel_report.v1.json").read_text(
            encoding="utf-8"
        )
    )
    registry = json.loads(
        (config_dir / "metric_registry.v1.json").read_text(encoding="utf-8")
    )
    guide_name = str(report.get("metric_guide_file") or "").strip()
    if not guide_name:
        raise ValueError("Report config metric_guide_file is required")
    guide_payload = json.loads(
        (config_dir / guide_name).read_text(encoding="utf-8")
    )
    report = dict(report)
    report["metric_guide_version"] = guide_payload.get("version")
    report["metric_guides"] = dict(guide_payload.get("metrics") or {})
    return report, registry


def _validated_tree(
    report_config: Mapping[str, Any], metric_ids: Sequence[str]
) -> list[dict[str, Any]]:
    tree = list(report_config.get("tree") or [])
    if not tree:
        return []
    referenced: list[str] = []

    def visit(node: Mapping[str, Any]) -> None:
        node_id = str(node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        if not node_id or not name:
            raise ValueError("Every report tree node requires id and name")
        referenced.extend(str(item) for item in node.get("metric_ids") or [])
        for child in node.get("children") or []:
            if not isinstance(child, Mapping):
                raise ValueError("Report tree children must be objects")
            visit(child)

    for root in tree:
        if not isinstance(root, Mapping):
            raise ValueError("Report tree roots must be objects")
        visit(root)
    if len(referenced) != len(set(referenced)):
        raise ValueError("Report tree contains duplicate metric ids")
    if set(referenced) != set(metric_ids):
        missing = sorted(set(metric_ids) - set(referenced))
        extra = sorted(set(referenced) - set(metric_ids))
        raise ValueError(f"Report tree mismatch: missing={missing}, extra={extra}")
    return json.loads(json.dumps(tree, ensure_ascii=False))


def _normalized_scope_filters(
    filters: Mapping[str, str] | None,
) -> dict[str, str]:
    return {
        key: str((filters or {}).get(key) or "").strip()
        for key in ("category", "product", "article")
        if str((filters or {}).get(key) or "").strip()
    }


def _filtered_funnel_payload(
    app: Any,
    client: str,
    marketplace: str,
    start: date,
    cutoff: date,
    filters: Mapping[str, str],
) -> Mapping[str, Any]:
    handler = getattr(app, "handle_funnel_daily", None)
    if not callable(handler):
        return {"rows": [], "reason": "handle_funnel_daily недоступен"}
    params: dict[str, Any] = {
        "date_from": start.isoformat(),
        "date_to": cutoff.isoformat(),
    }
    if filters.get("category"):
        params["categories"] = filters["category"]
    if filters.get("product"):
        params["product"] = filters["product"]
    if filters.get("article"):
        params["article"] = filters["article"]
    return app.review_source_payload(
        client,
        handler,
        "funnel",
        marketplace,
        params,
    )


def _window_sum(
    rows: Iterable[Mapping[str, Any]],
    field: str,
    start: date,
    end: date,
) -> float:
    total = 0.0
    for row in rows:
        day = _date(row.get("report_date"))
        value = _number(row.get(field))
        if day is not None and start <= day <= end and value is not None:
            total += value
    return total


def _scope_share(scoped: float, total: float) -> float | None:
    if total <= 0:
        return None
    return min(1.0, max(0.0, scoped / total))


def _scaled(value: Any, share: float | None) -> float | None:
    numeric = _number(value)
    if numeric is None or share is None:
        return None
    return numeric * share


def _apply_scope_to_daily_rows(
    rows: list[dict[str, Any]],
    order_share: float | None,
    ad_spend_share: float | None,
) -> None:
    for row in rows:
        row["pf_orders_rub"] = _number(row.get("ordered_amount_rub"))
        row["pf_sales_rub"] = _number(row.get("bought_amount_rub"))
        row["pf_ad_spend_rub"] = _number(row.get("adv_expense_rub"))
        row["pf_sales_plan_daily_rub"] = _scaled(
            row.get("pf_sales_plan_daily_rub"), order_share
        )
        row["pf_ad_spend_plan_daily_rub"] = _scaled(
            row.get("pf_ad_spend_plan_daily_rub"), ad_spend_share
        )


def _allocate_current_plan(
    plan: Mapping[str, Any],
    order_share: float | None,
    ad_spend_share: float | None,
) -> dict[str, Any]:
    allocated = dict(plan)
    allocated["sales_plan_rub"] = _scaled(
        allocated.get("sales_plan_rub"), order_share
    )
    allocated["ad_spend_plan_rub"] = _scaled(
        allocated.get("ad_spend_plan_rub"), ad_spend_share
    )
    return allocated


def _review_primary_source(
    app: Any,
    task: SourceTask,
    registry_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], dict[str, Any], str | None]:
    try:
        payload = query(app, task)
        reviewed = analyze_source(task, payload, registry_by_id)
        return {**dict(payload), "source_mode": "funnel"}, reviewed, None
    except Exception as exc:
        error = safe_error(exc)

    # Some clients have only an advertising mart. Health Check must still show
    # every backed advertising/attributed-sales metric instead of failing as a
    # whole because the optional general funnel view is absent.
    advertising_task = SourceTask(
        task.kind,
        task.client,
        task.label,
        "adv",
        task.marketplace,
        task.start,
        task.cutoff,
    )
    try:
        advertising_payload = query(app, advertising_task)
        reviewed = analyze_source(
            advertising_task,
            advertising_payload,
            registry_by_id,
        )
        dq = dict(reviewed.get("dq") or {})
        dq.update(
            {
                "primary_source_available": False,
                "primary_source_error": error,
                "fallback_source": "advertising",
            }
        )
        reviewed = {**reviewed, "dq": dq}
        return _advertising_payload_as_canonical(advertising_payload), reviewed, error
    except Exception as advertising_exc:
        payload = {"rows": [], "source_mode": "unavailable"}
        reviewed = analyze_source(task, payload, registry_by_id)
        dq = dict(reviewed.get("dq") or {})
        dq.update(
            {
                "status": "blocked",
                "primary_source_available": False,
                "primary_source_error": error,
                "fallback_source_error": safe_error(advertising_exc),
            }
        )
        return payload, {**reviewed, "dq": dq}, error


def _advertising_payload_as_canonical(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Map an advertising-only daily mart onto canonical Health Check fields."""

    aliases = {
        "ordered_amount_rub": "total_orders_amount_rub",
        "ordered_units": "total_orders_qty",
        "adv_impressions": "impressions",
        "adv_clicks": "clicks",
        "adv_cart_adds": "added_to_cart",
        "adv_orders": "orders_qty",
        "adv_orders_amount_rub": "orders_amount_rub",
        "adv_expense_rub": "expense_rub",
    }
    rows: list[dict[str, Any]] = []
    for source_row in payload.get("rows") or []:
        if not isinstance(source_row, Mapping):
            continue
        row = dict(source_row)
        for target, source in aliases.items():
            if row.get(target) is None:
                row[target] = row.get(source)
        rows.append(row)
    return {**dict(payload), "rows": rows, "source_mode": "advertising_only"}


def _align_implicit_period_to_source(
    target_month: date,
    requested_cutoff: date,
    source_analysis_date: date | None,
    *,
    explicit_period: bool,
) -> tuple[date, date, bool]:
    """Anchor an unfiltered Health Check to the client's latest real fact.

    A new calendar month may begin before a client's daily marts are refreshed.
    In that case an implicit request must follow the latest available source
    month instead of producing an invalid snapshot whose analysis date precedes
    its current month. Explicit user-selected periods remain authoritative.
    """

    if (
        explicit_period
        or source_analysis_date is None
        or source_analysis_date >= target_month
    ):
        return target_month, requested_cutoff, False
    aligned_month = _month_start(source_analysis_date)
    aligned_cutoff = min(source_analysis_date, _month_end(aligned_month))
    return aligned_month, aligned_cutoff, True


def build_live_matrix(
    app: Any,
    client: str,
    marketplace: str,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    project_root: Path | str | None = None,
    month: date | str | None = None,
    filters: Mapping[str, str] | None = None,
    include_diagnostics: bool = True,
    prefer_source_planfact: bool = False,
) -> dict[str, Any]:
    """Read the existing BI sources and return the same UI-ready matrix payload.

    Database read-only enforcement belongs to the BI runtime connection shim.
    This adapter performs no writes and never calls marketplace live APIs.
    """

    marketplace = str(marketplace or "").strip().lower()
    if marketplace not in {"wb", "ozon"}:
        raise ValueError("marketplace must be 'wb' or 'ozon'")
    root = Path(project_root) if project_root else globals()["project_root"]()
    report_config, registry = _load_contracts(root)
    registry_by_id, registry_version = _registry_map(registry)
    now = datetime.now(ZoneInfo(MOSCOW_TZ))

    explicit_month = _coerce_date(month)
    explicit_start = _coerce_date(date_from)
    explicit_end = _coerce_date(date_to)
    explicit_period = any((explicit_month, explicit_start, explicit_end))
    if explicit_month:
        target_month = _month_start(explicit_month)
    elif explicit_start:
        target_month = _month_start(explicit_start)
    elif explicit_end:
        target_month = _month_start(explicit_end)
    else:
        target_month = _month_start(now.date() - timedelta(days=1))

    natural_cutoff = (
        now.date() - timedelta(days=1)
        if target_month == _month_start(now.date())
        else _month_end(target_month)
    )
    requested_cutoff = explicit_end or natural_cutoff
    requested_cutoff = min(requested_cutoff, _month_end(target_month))
    if requested_cutoff < target_month:
        raise ValueError("date_to precedes the selected month")

    history_months = int(report_config.get("history_months") or 3)
    forecast_history_months = int(
        report_config.get("forecast_history_months") or 12
    )
    history_start = min(
        shift_month(target_month, -history_months),
        shift_month(target_month, -(max(1, forecast_history_months) - 1)),
        date(target_month.year, 1, 1),
    )
    label = client_label(app, client)
    task = SourceTask(
        "daily",
        client,
        label,
        "funnel",
        marketplace,
        history_start,
        requested_cutoff,
    )
    scope_filters = _normalized_scope_filters(filters)
    total_funnel_payload, source, primary_source_error = _review_primary_source(
        app, task, registry_by_id
    )
    source_analysis_date = _date(source.get("analysis_date"))
    target_month, requested_cutoff, period_aligned = _align_implicit_period_to_source(
        target_month,
        requested_cutoff,
        source_analysis_date,
        explicit_period=explicit_period,
    )
    if period_aligned:
        history_start = min(
            shift_month(target_month, -history_months),
            shift_month(target_month, -(max(1, forecast_history_months) - 1)),
            date(target_month.year, 1, 1),
        )
        task = SourceTask(
            "daily",
            client,
            label,
            "funnel",
            marketplace,
            history_start,
            requested_cutoff,
        )
        total_funnel_payload, source, primary_source_error = _review_primary_source(
            app, task, registry_by_id
        )
        source_analysis_date = _date(source.get("analysis_date"))
    analysis_date = source_analysis_date or requested_cutoff
    analysis_date = min(analysis_date, requested_cutoff)
    source_mode = str(total_funnel_payload.get("source_mode") or "funnel")
    funnel_payload = (
        _filtered_funnel_payload(
            app,
            client,
            marketplace,
            history_start,
            analysis_date,
            scope_filters,
        )
        if scope_filters and source_mode == "funnel"
        else total_funnel_payload
    )

    optional_errors: list[str] = []
    if primary_source_error:
        optional_errors.append(f"funnel: {primary_source_error}")
    if scope_filters and source_mode != "funnel":
        optional_errors.append(
            "scope: детализация недоступна для рекламной витрины; показан общий доступный слой"
        )
    daily_planfact_handler = (
        "_handle_planfact_daily_source"
        if prefer_source_planfact
        else "handle_planfact_daily"
    )
    monthly_planfact_handler = (
        "_handle_planfact_monthly_source"
        if prefer_source_planfact
        else "handle_planfact_monthly"
    )
    try:
        daily_planfact = planfact_payload(
            app,
            client,
            marketplace,
            daily_planfact_handler,
            history_start,
            analysis_date,
        )
    except Exception as exc:
        optional_errors.append(f"planfact_daily: {safe_error(exc)}")
        daily_planfact = {"rows": []}
    try:
        monthly_planfact = planfact_payload(
            app,
            client,
            marketplace,
            monthly_planfact_handler,
            history_start,
            analysis_date,
        )
    except Exception as exc:
        optional_errors.append(f"planfact_monthly: {safe_error(exc)}")
        monthly_planfact = {"rows": []}
    try:
        inventory = inventory_payload(
            app,
            client,
            marketplace,
            history_start,
            analysis_date,
            filters=scope_filters,
            include_oos_diagnostic=include_diagnostics,
        )
    except Exception as exc:
        optional_errors.append(f"inventory: {safe_error(exc)}")
        inventory = {"rows": []}

    daily_rows = merge_daily_rows(
        history_start,
        requested_cutoff,
        funnel_payload.get("rows") or [],
        daily_planfact.get("rows") or [],
        inventory.get("rows") or [],
    )
    order_share: float | None = 1.0
    ad_spend_share: float | None = 1.0
    if scope_filters:
        total_rows = total_funnel_payload.get("rows") or []
        scoped_rows = funnel_payload.get("rows") or []
        order_share = _scope_share(
            _window_sum(scoped_rows, "ordered_units", target_month, analysis_date),
            _window_sum(total_rows, "ordered_units", target_month, analysis_date),
        )
        ad_spend_share = _scope_share(
            _window_sum(scoped_rows, "adv_expense_rub", target_month, analysis_date),
            _window_sum(total_rows, "adv_expense_rub", target_month, analysis_date),
        )
        if ad_spend_share is None:
            ad_spend_share = order_share
        _apply_scope_to_daily_rows(daily_rows, order_share, ad_spend_share)
    dq = dict(source.get("dq") or {})
    metadata_values = {
        "analysis_freshness_days": dq.get("analysis_freshness_days"),
        "coverage_14_pct_fraction": (
            float(dq["coverage_14_pct"]) / 100
            if dq.get("coverage_14_pct") is not None
            else None
        ),
        "duplicate_share_fraction": None,
        "outcome_maturity_fraction": None,
    }
    monthly_rows = [
        {str(key): json_value(value) for key, value in row.items()}
        for row in monthly_planfact.get("rows") or []
        if str(row.get("marketplace") or "").lower() == marketplace.lower()
    ]
    plan = current_plan(monthly_rows, marketplace, target_month)
    if scope_filters:
        plan = _allocate_current_plan(plan, order_share, ad_spend_share)
    scope = {
        "active": bool(scope_filters),
        "filters": dict(scope_filters),
        "order_share": order_share if scope_filters else None,
        "ad_spend_share": ad_spend_share if scope_filters else None,
        "sales_plan_allocation": (
            "Доля заказанных единиц MTD выбранной группы"
            if scope_filters
            else "Общий план клиента"
        ),
        "ad_plan_allocation": (
            "Доля рекламных расходов MTD выбранной группы; при отсутствии расходов — доля заказов"
            if scope_filters
            else "Общий рекламный план клиента"
        ),
    }
    snapshot = {
        "version": "1.0.0",
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": MOSCOW_TZ,
        "client": client,
        "client_label": label,
        "marketplace": marketplace,
        "requested_cutoff": requested_cutoff.isoformat(),
        "analysis_date": analysis_date.isoformat(),
        "current_month": target_month.isoformat(),
        "history_start": history_start.isoformat(),
        "registry_version": registry_version,
        "source_status": dq.get("status") or "blocked",
        "source_dq": dq,
        "primary_source_error": primary_source_error,
        "source_mode": source_mode,
        "metadata_values": metadata_values,
        "current_plan": plan,
        "scope": scope,
        "monthly_planfact": monthly_rows,
        "oos_diagnostic": dict(inventory.get("oos_diagnostic") or {}),
        "daily_rows": daily_rows,
        "source_fields": sorted(
            {
                str(key)
                for row in daily_rows
                for key, value in row.items()
                if key != "report_date" and value is not None
            }
        ),
        "optional_errors": optional_errors,
        "sources": [
            {
                "name": (
                    f"{marketplace.upper()} performance advertising daily"
                    if source_mode == "advertising_only"
                    else f"{marketplace.upper()} funnel daily"
                ),
                "canonical_object": (
                    (
                        "mv_ozon_adv_daily_by_article_category"
                        if marketplace == "ozon"
                        else "mv_wb_adv_daily_by_article_category"
                    )
                    if source_mode == "advertising_only"
                    else (
                        "mv_ozon_funnel_daily_by_article_category"
                        if marketplace == "ozon"
                        else "mv_wb_funnel_daily_by_article_category"
                    )
                ),
                "role": (
                    "доступные рекламные метрики и атрибутированные продажи"
                    if source_mode == "advertising_only"
                    else "общая воронка"
                ),
                "cutoff": analysis_date.isoformat(),
                "status": "available" if source_mode != "unavailable" else "error",
                "error": primary_source_error,
            },
            {
                "name": f"{marketplace.upper()} performance advertising daily",
                "canonical_object": (
                    "mv_ozon_adv_daily_by_article_category"
                    if marketplace == "ozon"
                    else "mv_wb_adv_daily_by_article_category"
                ),
                "role": "реклама и производная органика",
                "cutoff": analysis_date.isoformat(),
            },
            {
                "name": "Plan-fact daily/monthly",
                "canonical_object": "mv_planfact_daily / mv_planfact_monthly",
                "role": "GMV, продажи, рекламный бюджет и факты",
                "cutoff": analysis_date.isoformat(),
            },
            {
                "name": "Inventory history",
                "canonical_object": "inventory_history_daily",
                "role": "остатки и доступность ассортимента",
                "cutoff": max(
                    (
                        str(row.get("report_date"))[:10]
                        for row in inventory.get("rows") or []
                    ),
                    default=None,
                ),
            },
        ],
    }
    return build_matrix_payload(snapshot, report_config, registry)


__all__ = [
    "aggregate_metric",
    "build_live_matrix",
    "build_matrix_payload",
]
