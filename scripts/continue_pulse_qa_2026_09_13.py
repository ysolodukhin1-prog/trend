"""Carry forward dated PULSE QA evidence and add today's bounded checks."""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reports/pulse_function_matrix_2026-09-12.csv"
OUT = ROOT / "reports/pulse_function_matrix_2026-09-13.csv"
SUMMARY = ROOT / "reports/pulse_function_matrix_2026-09-13_summary.json"


def main():
    with BASE.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    surfaces = sorted({(row["account"], row["report"]) for row in rows if row["report"] != "seoGeneration"})
    accounts = sorted({row["account"] for row in rows})
    assert len(surfaces) == 206 and len(accounts) == 17

    observed = {
        "render_report": "UI 2026-09-13: LERA/WB plan visible, 595 SKU, source slice 2026-09-10",
        "category_filter": "UI 2026-09-13: Ботинки Apply changed 595 to 53 SKU; demand 459, scenario 849 шт.",
        "category_expand": "UI 2026-09-13: Ботинки row expanded to SKU LNU.304.13914.901 and others",
        "currency_units_switch": "UI 2026-09-13: 53 SKU 849 шт. to 9,559,542 ₽; first SKU 290 × 6159.7 = 1,786,313 ₽",
        "calculate_plan": "UI 2026-09-13: recalculated selected category; 53 SKU and 9,559,542 ₽ retained; 21 focused tests passed",
    }
    for row in rows:
        if (row["account"], row["report"]) == ("lera_nena", "salesPlanning") and row["function"] in observed:
            row["status"] = "PASS"
            row["evidence"] = observed[row["function"]]

    def add(account, report, function, status, evidence):
        rows.append(dict(account=account, report=report, function=function, status=status, evidence=evidence))

    add("lera_nena", "salesPlanning", "planning_sections", "PASS", "UI 2026-09-13: plan, coefficients, supply, versions opened; URL sales_view and content changed")
    add("lera_nena", "salesPlanning", "month_filter", "PASS", "UI 2026-09-13: September to October; Botinki plan 10,434,161 ₽ matched October matrix column")
    add("lera_nena", "salesPlanning", "sku_modal", "PASS", "UI 2026-09-13: SKU 256685368 opened/closed without edits; 12-month demand/plan/price and missing buyout shown")
    add("lera_nena", "salesPlanning", "export_csv", "PARTIAL", "UI click did not produce observable download event; file contents not verified")

    for account in accounts:
        if account == "yasno":
            status, evidence = "FAIL", "UI 2026-09-13: React URL avitoOverview, title Дашборд, content Воронка продаж, no report nav; App.tsx isReportMode fallback"
        elif account == "tsvet_divanov":
            status, evidence = "FAIL", "UI 2026-09-13: switching from Avito route retained unsupported avitoOverview; title Дашборд, content Воронка продаж; App.tsx handleClientChange guard"
        else:
            status, evidence = "PARTIAL", "Current React account-switch content not independently verified; prior route sweep is dated 2026-09-12"
        add(account, "accountNavigation", "react_client_switch", status, evidence)

    for account, report in surfaces:
        for function in ("date_coverage", "numeric_reconciliation", "responsive_visual"):
            add(account, report, function, "PARTIAL", "2026-09-13: per-account source/UI evidence not yet collected; historical API success is insufficient")

    with OUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("account", "report", "function", "status", "evidence"))
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(row["status"] for row in rows)
    per_account = defaultdict(Counter)
    for row in rows:
        per_account[row["account"]][row["status"]] += 1
    closed = sum(counts[key] for key in ("PASS", "FIXED", "N/A"))
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "kind": "provisional cumulative; 206 surfaces last confirmed 2026-09-12, today only 17 account names and bounded UI verified",
        "accounts": len(accounts), "last_confirmed_report_surfaces": len(surfaces),
        "rows": len(rows), "closed": closed, "remaining": len(rows) - closed,
        "coverage_pct": round(100 * closed / len(rows), 2), "statuses": dict(sorted(counts.items())),
        "per_account": {key: dict(sorted(value.items())) for key, value in sorted(per_account.items())},
        "today": {"ui_report": "lera_nena/salesPlanning", "newly_closed_rows": 7,
                  "react_account_switch_fail": 2, "api_sweep": "blocked by dashboard_access_required",
                  "refresh_tasks": {"ok": 46, "queued": 78}},
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("rows", "closed", "remaining", "coverage_pct", "statuses")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
