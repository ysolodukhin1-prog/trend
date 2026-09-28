#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(
    os.environ.get(
        "KM_DATA_ROOT",
        r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data",
    )
)

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))
os.environ.setdefault("OZON_ADV_SOURCE_DIR", str(DATA_ROOT / "Ozon" / "Adv"))
os.environ.setdefault("OZON_PROD_ADV_SOURCE_DIR", str(DATA_ROOT / "Ozon" / "Prod_adv"))
os.environ.setdefault("OZON_PROD_ADV_START_DATE", "2026-03-01")
os.environ.setdefault("OZON_PROD_ADV_CUTOFF_DATE", "1900-01-01")
sys.argv = [
    str(PROJECT_ROOT / "Скрипты" / "import_ozon_adv_daily_reports.py"),
    *sys.argv[1:],
]
runpy.run_path(sys.argv[0], run_name="__main__")
