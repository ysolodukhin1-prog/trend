"""Build the resumable account x report x function QA matrix for PULSE."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


API_RESULT = Path("reports/pulse_all_registered_report_apis_latest.json")
OUT_CSV = Path("reports/pulse_function_matrix_2026-09-10.csv")
OUT_JSON = Path("reports/pulse_function_matrix_2026-09-10_summary.json")

COMMON = ("render_report", "api_payload", "select_account", "navigate_report")
CATALOG = {
    "abc": ("marketplace_switch", "category_filter", "row_limit", "date_filter", "product_filter", "text_search", "reset_filters", "advanced_filters", "chart_primary", "chart_secondary", "table_sort", "column_filter", "pagination", "export_excel", "drilldown_to_product"),
    "product": ("marketplace_switch", "category_filter", "row_limit", "date_filter", "product_filter", "text_search", "reset_filters", "advanced_filters", "chart_primary", "chart_secondary", "table_sort", "column_filter", "pagination", "export_excel", "open_sku_sheet"),
    "sku": ("marketplace_switch", "category_filter", "row_limit", "date_filter", "product_filter", "text_search", "reset_filters", "advanced_filters", "chart_primary", "chart_secondary", "table_sort", "column_filter", "pagination", "export_excel"),
    "adv": ("marketplace_switch", "category_filter", "campaign_filter", "row_limit", "date_filter", "product_filter", "text_search", "reset_filters", "advanced_filters", "chart_primary", "chart_secondary", "metric_toggle", "axis_assignment", "table_sort", "column_filter", "pagination", "export_excel", "campaign_drilldown"),
    "mediaAdv": ("marketplace_switch", "media_level_filter", "status_filter", "campaign_filter", "group_filter", "creative_filter", "date_filter", "text_search", "reset_filters", "metric_toggle", "axis_assignment", "table_sort", "column_filter", "pagination", "export_excel"),
    "funnel": ("marketplace_switch", "category_filter", "period_group", "date_filter", "product_filter", "text_search", "reset_filters", "metric_toggle", "axis_assignment", "chart_export", "table_sort", "column_filter", "pagination", "export_excel"),
    "weeklyDynamics": ("marketplace_switch", "category_filter", "date_filter", "product_filter", "text_search", "reset_filters", "summary_cards", "table_sort", "column_filter", "pagination", "export_excel"),
    "inventoryHistory": ("marketplace_switch", "category_filter", "date_filter", "product_filter", "text_search", "reset_filters", "inventory_status", "table_sort", "column_filter", "pagination", "export_excel"),
    "planfact": ("marketplace_switch", "month_filter", "date_filter", "metric_toggle", "metric_color", "chart_type", "axis_assignment", "reset_chart", "table_sort", "column_filter", "pagination", "export_excel"),
    "salesPlanning": ("marketplace_switch", "category_filter", "product_filter", "coefficient_edit", "calculate_plan", "reset_coefficients", "currency_units_switch", "category_expand", "methodology_expand", "pagination", "version_filter", "version_name", "save_plan_version"),
    "mediaPlan": ("marketplace_switch", "category_filter", "product_filter", "coefficient_edit", "restore_actual_references", "save_media_plan"),
    "profitLoss": ("marketplace_tabs", "date_filter", "expense_toggle", "expense_edit", "sku_search", "table_sort", "pagination", "export_excel", "save_expenses"),
    "unitEconomics": ("marketplace_switch", "date_filter", "actual_model_tabs", "sku_search", "table_sort", "pagination", "export_excel", "cost_edit", "save_and_recalculate"),
    "seoMonitoring": ("marketplace_switch", "date_filter", "status_filter", "collection_filter", "text_search", "table_sort", "column_filter", "pagination", "open_project", "new_project_modal", "switch_to_generation", "delete_project"),
    "wbSearchQueries": ("date_filter", "category_filter", "query_classification_filter", "text_search", "metric_toggle", "axis_assignment", "table_sort", "column_filter", "pagination", "reset_filters", "export_excel"),
    "wbAdSearchQueries": ("date_filter", "campaign_filter", "product_filter", "status_filter", "text_search", "refresh_report", "column_order", "table_sort", "column_filter", "pagination", "export_excel"),
    "wbEntrance": ("date_filter", "category_filter", "entrance_filter", "text_search", "metric_toggle", "metric_color", "chart_type", "axis_assignment", "reset_chart", "table_sort", "column_filter", "pagination", "export_excel"),
    "reviews": ("marketplace_filter", "category_filter", "rating_filter", "status_filter", "date_filter", "text_search", "refresh_report", "table_sort", "pagination", "export_excel"),
    "commercialRadar": ("marketplace_context", "health_score", "metric_matrix", "metric_guide", "data_quality_notes", "oos_diagnostic"),
    "avitoOverview": ("date_filter", "text_search", "status_filter", "format_filter", "pricing_model_filter", "reset_filters", "metric_toggle", "axis_assignment", "table_sort", "pagination", "export_excel"),
    "avitoCampaigns": ("date_filter", "text_search", "status_filter", "format_filter", "pricing_model_filter", "reset_filters", "metric_select", "table_sort", "pagination", "export_excel"),
    "avitoGroups": ("date_filter", "text_search", "campaign_filter", "status_filter", "reset_filters", "metric_select", "table_sort", "pagination", "export_excel"),
    "avitoCreatives": ("date_filter", "text_search", "campaign_filter", "group_filter", "status_filter", "reset_filters", "metric_toggle", "axis_assignment", "table_sort", "pagination", "export_excel"),
    "avitoDaily": ("date_filter", "text_search", "campaign_filter", "reset_filters", "metric_toggle", "axis_assignment", "table_sort", "pagination", "export_excel"),
    "yandexOverview": ("date_filter", "store_filter", "apply_filters", "reset_filters", "summary_cards", "table_view"),
    "yandexFunnel": ("date_filter", "store_filter", "apply_filters", "reset_filters", "funnel_metrics", "table_view"),
    "yandexFinance": ("date_filter", "store_filter", "apply_filters", "reset_filters", "finance_metrics", "table_view"),
    "yandexPromotion": ("date_filter", "apply_filters", "reset_filters", "promotion_metrics", "table_view"),
    "yandexInventory": ("date_filter", "store_filter", "apply_filters", "reset_filters", "inventory_metrics", "table_view"),
}

MUTATING = {"save_plan_version", "save_media_plan", "save_expenses", "save_and_recalculate", "delete_project"}


def main() -> None:
    payload = json.loads(API_RESULT.read_text(encoding="utf-8"))
    api_results = {(item["client"], item["report"]): item for item in payload["results"]}
    rows: list[dict[str, str]] = []
    for (client, report), api in sorted(api_results.items()):
        for function in (*COMMON, *CATALOG[report]):
            if function in {"render_report", "select_account", "navigate_report"}:
                status = "PASS"
                evidence = "CUA route sweep 2026-09-10: exact account/report route opened; 206/206 PASS"
            elif function == "api_payload":
                status = "PARTIAL" if api.get("limitation") else "PASS"
                evidence = f"HTTP {api.get('http')}; endpoint={api['endpoint']}; limitation={bool(api.get('limitation'))}; note={api.get('note', '')[:140]}"
            elif function in MUTATING:
                status = "BLOCKED"
                evidence = "production mutation not executed; no isolated draft/test transaction available"
            else:
                status = "PARTIAL"
                evidence = "control/function inventoried; per-account action replay not yet completed after CUA batch reset"
            rows.append({"account": client, "report": report, "function": function, "status": status, "evidence": evidence})

    # SEO generation is a separate visible workspace reachable from SEO monitoring.
    for client, report in sorted(api_results):
        if report != "seoMonitoring":
            continue
        for function in ("render_generation_workspace", "search_projects", "open_project", "new_project_modal", "generation_tabs", "draft_generation", "delete_project"):
            rows.append({
                "account": client,
                "report": "seoGeneration",
                "function": function,
                "status": "BLOCKED" if function == "delete_project" else "PARTIAL",
                "evidence": "visible workspace inventoried; exhaustive per-account action replay remains" if function != "delete_project" else "delete not executed on production data",
            })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("account", "report", "function", "status", "evidence"))
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["status"] for row in rows)
    closed = counts["PASS"] + counts["FIXED"] + counts["N/A"]
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "accounts": len({row["account"] for row in rows}),
        "registered_report_surfaces": len(api_results),
        "extra_seo_generation_surfaces": len({row["account"] for row in rows if row["report"] == "seoGeneration"}),
        "matrix_rows_total": len(rows),
        "closed_rows": closed,
        "remaining_rows": len(rows) - closed,
        "status_counts": dict(sorted(counts.items())),
        "route_sweep": {"checked": 206, "passed": 206, "failed": 0},
        "api_sweep": {"checked": payload["total"], "passed_http": payload["ok"], "failed": payload["failed"], "limitations": payload["limitations"]},
        "matrix_csv": str(OUT_CSV.resolve()),
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
