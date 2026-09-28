#!/usr/bin/env python3
"""Read-only QA for the isolated KM Trade Ozon finance mart."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "ozon_category_dashboard"
TARGET_DB = "km_trade_products"
sys.path.insert(0, str(DASHBOARD))

import app  # noqa: E402
from km_trade_finance import pl_payload, unit_payload  # noqa: E402


def main() -> int:
    print(
        "ПЛАН: KM Trade finance QA | БД km_trade_products | "
        "проверки: диапазоны, источники, сверка событий/строк, API payload | запись=false",
        flush=True,
    )
    config = app.read_db_config("km_trade")
    config["database"] = TARGET_DB
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
        if actual != TARGET_DB:
            raise RuntimeError(f"Защита клиента: ожидалась {TARGET_DB}, открылась {actual}")
        print("ПРОГРЕСС: 1/4 (25%) | защита БД подтверждена", flush=True)

        cur.execute(
            """
            SELECT source_kind, min(operation_date), max(operation_date),
                   count(*), round(sum(total_amount), 2)
            FROM public.ozon_finance_events
            GROUP BY source_kind
            ORDER BY source_kind
            """
        )
        sources = cur.fetchall()
        print("ПРОГРЕСС: 2/4 (50%) | источники", flush=True)
        for row in sources:
            print(
                f"  {row[0]}: {row[1]}..{row[2]} | events={row[3]:,} | total={row[4]}",
                flush=True,
            )

        cur.execute(
            """
            SELECT line_kind, coalesce(type_name, ''), count(*),
                   round(sum(amount), 2)
            FROM public.ozon_finance_lines
            WHERE source_kind = 'ozon_api'
            GROUP BY 1, 2
            ORDER BY 1, abs(sum(amount)) DESC
            """
        )
        types = cur.fetchall()
        print("ПРОГРЕСС: 3/4 (75%) | типы API-строк", flush=True)
        for row in types:
            print(f"  {row[0]} | {row[1] or 'без имени'} | lines={row[2]:,} | amount={row[3]}")

        cur.execute(
            """
            SELECT
                round((SELECT sum(total_amount)
                       FROM public.ozon_finance_events
                       WHERE source_kind = 'ozon_api'), 2),
                round((SELECT sum(amount)
                       FROM public.ozon_finance_lines
                       WHERE source_kind = 'ozon_api'), 2)
            """
        )
        event_total, line_total = cur.fetchone()
        print(
            f"  Сверка API: event_total={event_total} | detailed_line_total={line_total} | "
            f"unallocated={event_total - line_total}",
            flush=True,
        )

    unit = unit_payload(config, "2026-07-01", "2026-07-26")
    pl = pl_payload(config, "2026-07-01", "2026-07-26")
    print(
        "ПРОГРЕСС: 4/4 (100%) | API payload | "
        f"unit_skus={len(unit['rows']):,} | pl_skus={len(pl['products']):,} | "
        f"profit_ready={pl['totals']['profit_ready']}",
        flush=True,
    )
    print(
        "ИТОГ: QA завершён | ошибок=0 | запись=false | "
        f"доступный период={pl['available_date_from']}..{pl['available_date_to']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
