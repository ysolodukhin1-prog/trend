"""Source-bounded hypothesis analysis for one Health Check metric.

The deterministic DAG is authoritative.  A language model may summarize the
returned evidence pack, but it must not create facts, overwrite data-quality
gates, or promote an untested association to a confirmed cause.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
TREE_PATH = ROOT / "config" / "wb-screening" / "health_check_hypothesis_trees.v1.json"
RULES_PATH = ROOT / "config" / "wb-screening" / "diagnostic_rules.v1.json"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _linear_slope(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    x_mean = (len(values) - 1) / 2
    y_mean = statistics.mean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    if not denominator:
        return 0.0
    return sum(
        (index - x_mean) * (value - y_mean)
        for index, value in enumerate(values)
    ) / denominator


def detect_scenario(
    trend: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    points = [
        {"date": str(point.get("date") or ""), "value": _number(point.get("value"))}
        for point in trend
    ]
    valid = [point for point in points if point["value"] is not None]
    values = [float(point["value"]) for point in valid]
    minimum = int(settings.get("minimum_points") or 7)
    if len(values) < minimum:
        return {
            "code": "insufficient_data",
            "label": "Недостаточно данных",
            "confidence": "low",
            "points_used": len(values),
            "evidence": {},
        }

    baseline = abs(statistics.median(values)) or 1.0
    slope = _linear_slope(values)
    slope_pct = slope / baseline * 100
    mean = statistics.mean(values)
    cv_pct = statistics.pstdev(values) / abs(mean) * 100 if mean else None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    median_change = statistics.median(changes) if changes else 0.0
    mad = statistics.median(abs(value - median_change) for value in changes) if changes else 0.0
    latest_change = changes[-1] if changes else 0.0
    latest_change_pct = latest_change / (abs(values[-2]) or 1.0) * 100
    robust_threshold = max(
        float(settings.get("sudden_change_min_pct") or 15) / 100 * baseline,
        float(settings.get("sudden_change_mad_multiplier") or 3) * mad,
    )
    recent = values[-7:]
    previous = values[-14:-7]
    seven_change_pct = None
    if previous and statistics.mean(previous):
        seven_change_pct = (
            statistics.mean(recent) / statistics.mean(previous) - 1
        ) * 100

    code = "long_plateau"
    confidence = "medium"
    if latest_change <= -robust_threshold:
        code = "sudden_drop"
        confidence = "high" if len(values) >= 14 else "medium"
    elif latest_change >= robust_threshold:
        code = "sudden_spike"
        confidence = "high" if len(values) >= 14 else "medium"
    elif (
        slope_pct <= -float(settings.get("trend_slope_min_pct_per_day") or 0.35)
        and seven_change_pct is not None
        and seven_change_pct <= -float(settings.get("trend_change_min_pct") or 5)
    ):
        code = "trend_decline"
    elif (
        slope_pct >= float(settings.get("trend_slope_min_pct_per_day") or 0.35)
        and seven_change_pct is not None
        and seven_change_pct >= float(settings.get("trend_change_min_pct") or 5)
    ):
        code = "trend_growth"
    elif (
        cv_pct is not None
        and cv_pct <= float(settings.get("plateau_cv_max_pct") or 3)
        and abs(slope_pct) <= float(settings.get("plateau_slope_max_pct_per_day") or 0.2)
        and len(values) >= 14
    ):
        code = "long_plateau"
    elif (
        seven_change_pct is not None
        and seven_change_pct <= -float(settings.get("trend_change_min_pct") or 5)
    ):
        # Prefer the recent 7x7 comparison over the sign of a practically flat
        # regression; otherwise a tiny positive slope can label weakening as growth.
        code = "trend_decline"
        confidence = "medium" if abs(slope_pct) >= 0.1 else "low"
    elif (
        seven_change_pct is not None
        and seven_change_pct >= float(settings.get("trend_change_min_pct") or 5)
    ):
        code = "trend_growth"
        confidence = "medium" if abs(slope_pct) >= 0.1 else "low"
    elif slope_pct <= -float(settings.get("plateau_slope_max_pct_per_day") or 0.2):
        code = "trend_decline"
        confidence = "low"
    elif slope_pct >= float(settings.get("plateau_slope_max_pct_per_day") or 0.2):
        code = "trend_growth"
        confidence = "low"

    labels = {
        "sudden_drop": "Резкое падение",
        "sudden_spike": "Резкий скачок",
        "trend_decline": "Трендовое снижение",
        "trend_growth": "Трендовый рост",
        "long_plateau": "Длительное плато",
    }
    return {
        "code": code,
        "label": labels[code],
        "confidence": confidence,
        "points_used": len(values),
        "first_date": valid[0]["date"],
        "last_date": valid[-1]["date"],
        "evidence": {
            "latest_change_pct": round(latest_change_pct, 2),
            "trend_slope_pct_per_day": round(slope_pct, 3),
            "recent_7_to_previous_7_pct": (
                round(seven_change_pct, 2) if seven_change_pct is not None else None
            ),
            "coefficient_of_variation_pct": (
                round(cv_pct, 2) if cv_pct is not None else None
            ),
        },
    }


def _observation(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"available": False, "reason": "metric_not_in_matrix"}
    values = row.get("values") or {}
    return {
        "available": values.get("fact_mtd") is not None,
        "fact_mtd": values.get("fact_mtd"),
        "plan_mtd": values.get("plan_mtd"),
        "status": str((row.get("status") or {}).get("code") or "no_data"),
        "status_basis": str((row.get("status") or {}).get("basis") or ""),
        "availability": str((row.get("availability") or {}).get("status") or ""),
    }


def _comparable_change(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compare aligned, non-overlapping 28-day windows without imputing gaps."""
    if not row:
        return {"available": False, "current": None, "previous": None, "delta": None, "change_pct": None, "aligned_points": 0, "method": "no_data"}
    current = list(row.get("trend_28d") or [])
    previous = list(row.get("trend_previous_28d") or [])
    valid_indexes = [
        index for index in range(min(len(current), len(previous)))
        if _number(current[index].get("value")) is not None
        and _number(previous[index].get("value")) is not None
    ]
    if not valid_indexes:
        return {"available": False, "current": None, "previous": None, "delta": None, "change_pct": None, "aligned_points": 0, "method": "no_comparable_days"}
    pairs = [
        (float(current[index]["value"]), float(previous[index]["value"]))
        for index in valid_indexes
    ]
    aggregation = str(row.get("aggregation") or "").lower()
    if aggregation == "sum":
        current_level = sum(item[0] for item in pairs)
        previous_level = sum(item[1] for item in pairs)
        method = "sum_on_aligned_days"
    else:
        current_level = statistics.mean(item[0] for item in pairs)
        previous_level = statistics.mean(item[1] for item in pairs)
        method = "mean_on_aligned_days"
    delta = current_level - previous_level
    change_pct = delta / abs(previous_level) * 100 if previous_level != 0 else None
    first_index, last_index = valid_indexes[0], valid_indexes[-1]
    return {
        "available": True,
        "current": current_level,
        "previous": previous_level,
        "delta": delta,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "aligned_points": len(pairs),
        "method": method,
        "current_period": {"from": str(current[first_index].get("date") or ""), "to": str(current[last_index].get("date") or "")},
        "previous_period": {"from": str(previous[first_index].get("date") or ""), "to": str(previous[last_index].get("date") or "")},
    }


STATUS_LABELS_RU = {
    "ok": "в норме",
    "watch": "требует наблюдения",
    "signal": "ниже ориентира",
    "critical": "критическое отклонение",
    "limited": "данные ограничены",
    "immature": "данные ещё не созрели",
    "no_data": "нет данных",
    "no_plan": "нет норматива",
    "not_applicable": "не применимо",
}

METRIC_ALIASES = {
    "ordered_revenue": "ordered_revenue_rub",
    "oos": "oos_sku_share_pct",
    "oos_sku_share": "oos_sku_share_pct",
    "impression_to_card": "impression_to_card_pct",
    "card_to_cart": "card_to_cart_pct",
    "cart_to_order": "cart_to_order_pct",
    "mature_buyout": "mature_buyout_rate_pct",
    "cancellation": "cancellation_rate_pct",
    "local_orders": "local_orders_pct",
    "stock_cover": "stock_cover_days",
    "price_index": "price_index_pct",
    "ad_ctr": "ad_ctr_pct",
    "ad_cpc": "ad_cpc_rub",
    "ad_cpo": "ad_cpo_rub",
    "acos": "acos_pct",
    "tacos": "tacos_pct",
    "paid_share": "paid_impression_share_pct",
    "profit": "profit_after_ads_rub",
    "organic_ctr": "organic_ctr_pct",
}


def _oos_diagnostic_status(oos_diagnostic: Mapping[str, Any] | None) -> str:
    status = str((oos_diagnostic or {}).get("status") or "")
    return status if status in {"supported", "refuted", "evidence_gap"} else ""


def _metric_label(row: Mapping[str, Any] | None, metric_id: str) -> str:
    if not row:
        return metric_id.replace("_", " ")
    guide = row.get("guide") or {}
    return str(guide.get("short_label") or row.get("name") or metric_id)


def _metric_summary(row: Mapping[str, Any] | None, metric_id: str) -> dict[str, Any]:
    observation = _observation(row)
    plan = _number(observation.get("plan_mtd"))
    fact = _number(observation.get("fact_mtd"))
    achievement = fact / plan if fact is not None and plan not in {None, 0.0} else None
    return {
        "metric_id": metric_id,
        "label": _metric_label(row, metric_id),
        "description": str(((row or {}).get("guide") or {}).get("short_description") or ""),
        **observation,
        "status_label": STATUS_LABELS_RU.get(str(observation.get("status")), "статус не определён"),
        "achievement_pct": round(achievement * 100, 1) if achievement is not None else None,
    }


def _metric_status(rows: Mapping[str, Mapping[str, Any]], metric_id: str) -> str:
    canonical = METRIC_ALIASES.get(metric_id, metric_id)
    return str(((rows.get(canonical) or {}).get("status") or {}).get("code") or "no_data")


def _condition_atom(
    atom: str,
    rows: Mapping[str, Mapping[str, Any]],
    oos_diagnostic: Mapping[str, Any] | None = None,
) -> bool | None:
    atom = atom.lower().strip()
    match = re.search(r"([a-z][a-z0-9_]*)\s+status\s+(not\s+)?in\s+\[([^\]]+)\]", atom)
    if match:
        canonical = METRIC_ALIASES.get(match.group(1), match.group(1))
        oos_status = _oos_diagnostic_status(oos_diagnostic) if canonical == "oos_sku_share_pct" else ""
        if oos_status == "evidence_gap":
            return None
        status = (
            "critical" if oos_status == "supported"
            else "ok" if oos_status == "refuted"
            else _metric_status(rows, match.group(1))
        )
        expected = {part.strip() for part in match.group(3).split(",")}
        value = status in expected
        return not value if match.group(2) else value
    match = re.search(r"([a-z][a-z0-9_]*)\s+status\s*=\s*([a-z_]+)", atom)
    if match:
        return _metric_status(rows, match.group(1)) == match.group(2)
    match = re.search(r"([a-z][a-z0-9_]*)\s+is\s+not\s+a\s+signal", atom)
    if match:
        return _metric_status(rows, match.group(1)) not in {"signal", "critical"}
    match = re.search(r"([a-z][a-z0-9_]*)\s+is\s+(?:normal|stable)", atom)
    if match:
        return _metric_status(rows, match.group(1)) in {"ok", "watch"}
    if "oos is not confirmed" in atom:
        oos_status = _oos_diagnostic_status(oos_diagnostic)
        if oos_status:
            return oos_status != "supported"
        return _metric_status(rows, "oos_sku_share_pct") not in {"signal", "critical"}
    if "metric is blocked" in atom:
        return any(_metric_status(rows, metric_id) in {"no_data", "limited", "immature"} for metric_id in rows)
    if "profit is negative" in atom:
        value = _number(((rows.get("profit_after_ads_rub") or {}).get("values") or {}).get("fact_mtd"))
        return value is not None and value < 0
    return None


def _condition_matches(
    condition: str,
    rows: Mapping[str, Mapping[str, Any]],
    oos_diagnostic: Mapping[str, Any] | None = None,
) -> bool | None:
    text = condition.lower().strip()
    if text == "otherwise" or "inconclusive" in text:
        return None
    and_results: list[bool | None] = []
    for clause in [item.strip() for item in re.split(r"\s+and\s+", text) if item.strip()]:
        or_results = [_condition_atom(item, rows, oos_diagnostic) for item in re.split(r"\s+or\s+", clause)]
        if any(value is True for value in or_results):
            and_results.append(True)
        elif all(value is False for value in or_results):
            and_results.append(False)
        else:
            and_results.append(None)
    if any(value is False for value in and_results):
        return False
    if and_results and all(value is True for value in and_results):
        return True
    return None


def _tree_preview(
    entrypoint: str,
    rules: Mapping[str, Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    rule = rules.get(entrypoint)
    if not rule:
        return {
            "id": entrypoint,
            "kind": "evidence_gap",
            "title": "Ветка не описана",
            "terminal_type": "insufficient_data",
        }
    node = {
        "id": entrypoint,
        "kind": str(rule.get("kind") or "decision"),
        "title": str(rule.get("title_ru") or entrypoint),
        "checks": [
            {"metric_id": metric_id, **_observation(rows.get(metric_id))}
            for metric_id in rule.get("checks") or []
        ],
    }
    if rule.get("terminal_type"):
        node["terminal_type"] = str(rule.get("terminal_type"))
        node["conclusion"] = str(rule.get("conclusion_template") or "")
        return node
    branches = []
    for branch in rule.get("branches") or []:
        target = str(branch.get("then") or "")
        required = [str(item) for item in branch.get("evidence_required") or []]
        item = {
            "if": str(branch.get("when") or "otherwise"),
            "then": target,
            "action": str(branch.get("action") or ""),
            "evidence_required": required,
            "evidence_status": (
                "missing" if required else "not_declared"
            ),
        }
        if depth < 3 and target and target not in seen:
            item["next"] = _tree_preview(
                target,
                rules,
                rows,
                depth=depth + 1,
                seen=seen | {entrypoint},
            )
        branches.append(item)
    node["branches"] = branches
    return node


def _relevant_path(
    entrypoint: str,
    rules: Mapping[str, Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    oos_diagnostic: Mapping[str, Any] | None = None,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    node = _tree_preview(entrypoint, rules, rows, depth=3)
    if depth >= 4 or node.get("terminal_type") or entrypoint in seen:
        node["branches"] = []
        return node
    rule = rules.get(entrypoint) or {}
    selected = None
    fallback = None
    for branch in rule.get("branches") or []:
        condition = str(branch.get("when") or "otherwise")
        if condition.lower().strip() == "otherwise" or "inconclusive" in condition.lower():
            fallback = branch
            continue
        if _condition_matches(condition, rows, oos_diagnostic) is True:
            selected = branch
            break
    selected = selected or fallback
    if not selected:
        node["branches"] = []
        return node
    target = str(selected.get("then") or "")
    branch_item = {
        "if": str(selected.get("when") or "otherwise"),
        "then": target,
        "action": str(selected.get("action") or ""),
        "evidence_required": [str(item) for item in selected.get("evidence_required") or []],
        "selected": True,
    }
    if target and target not in seen:
        branch_item["next"] = _relevant_path(
            target,
            rules,
            rows,
            oos_diagnostic=oos_diagnostic,
            depth=depth + 1,
            seen=seen | {entrypoint},
        )
    node["branches"] = [branch_item]
    return node


def _reader_summary(
    metric_id: str,
    row: Mapping[str, Any],
    rows: Mapping[str, Mapping[str, Any]],
    scenario: Mapping[str, Any],
    relevant_path: Mapping[str, Any],
    *,
    dq_blocked: bool,
    missing_dates: Sequence[str],
) -> dict[str, Any]:
    metric = _metric_summary(row, metric_id)
    seven_change = _number((scenario.get("evidence") or {}).get("recent_7_to_previous_7_pct"))
    if metric.get("achievement_pct") is not None:
        what_happened = (
            f"{metric['label']}: выполнено {metric['achievement_pct']:.1f}% плана; "
            f"статус — {metric['status_label']}."
        )
    else:
        what_happened = f"{metric['label']}: {metric['status_label']}."
    if seven_change is not None:
        direction = "выше" if seven_change > 0 else "ниже"
        what_happened += f" Средний уровень последних 7 дней на {abs(seven_change):.1f}% {direction} предыдущих 7 дней."

    path_checks: list[str] = []
    cursor: Mapping[str, Any] | None = relevant_path
    while cursor:
        for check in cursor.get("checks") or []:
            check_id = str(check.get("metric_id") or "")
            if check_id and check_id not in path_checks:
                path_checks.append(check_id)
        branches = cursor.get("branches") or []
        cursor = branches[0].get("next") if branches else None

    support_ids = path_checks + [
        "impressions", "organic_impressions", "ad_impressions",
        "impression_to_card_pct", "card_to_cart_pct", "cart_to_order_pct",
        "oos_sku_share_pct", "ad_ctr_pct",
    ]
    unique_support = list(dict.fromkeys(item for item in support_ids if item != metric_id and item in rows))
    summaries = [_metric_summary(rows.get(item), item) for item in unique_support]
    problems = [item for item in summaries if item["status"] in {"signal", "critical"}]
    normal = [item for item in summaries if item["status"] in {"ok", "watch"}]
    missing = [item for item in summaries if item["status"] in {"no_data", "limited", "immature"}]
    primary = problems[0] if problems else None

    if dq_blocked:
        hypothesis = "Бизнес-причина не определяется: сначала нужно восстановить полноту источника."
        next_step = "Восстановить пропущенные даты и повторить анализ на сопоставимом окне."
    elif metric_id == "ordered_units" and primary and primary["metric_id"] == "impressions":
        hypothesis = (
            "Первый подтверждённый провал находится в охвате: товаров показывают меньше ориентира. "
            "Нижняя часть общей воронки не выглядит основной причиной, если её конверсии находятся в норме."
        )
        next_step = (
            "Разделить потерю показов на органическую и рекламную, затем проверить остатки/OOS, "
            "поисковые позиции и индексацию приоритетных SKU."
        )
    elif primary:
        hypothesis = (
            f"Первый подтверждённый провал — «{primary['label']}». "
            "Это локализует участок проверки, но ещё не доказывает конечную бизнес-причину."
        )
        next_step = f"Сначала проверить источники и факторы, влияющие на «{primary['label']}», затем повторить расчёт."
    else:
        hypothesis = "Первый провал в доступных проверках не найден; причинный вывод пока не подтверждён."
        next_step = "Расширить доказательную базу по зависимым метрикам и повторить проверку."

    return {
        "what_happened": what_happened,
        "scenario_label": str(scenario.get("label") or "Сценарий не определён"),
        "primary_bottleneck": primary,
        "normal_metrics": normal[:4],
        "problem_metrics": problems[:4],
        "missing_metrics": missing[:4],
        "leading_hypothesis": hypothesis,
        "next_step": next_step,
        "limitation": (
            "Пропущенные даты: " + ", ".join(missing_dates)
            if missing_dates
            else "Вывод основан только на доступных метриках; гипотеза не подменяет подтверждённую причину."
        ),
    }


EVIDENCE_STATUS_LABELS = {
    "supported": "Поддержана фактами",
    "refuted": "Не поддержана фактами",
    "evidence_gap": "Нужны данные",
    "not_material": "Не объясняет изменение",
}


def _metric_evidence(
    metric_id: str,
    row: Mapping[str, Any] | None,
    target_change_pct: float | None,
) -> dict[str, Any]:
    summary = _metric_summary(row, metric_id)
    comparison = _comparable_change(row)
    change_pct = _number(comparison.get("change_pct"))
    relation_code = "unknown"
    relation_label = "сопоставимость не рассчитана"
    if change_pct is not None and target_change_pct is not None:
        same_direction = change_pct == 0 or target_change_pct == 0 or (change_pct > 0) == (target_change_pct > 0)
        if same_direction:
            distance = abs(change_pct - target_change_pct)
            threshold = max(5.0, abs(target_change_pct) * 0.35)
            relation_code = "similar" if distance <= threshold else "same_direction"
            relation_label = "изменение сопоставимо" if relation_code == "similar" else "движется в том же направлении"
        else:
            relation_code = "opposite_direction"
            relation_label = "движется в противоположном направлении"
    elif not comparison.get("available"):
        relation_code = "no_comparable_data"
        relation_label = "нет сопоставимого окна"
    return {
        **summary,
        "unit": str((row or {}).get("unit") or ""),
        "comparison": comparison,
        "relation_to_target": {"code": relation_code, "label": relation_label},
    }


def _branch_metric_ids(rule: Mapping[str, Any], next_rule: Mapping[str, Any]) -> list[str]:
    source = list(rule.get("checks") or []) + list(next_rule.get("checks") or [])
    return list(dict.fromkeys(str(metric_id) for metric_id in source if metric_id))


def _evidence_tree(
    entrypoint: str,
    rules: Mapping[str, Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    focal_metric_id: str,
    *,
    dq_blocked: bool,
    target_change_pct: float | None,
    oos_diagnostic: Mapping[str, Any] | None = None,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    rule = rules.get(entrypoint) or {}
    focal_row = rows.get(focal_metric_id)
    node = {
        "id": entrypoint,
        "kind": str(rule.get("kind") or "evidence_gap"),
        "title": str(rule.get("title_ru") or "Проверка причины"),
        "metric": _metric_evidence(focal_metric_id, focal_row, target_change_pct),
        "hypotheses": [],
    }
    if rule.get("terminal_type"):
        terminal_type = str(rule.get("terminal_type") or "")
        node["terminal"] = {
            "type": terminal_type,
            "status": "evidence_gap" if terminal_type.startswith(("blocked", "insufficient")) else "not_material" if terminal_type == "no_signal" else "supported",
            "conclusion": str(rule.get("conclusion_template") or ""),
            "causal_limit": "Терминальная гипотеза требует прямой проверки; синхронная динамика сама по себе не доказывает причинность.",
        }
        return node
    if depth >= 5 or entrypoint in seen:
        node["terminal"] = {"type": "depth_limit", "status": "evidence_gap", "conclusion": "Дальнейшая ветка требует отдельной проверки.", "causal_limit": "Автоматический вывод остановлен на границе доказательств."}
        return node

    branches = list(rule.get("branches") or [])
    evaluations = []
    fallback_index = None
    for index, branch in enumerate(branches):
        condition = str(branch.get("when") or "otherwise")
        if condition.lower().strip() == "otherwise" or "inconclusive" in condition.lower():
            fallback_index = index
            evaluations.append(None)
        else:
            evaluations.append(_condition_matches(condition, rows, oos_diagnostic))
    selected_index = next((index for index, value in enumerate(evaluations) if value is True), None)
    if selected_index is None:
        selected_index = fallback_index
    unresolved_before_fallback = any(value is None for index, value in enumerate(evaluations) if index != fallback_index)

    hypotheses = []
    for index, branch in enumerate(branches):
        target = str(branch.get("then") or "")
        next_rule = rules.get(target) or {}
        is_fallback = index == fallback_index
        selected = index == selected_index
        evaluation = evaluations[index]
        if is_fallback:
            status = "evidence_gap" if unresolved_before_fallback else "not_material"
        elif evaluation is True:
            status = "evidence_gap" if dq_blocked else "supported"
        elif evaluation is False:
            status = "refuted"
        else:
            status = "evidence_gap"
        is_oos_hypothesis = target == "term_stockout_visibility"
        oos_status = _oos_diagnostic_status(oos_diagnostic) if is_oos_hypothesis else ""
        if oos_status:
            status = "evidence_gap" if dq_blocked else oos_status
        evidence = [
            _metric_evidence(metric_id, rows.get(metric_id), target_change_pct)
            for metric_id in _branch_metric_ids(rule, next_rule)
            if metric_id != focal_metric_id
        ]
        missing = [item["label"] for item in evidence if not item.get("comparison", {}).get("available") or item.get("status") in {"no_data", "limited", "immature"}]
        if is_oos_hypothesis and oos_status:
            verdict = str((oos_diagnostic or {}).get("reason") or "Проверка OOS выполнена по SKU.")
            if oos_status == "supported":
                verdict += (
                    f" Затронуто SKU: {int((oos_diagnostic or {}).get('affected_sku_count') or 0)}; "
                    f"оценка потерянных единиц: {(oos_diagnostic or {}).get('estimated_lost_units') or 0}."
                )
        elif status == "supported":
            verdict = "Доступные факты согласуются с гипотезой. Подтверждён участок потери, но не конечная причинность."
        elif status == "refuted":
            verdict = "Доступные факты не поддерживают эту гипотезу на сопоставимом окне."
        elif missing:
            verdict = "Проверка не завершена: нужны данные — " + ", ".join(missing[:4]) + "."
        else:
            verdict = "Измеренного влияния недостаточно, чтобы объяснить изменение целевой метрики."
        item = {
            "id": target,
            "title": str(next_rule.get("title_ru") or "Уточнить причину"),
            "status": status,
            "status_label": EVIDENCE_STATUS_LABELS[status],
            "selected": selected,
            "verdict": verdict,
            "evidence": evidence,
            "action": str(branch.get("action") or ""),
            "causal_limit": "Совпадение направления и масштаба — свидетельство драйвера, а не самостоятельное доказательство корневой причины.",
            "oos_evidence": dict(oos_diagnostic or {}) if is_oos_hypothesis else None,
        }
        if selected and target and target not in seen:
            next_metric = next((metric_id for metric_id in next_rule.get("checks") or [] if metric_id in rows), focal_metric_id)
            item["next"] = _evidence_tree(
                target,
                rules,
                rows,
                next_metric,
                dq_blocked=dq_blocked,
                target_change_pct=target_change_pct,
                oos_diagnostic=oos_diagnostic,
                depth=depth + 1,
                seen=seen | {entrypoint},
            )
        hypotheses.append(item)
    node["hypotheses"] = hypotheses
    return node


def _scenario_effect(scenario_code: str, direction: str) -> str:
    rising = scenario_code in {"sudden_spike", "trend_growth"}
    falling = scenario_code in {"sudden_drop", "trend_decline"}
    if scenario_code == "long_plateau":
        return "нейтрально до сравнения уровня плато с нормативом"
    if direction == "higher_is_better":
        return "положительно" if rising else "негативно" if falling else "не определено"
    if direction == "lower_is_better":
        return "негативно" if rising else "положительно" if falling else "не определено"
    return "зависит от отклонения от целевого диапазона"


def analyze_metric(
    payload: Mapping[str, Any],
    metric_id: str,
    *,
    tree_config: Mapping[str, Any] | None = None,
    rule_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tree_config = dict(tree_config or _load_json(TREE_PATH))
    rule_config = dict(rule_config or _load_json(RULES_PATH))
    rows = {
        str(row.get("id") or ""): row
        for row in payload.get("rows") or []
        if row.get("id")
    }
    row = rows.get(metric_id)
    if row is None:
        raise ValueError(f"Unknown Health Check metric: {metric_id}")
    entrypoint = str((tree_config.get("metric_entrypoints") or {}).get(metric_id) or "")
    if not entrypoint:
        raise ValueError(f"No hypothesis tree for metric: {metric_id}")
    rules = {
        str(rule.get("id") or ""): rule
        for rule in rule_config.get("rules") or []
        if rule.get("id")
    }
    scenario = detect_scenario(
        row.get("trend_28d") or [],
        tree_config.get("scenario_detection") or {},
    )
    scenario_contract = dict((tree_config.get("scenarios") or {}).get(scenario["code"]) or {})
    gaps = dict(row.get("data_gaps") or {})
    status = dict(row.get("status") or {})
    dq_blocked = bool(
        (gaps.get("current") or {}).get("missing_dates")
        or str(status.get("basis") or "") == "data_quality_override"
        or str((payload.get("data_quality") or {}).get("status") or "") in {"critical", "blocked"}
    )
    missing_dates = list((gaps.get("current") or {}).get("missing_dates") or [])
    if dq_blocked:
        conclusion = (
            "Подтверждена проблема качества данных, но бизнес-причина изменения "
            "метрики пока не подтверждена."
        )
        if missing_dates:
            conclusion += " В текущем окне отсутствуют даты: " + ", ".join(missing_dates) + "."
        conclusion += " Сначала восстановите источник и повторите диагностику."
        classification = "evidence_gap"
        confidence = "high"
    else:
        conclusion = (
            f"Обнаружен сценарий «{scenario['label']}». "
            f"Для метрики это {_scenario_effect(scenario['code'], str(row.get('direction') or ''))}. "
            f"Диагностика начинается с ветки «{str(rules.get(entrypoint, {}).get('title_ru') or entrypoint)}»; "
            "причина считается подтверждённой только после прохождения требуемых проверок."
        )
        classification = "leading_hypothesis"
        confidence = str(scenario.get("confidence") or "low")
    all_scenarios = [
        {
            "code": code,
            "label": str(item.get("label") or code),
            "if": str(item.get("if") or ""),
            "then": list(item.get("then") or []),
            "active": code == scenario.get("code"),
        }
        for code, item in (tree_config.get("scenarios") or {}).items()
    ]
    oos_diagnostic = dict(payload.get("oos_diagnostic") or {})
    relevant_path = _relevant_path(
        entrypoint,
        rules,
        rows,
        oos_diagnostic=oos_diagnostic,
    )
    target_comparison = _comparable_change(row)
    evidence_tree = _evidence_tree(
        entrypoint,
        rules,
        rows,
        metric_id,
        dq_blocked=dq_blocked,
        target_change_pct=_number(target_comparison.get("change_pct")),
        oos_diagnostic=oos_diagnostic,
    )
    reader_summary = _reader_summary(
        metric_id,
        row,
        rows,
        scenario,
        relevant_path,
        dq_blocked=dq_blocked,
        missing_dates=missing_dates,
    )
    return {
        "version": "1.3.0",
        "engine": {
            "mode": "deterministic_dag",
            "tree_version": str(tree_config.get("version") or ""),
            "rules_version": str(rule_config.get("version") or ""),
            "model_status": "not_invoked",
        },
        "metric": {
            "id": metric_id,
            "name": str(row.get("name") or metric_id),
            "short_label": str((row.get("guide") or {}).get("short_label") or row.get("name") or metric_id),
            "direction": str(row.get("direction") or ""),
            "unit": str(row.get("unit") or ""),
            "observation": _observation(row),
        },
        "scenario": scenario,
        "scenario_method": scenario_contract,
        "all_scenarios": all_scenarios,
        "data_quality_gate": {
            "blocked": dq_blocked,
            "gaps": gaps,
            "rule": dict(tree_config.get("data_quality_gate") or {}),
        },
        "conclusion": {
            "classification": classification,
            "confidence": confidence,
            "text": conclusion,
        },
        "entrypoint": entrypoint,
        "diagnostic_tree": _tree_preview(entrypoint, rules, rows),
        "relevant_path": relevant_path,
        "evidence_tree": evidence_tree,
        "methodology": {
            "comparison": "aligned non-overlapping 28-day windows; missing values are excluded pairwise and never imputed as zero",
            "association_rule": "direction and materiality localize a driver; causal language requires direct discriminating evidence",
            "oos_rule": "previous-period SKU sales plus >=30% current decline plus latest stock zero or <3 days at previous-period daily rate; missing stock is never zero",
            "terminal_classes": ["confirmed_cause", "leading_hypothesis", "evidence_gap", "not_material"],
        },
        "reader_summary": reader_summary,
        "if_then_summary": [
            f"ЕСЛИ качество данных не прошло gate, ТО бизнес-диагноз блокируется и восстанавливается источник.",
            f"ЕСЛИ подтверждён сценарий «{scenario.get('label')}», ТО запускается ветка «{str(rules.get(entrypoint, {}).get('title_ru') or entrypoint)}».",
            "ЕСЛИ прямых доказательств ветки нет, ТО результат остаётся гипотезой или пробелом доказательств, а не подтверждённой причиной.",
        ],
    }


def build_model_prompt(analysis: Mapping[str, Any]) -> str:
    evidence = {
        "metric": analysis.get("metric"),
        "scenario": analysis.get("scenario"),
        "data_quality_gate": analysis.get("data_quality_gate"),
        "evidence_tree": analysis.get("evidence_tree"),
        "deterministic_conclusion": analysis.get("conclusion"),
    }
    return (
        "Сформируй краткое заключение по метрике маркетплейса на русском. "
        "Используй только JSON ниже. Не выдумывай факты и не называй гипотезу "
        "причиной. Если data_quality_gate.blocked=true, укажи только проблему "
        "данных и следующий проверочный шаг. Формат: вывод; if-then путь; что "
        "проверить первым; уровень уверенности.\n\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
