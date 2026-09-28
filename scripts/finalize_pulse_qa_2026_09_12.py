"""Reconcile cumulative PULSE QA with live API and Avito browser evidence."""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reports/pulse_function_matrix_2026-09-11.csv"
API = ROOT / "reports/pulse_all_registered_report_apis_2026-09-12_reconciled.json"
UI = ROOT / "reports/pulse_avito_date_filters_2026-09-12.json"
ROUTES = ROOT / "reports/pulse_registered_routes_2026-09-12_reconciled.json"
OUT = ROOT / "reports/pulse_function_matrix_2026-09-12.csv"
SUMMARY = ROOT / "reports/pulse_function_matrix_2026-09-12_summary.json"


def main():
    api = json.loads(API.read_text(encoding="utf-8"))
    ui = json.loads(UI.read_text(encoding="utf-8"))
    routes = json.loads(ROUTES.read_text(encoding="utf-8"))
    assert api["total"] == 206 and api["failed"] == 0
    assert ui["total"] == 30 and ui["failed"] == 0
    assert all(item["status"] == "PASS" for item in ui["responsive"])
    assert routes["total"] == 206 and routes["failed"] == 0
    api_by_key = {(item["client"], item["report"]): item for item in api["results"]}
    ui_by_key = {(item["account"], item["report"]): item for item in ui["results"]}
    with BASE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    print(f"ПЛАН: {len(rows)} строк; 206 API и 30 Avito UI; без внешних изменений", flush=True)
    updated_api = updated_ui = corrected_na = 0
    for row in rows:
        key = (row["account"], row["report"])
        if row["function"] == "api_payload" and key in api_by_key:
            result = api_by_key[key]
            row["status"] = "PARTIAL" if result["limitation"] else "PASS"
            row["evidence"] = (
                f"API 2026-09-12: HTTP {result['http']}, 2026-09-05..11; "
                f"limitation={result['limitation']}; {result.get('note', '')[:130]}"
            )
            updated_api += 1
        if row["function"] == "date_filter" and key in ui_by_key:
            item = ui_by_key[key]
            row["status"] = "FIXED"
            row["evidence"] = (
                f"Browser UI 2026-09-12: visible preset clicked, applied {item['date']} "
                "and URL confirmed; CSS period menu geometry fixed; source coverage separate"
            )
            updated_ui += 1
        if row["function"] == "axis_assignment" and row["report"] == "avitoCreatives" and key in ui_by_key:
            row["status"] = "N/A"
            row["evidence"] = (
                "2026-09-12 UI+app.js: creative heatmap uses metric intensity, "
                "has no axis control; axis assignment is not supported"
            )
            corrected_na += 1
    assert (updated_api, updated_ui, corrected_na) == (206, 30, 6)
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("account", "report", "function", "status", "evidence"))
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(row["status"] for row in rows)
    per_account = defaultdict(Counter)
    for row in rows:
        per_account[row["account"]][row["status"]] += 1
    closed = sum(counts[key] for key in ("PASS", "FIXED", "N/A"))
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "matrix_kind": "cumulative; old verified evidence retained, current API and Avito dates replayed",
        "accounts": len(per_account), "registered_report_surfaces": len(api_by_key),
        "matrix_rows_total": len(rows), "closed_rows": closed,
        "remaining_rows": len(rows) - closed, "coverage_pct": round(closed / len(rows) * 100, 2),
        "status_counts": dict(sorted(counts.items())),
        "per_account": {key: dict(sorted(value.items())) for key, value in sorted(per_account.items())},
        "current_run": {
            "api_sweep": {"checked": api["total"], "http_ok_after_isolated_retry": api["ok"],
                          "failed": api["failed"], "source_limitations": api["limitations"]},
            "ui_date_filters": {"checked": ui["total"], "passed": ui["passed"],
                                "responsive": ui["responsive"]},
            "unsupported_creative_axes": corrected_na,
            "route_account_title": {"checked": routes["total"], "passed": routes["passed"],
                                    "react_surface_retries": len(routes["retry"])},
        },
        "matrix_csv": str(OUT),
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ИТОГО: {closed}/{len(rows)} ({summary['coverage_pct']}%); осталось {len(rows)-closed}; API={updated_api}; UI={updated_ui}; N/A={corrected_na}; {OUT}", flush=True)


if __name__ == "__main__":
    main()
