#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))

sys.argv = [str(PROJECT_ROOT / "scripts" / "ensure_km_compatibility.py")]
runpy.run_path(sys.argv[0], run_name="__main__")

funnel_script = PROJECT_ROOT / "scripts" / "import_ozon_funnel_reports.py"
print(f"Running {funnel_script.name} --skip-import on {os.environ['DASHBOARD_DB_NAME']}...")
sys.argv = [
    str(funnel_script),
    "--skip-import",
    "--skip-dependent-views",
]
runpy.run_path(str(funnel_script), run_name="__main__")

for script in [
    PROJECT_ROOT / "scripts" / "build_ozon_abc_materialized_views.py",
    PROJECT_ROOT / "scripts" / "rebuild_ozon_sku_scoring_view.py",
    PROJECT_ROOT / "Скрипты" / "rebuild_ozon_adv_daily_category_view.py",
    PROJECT_ROOT / "scripts" / "rebuild_api_planfact_views.py",
]:
    print(f"Running {script.name} on {os.environ['DASHBOARD_DB_NAME']}...")
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name="__main__")

print("KM dashboard views refreshed. errors=0")
