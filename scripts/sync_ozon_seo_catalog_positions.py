#!/usr/bin/env python3
"""Refresh source-backed Ozon search positions used by the SEO catalog."""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ozon_category_dashboard.app import (  # noqa: E402
    apply_registered_client,
    client_registry_connection,
    handle_ozon_seo_product_queries_details,
    read_db_config,
)
from ozon_category_dashboard.client_registry import list_clients  # noqa: E402
from ozon_category_dashboard.seo_projects import _conn, sync_ozon_catalog_positions  # noqa: E402


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


def hydrate_clients():
    with client_registry_connection() as conn:
        for row in list_clients(conn):
            apply_registered_client(row)


def resolve_period(config, date_from, date_to):
    if date_from and date_to:
        return date_from, date_to
    with _conn(config) as conn, conn.cursor() as cur:
        cur.execute("SELECT max(report_date) AS date_to FROM public.mv_ozon_funnel_daily_by_article_category")
        latest = cur.fetchone()["date_to"]
    if not latest:
        raise RuntimeError("В Ozon-воронке нет доступного периода")
    return str(latest - timedelta(days=13)), str(latest)


def progress(event):
    if event["event"] == "plan":
        print(
            f"ПЛАН: {event['total']} SKU | {event['batches']} запросов по пакетам | "
            f"период {event['date_from']} — {event['date_to']} | Ozon rate-limit обрабатывается источником",
            flush=True,
        )
        return
    pct = event["current"] * 100 / max(event["total"], 1)
    eta = "—" if event["eta"] is None else f"{event['eta']:.0f} сек"
    print(
        f"ПРОГРЕСС: {event['current']}/{event['total']} ({pct:.1f}%) | "
        f"запрос {event['batch']}/{event['batches']} | сохранено {event['imported']} | "
        f"с позицией {event['positioned']} | ошибок {event['errors']} | ETA {eta}",
        flush=True,
    )


def main():
    args = arguments()
    hydrate_clients()
    config = read_db_config(args.client)
    date_from, date_to = resolve_period(config, args.date_from, args.date_to)
    result = sync_ozon_catalog_positions(
        config,
        lambda payload: handle_ozon_seo_product_queries_details({"client": args.client, **payload}),
        date_from,
        date_to,
        batch_size=args.batch_size,
        progress=progress,
    )
    print(
        f"ИТОГ: статус={'partial' if result['partial'] else 'ok'} | всего {result['total_skus']} | "
        f"сохранено {result['imported_skus']} | с позицией {result['positioned_skus']} | "
        f"без поисковых запросов {result['missing_query_skus']} | ошибок {len(result['errors'])} | "
        f"время {result['elapsed_seconds']} сек",
        flush=True,
    )
    if result["errors"]:
        for error in result["errors"]:
            print(f"ОШИБКА: пакет {error['batch']} | {error['error']}", flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
