#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Start the shared BI with the complete Konstex runtime and inventory history.

The complete Konstex implementation is maintained in its client workspace.  This
small project-owned launcher keeps that runtime as the source of truth while
adding the newer inventory-history report that lives in the shared BI project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_RUNTIME = Path(
    r"C:\Users\Solod\Documents\Konstex\run_konstex_dashboard_8052_v6.py"
)
RUNTIME_PATH = Path(os.environ.get("KONSTEX_DASHBOARD_RUNTIME") or DEFAULT_RUNTIME)

BASE_BACKEND_REPORTS = (
    '["abc", "product", "sku", "adv", "wbAdSearchQueries", "funnel", '
    '"weeklyDynamics", "planfact", "profitLoss", "unitEconomics", '
    '"wbSearchQueries", "assortmentDevelopment"]'
)
BASE_CLIENT_REPORTS = BASE_BACKEND_REPORTS.replace(
    '"assortmentDevelopment"]',
    '"assortmentDevelopment", "commercialRadar"]',
)
BASE_DASHBOARD_REPORTS = (
    '["abc", "product", "sku", "adv", "wbAdSearchQueries", "funnel", '
    '"weeklyDynamics", "planfact", "profitLoss", "wbSearchQueries", '
    '"unitEconomics", "assortmentDevelopment", "commercialRadar"]'
)

REPORTS_WITH_INVENTORY = (
    '["abc", "product", "sku", "adv", "wbAdSearchQueries", "funnel", '
    '"weeklyDynamics", "inventoryHistory", "planfact", "profitLoss", '
    '"unitEconomics", "wbSearchQueries", "assortmentDevelopment", '
    '"commercialRadar"]'
)
DASHBOARD_REPORTS_WITH_INVENTORY = (
    '["abc", "product", "sku", "adv", "wbAdSearchQueries", "funnel", '
    '"weeklyDynamics", "inventoryHistory", "planfact", "profitLoss", '
    '"wbSearchQueries", "unitEconomics", "assortmentDevelopment", '
    '"commercialRadar"]'
)

_BACKEND_PREFIX = 'app.ADMIN_CLIENTS["konstex"]["reports"] = '
_CLIENT_PREFIX = (
    'state.clients.push({ key: "konstex", label: "Konstex", status: "active", '
    'reports: '
)
_CLIENT_SUFFIX = " });"
_DASHBOARD_PREFIX = "state.dashboard = "
_DASHBOARD_SUFFIX = '.includes(dashboard) ? dashboard : "funnel";'

BACKEND_REGISTRY = _BACKEND_PREFIX + BASE_BACKEND_REPORTS
BACKEND_REGISTRY_WITH_INVENTORY = _BACKEND_PREFIX + REPORTS_WITH_INVENTORY
CLIENT_REGISTRY_VARIANTS = (
    _CLIENT_PREFIX + BASE_BACKEND_REPORTS + _CLIENT_SUFFIX,
    _CLIENT_PREFIX + BASE_CLIENT_REPORTS + _CLIENT_SUFFIX,
)
CLIENT_REGISTRY_WITH_INVENTORY = (
    _CLIENT_PREFIX + REPORTS_WITH_INVENTORY + _CLIENT_SUFFIX
)
DASHBOARD_REGISTRY = (
    _DASHBOARD_PREFIX + BASE_DASHBOARD_REPORTS + _DASHBOARD_SUFFIX
)
DASHBOARD_REGISTRY_WITH_INVENTORY = (
    _DASHBOARD_PREFIX
    + DASHBOARD_REPORTS_WITH_INVENTORY
    + _DASHBOARD_SUFFIX
)


def _replace_guarded_registry(
    source: str,
    *,
    label: str,
    variants: tuple[str, ...],
    replacement: str,
) -> str:
    matches = [
        (variant, source.count(variant))
        for variant in variants
        if source.count(variant)
    ]
    occurrences = sum(count for _, count in matches)
    if occurrences != 1:
        raise SystemExit(
            "Konstex runtime report registry changed; refusing to start with a "
            f"possibly incomplete client menu ({label}, occurrences={occurrences})."
        )
    return source.replace(matches[0][0], replacement, 1)


def patch_report_registries(source: str) -> str:
    # The Konstex runtime now owns the complete WB report registry, including
    # inventory history. Keep this launcher compatible with newer runtimes
    # instead of rewriting a stale, hard-coded client menu on every start.
    return source


def main() -> None:
    if not RUNTIME_PATH.is_file():
        raise SystemExit(f"Konstex runtime not found: {RUNTIME_PATH}")

    source = RUNTIME_PATH.read_text(encoding="utf-8")
    source = patch_report_registries(source)
    source = source.replace(
        'cache_control="no-cache"',
        'cache_control="no-store, max-age=0, must-revalidate"',
    )
    source = source.replace(
        '    "konstex_wb_daily",\n    "konstex_wb_advertising",',
        '    "konstex_wb_daily",\n'
        '    "konstex_inventory_history",\n'
        '    "konstex_wb_advertising",',
        1,
    )

    source = source.replace(
        'import planfact_funnel_matrix_runtime  # noqa: E402',
        'import planfact_funnel_matrix_runtime  # noqa: E402\n'
        'import health_check_model_providers  # noqa: E402\n'
        'health_check_model_providers.install(app, planfact_funnel_matrix_runtime)',
        1,
    )

    health_result_marker = """                result["category_diagnostics"] = health_check_category_diagnostics.analyze(
                    app,
                    parsed,
                    result.get("context") or {},
                    metric_id,
                )
                self.send_json(result)
"""
    health_result_replacement = """                result["category_diagnostics"] = health_check_category_diagnostics.analyze(
                    app,
                    parsed,
                    result.get("context") or {},
                    metric_id,
                )
                export_format = (parse_qs(parsed.query).get("export") or [""])[0].strip().lower()
                if export_format == "xlsx":
                    body, filename = health_check_category_diagnostics.build_oos_workbook(app, result["category_diagnostics"])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_json(result)
"""
    if source.count(health_result_marker) != 1:
        raise SystemExit("Health Check result route changed; XLSX export was not installed.")
    source = source.replace(health_result_marker, health_result_replacement, 1)

    runtime_dir = str(RUNTIME_PATH.parent)
    if runtime_dir not in sys.path:
        sys.path.insert(0, runtime_dir)

    runtime_globals = {
        "__name__": "__main__",
        "__file__": str(RUNTIME_PATH),
        "__package__": None,
        "__cached__": None,
    }
    exec(compile(source, str(RUNTIME_PATH), "exec"), runtime_globals)


if __name__ == "__main__":
    main()
