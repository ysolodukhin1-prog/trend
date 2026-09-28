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
    "oos_sku_share": "oos_sku_share_pct",
    "impression_to_card": "impression_to_card_pct",
    "card_to_cart": "card_to_cart_pct",
    "cart_to_order": "cart_to_order_pct",
    "ad_ctr": "ad_ctr_pct",
    "organic_ctr": "organic_ctr_pct",
}


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


def _condition_matches(condition: str, rows: Mapping[str, Mapping[str, Any]]) -> bool | None:
    text = condition.lower().strip()
    if text == "otherwise" or "inconclusive" in text:
        return True
    clauses = [item.strip() for item in text.split(" and ") if item.strip()]
    results: list[bool] = []
    for clause in clauses:
        match = re.search(
            r"([a-z][a-z0-9_]*)\s+status\s+(not\s+)?in\s+\[([^\]]+)\]",
            clause,
        )
        if match:
            metric_id = METRIC_ALIASES.get(match.group(1), match.group(1))
            status = str(((rows.get(metric_id) or {}).get("status") or {}).get("code") or "no_data")
            expected = {part.strip() for part in match.group(3).split(",")}
            value = status in expected
            results.append(not value if match.group(2) else value)
            continue
        if "oos is not confirmed" in clause:
            status = str(((rows.get("oos_sku_share_pct") or {}).get("status") or {}).get("code") or "no_data")
            results.append(status not in {"signal", "critical"})
            continue
        match = re.search(r"([a-z][a-z0-9_]*)\s+is\s+not\s+a\s+signal", clause)
        if match:
            metric_id = METRIC_ALIASES.get(match.group(1), match.group(1))
            status = str(((rows.get(metric_id) or {}).get("status") or {}).get("code") or "no_data")
            results.append(status not in {"signal", "critical"})
            continue
        return None
    return all(results) if results else None


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
        if _condition_matches(condition, rows) is True:
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
    relevant_path = _relevant_path(entrypoint, rules, rows)
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
        "version": "1.1.0",
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
        "diagnostic_tree": analysis.get("diagnostic_tree"),
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
