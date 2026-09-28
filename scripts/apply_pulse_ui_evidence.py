"""Apply the verified 2026-09-10 browser action replay to the PULSE QA matrix."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "reports" / "pulse_function_matrix_2026-09-10.csv"
SUMMARY = ROOT / "reports" / "pulse_function_matrix_2026-09-10_summary.json"

AVITO_CLIENTS = {
    "divan_ru_avito_ext",
    "divan_ru_self",
    "na100proext",
    "ozon_bank_b2c",
    "detskiy_mir",
    "yasno",
}
YANDEX_CLIENTS = {"lera_nena", "toptop"}


def set_result(row: dict[str, str], status: str, evidence: str) -> None:
    row["status"] = status
    row["evidence"] = evidence


def apply_avito(row: dict[str, str]) -> None:
    report = row["report"]
    function = row["function"]
    client = row["account"]
    common_pass = {"text_search", "reset_filters", "export_excel"}
    always_na = {"table_sort", "pagination"}
    applicable: dict[str, set[str]] = {
        "avitoOverview": {"status_filter", "format_filter", "pricing_model_filter", "metric_toggle", "axis_assignment"},
        "avitoCampaigns": {"status_filter", "format_filter", "pricing_model_filter"},
        "avitoGroups": {"status_filter", "campaign_filter", "metric_select"},
        "avitoCreatives": {"status_filter", "campaign_filter", "group_filter", "metric_toggle"},
        "avitoDaily": {"campaign_filter", "metric_toggle", "axis_assignment"},
    }
    missing_self = {
        "avitoOverview": {"metric_toggle", "axis_assignment"},
        "avitoCreatives": {"status_filter", "campaign_filter", "group_filter", "metric_toggle"},
        "avitoDaily": {"campaign_filter", "metric_toggle", "axis_assignment"},
    }
    if function == "date_filter":
        set_result(row, "PARTIAL", "CUA 2026-09-10: date picker opened; changed range was not reproducibly applied")
    elif function in common_pass:
        set_result(row, "PASS", "CUA 2026-09-10: control changed and restored; Excel download event confirmed where applicable")
    elif function in always_na:
        set_result(row, "N/A", "CUA 2026-09-10: this native Avito surface has no sortable/paginated table control")
    elif function in applicable.get(report, set()):
        if client == "divan_ru_self" and function in missing_self.get(report, set()):
            set_result(row, "N/A", "CUA 2026-09-10: source returned no applicable option/control for this account")
        else:
            set_result(row, "PASS", "CUA 2026-09-10: option/control changed and restored")


def apply_yandex(row: dict[str, str]) -> None:
    function = row["function"]
    client = row["account"]
    report = row["report"]
    metric_function = {
        "yandexOverview": "summary_cards",
        "yandexFunnel": "funnel_metrics",
        "yandexFinance": "finance_metrics",
        "yandexPromotion": "promotion_metrics",
        "yandexInventory": "inventory_metrics",
    }[report]
    if function in {"date_filter", "apply_filters"}:
        set_result(row, "PARTIAL", "CUA 2026-09-10: date picker opened; changed range was not reproducibly applied")
    elif function in {"table_view", metric_function}:
        set_result(row, "PASS", "CUA 2026-09-10: report-specific metrics and table rendered")
    elif function in {"store_filter", "reset_filters"}:
        if client == "lera_nena":
            set_result(row, "PASS", "CUA 2026-09-10: store option changed and filters reset")
        else:
            set_result(row, "N/A", "CUA 2026-09-10: source returned no selectable store option for this account")


def apply_react_sample(row: dict[str, str]) -> None:
    if row["account"] == "konstex" and row["report"] == "funnel":
        passed = {
            "category_filter", "period_group", "date_filter", "text_search", "reset_filters",
            "metric_toggle", "axis_assignment", "table_sort", "column_filter", "pagination",
            "export_excel",
        }
        if row["function"] in passed:
            set_result(row, "PASS", "CUA 2026-09-10: fixed Konstex funnel control changed and restored")
        elif row["function"] == "marketplace_switch":
            set_result(row, "N/A", "CUA 2026-09-10: Konstex has one marketplace (WB)")
        return
    if row["account"] != "ametist" or row["report"] != "abc":
        return
    passed = {
        "row_limit", "date_filter", "category_filter", "text_search", "reset_filters",
        "advanced_filters", "chart_primary", "chart_secondary", "export_excel",
    }
    if row["function"] in passed:
        set_result(row, "PASS", "CUA 2026-09-10: React control changed and restored on ametist/abc")
    elif row["function"] in {"marketplace_switch", "column_filter"}:
        set_result(row, "N/A", "CUA 2026-09-10: control is not applicable on this single-marketplace/category surface")


def main() -> None:
    with MATRIX.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        if row["account"] in AVITO_CLIENTS and row["report"].startswith("avito"):
            apply_avito(row)
        elif row["account"] in YANDEX_CLIENTS and row["report"].startswith("yandex"):
            apply_yandex(row)
        else:
            apply_react_sample(row)

    with MATRIX.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["account", "report", "function", "status", "evidence"])
        writer.writeheader()
        writer.writerows(rows)

    statuses = Counter(row["status"] for row in rows)
    resolved = statuses["PASS"] + statuses["N/A"]
    payload = {
        "total_rows": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "resolved_rows": resolved,
        "remaining_rows": len(rows) - resolved,
        "coverage_pct": round(resolved / len(rows) * 100, 2),
        "ui_replay_surfaces": 42,
        "ui_replay_scope": {"avito": 30, "yandex_market": 10, "react": 2},
    }
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
