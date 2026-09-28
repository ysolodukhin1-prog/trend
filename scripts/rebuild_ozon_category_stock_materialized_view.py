#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Rebuild Ozon category stock materialized view after assortment refresh."""

from __future__ import annotations

import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
SCRIPT_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import app  # noqa: E402
from import_ozon_stock_reports import (  # noqa: E402
    create_schema,
    ensure_ozon_category_stock_materialized_view,
    format_clock,
    print_progress,
)


def main() -> None:
    started = time.monotonic()
    progress_total = 3
    print(
        "ПЛАН: пересборка mv_ozon_category_stock_sku_attribute_stats | "
        "импорт данных: нет | без API-пауз",
        flush=True,
    )
    with app.get_conn() as conn:
        with conn.cursor() as cur:
            print_progress(1, progress_total, "проверка Ozon stock/category regular views", started)
            create_schema(cur)
            print_progress(
                2,
                progress_total,
                "пересборка mv_ozon_category_stock_sku_attribute_stats",
                started,
                extra="может занять несколько минут",
            )
            ensure_ozon_category_stock_materialized_view(cur)
            print_progress(3, progress_total, "commit", started)
        conn.commit()
    elapsed = time.monotonic() - started
    print(
        "ИТОГ: mv_ozon_category_stock_sku_attribute_stats пересобрана | "
        f"ошибок 0 | stopped no | partial no | elapsed {format_clock(elapsed)}"
    )


if __name__ == "__main__":
    main()