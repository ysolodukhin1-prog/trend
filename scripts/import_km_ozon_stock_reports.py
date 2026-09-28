#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(
    os.environ.get(
        "KM_OZON_STOCK_FILE",
        r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data\Ozon\Stock\Stock.xlsx",
    )
)

os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("KM_DB_NAME", "km_trade_products"))
forwarded_args = sys.argv[1:]

sys.argv = [
    str(PROJECT_ROOT / "scripts" / "import_ozon_stock_reports.py"),
    "--source",
    str(SOURCE),
    "--force",
    *forwarded_args,
]
runpy.run_path(sys.argv[0], run_name="__main__")
