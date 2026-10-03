"""Refresh one named TREND BI view group for one scoped client."""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path
import runpy
import sys
import time

from psycopg2 import sql
from psycopg2.extras import Json


ROOT = Path(__file__).resolve().parent

VIEW_GROUPS = {
    "wb_view_funnel": (
        "Воронка и фильтры WB",
        810,
        ("mv_wb_funnel_daily_by_article_category", "mv_wb_funnel_filter_options", "mv_wb_funnel_product_options"),
    ),
    "wb_view_abc": (
        "ABC, остатки и карточки WB",
        820,
        ("mv_wb_abc_product_orders_base", "mv_wb_abc_product_stock_base", "mv_sku_card_scoring_wb", "mv_category_stock_sku_attribute_stats", "mv_product_abc_wb"),
    ),
    "wb_view_rollups": (
        "Сводки воронки WB",
        830,
        ("mv_wb_funnel_daily_product_rollup", "mv_wb_funnel_daily_summary_rollup"),
    ),
    "wb_view_product_ads": ("Товарная реклама WB", 840, ("mv_wb_adv_daily_by_article_category",)),
    "wb_view_search_ads": ("Поисковая реклама WB", 850, ("mv_wb_ad_search_query_daily",)),
    "wb_view_media": (
        "Медийная реклама WB",
        860,
        ("mv_wb_media_adv_campaign_daily", "mv_wb_media_adv_group_daily", "mv_wb_media_adv_creative_daily"),
    ),
    "ozon_view_funnel": (
        "Воронка и фильтры Ozon",
        810,
        ("mv_ozon_funnel_daily_by_article_category", "mv_ozon_funnel_filter_options", "mv_ozon_funnel_product_options"),
    ),
    "ozon_view_abc": (
        "ABC, остатки и карточки Ozon",
        820,
        ("mv_ozon_abc_product_orders_base", "mv_ozon_abc_product_stock_base", "mv_sku_card_scoring_ozon", "mv_product_abc_ozon"),
    ),
    "ozon_view_assortment_quality": ("Качество ассортимента Ozon", 830, ("mv_ozon_category_stock_sku_attribute_stats",)),
    "ozon_view_rollups": (
        "Сводки воронки Ozon",
        840,
        ("mv_ozon_funnel_daily_product_rollup", "mv_ozon_funnel_daily_summary_rollup"),
    ),
    "ozon_view_product_ads": ("Товарная реклама Ozon", 850, ("mv_ozon_adv_daily_by_article_category",)),
    "ozon_view_planfact": ("План/факт Ozon и WB", 860, ("mv_planfact_daily", "mv_planfact_monthly")),
    "ozon_view_media": ("Медийная реклама Ozon", 870, ("mv_ozon_media_adv_daily",)),
    "yandex_view_funnel": ("Воронка Яндекс Маркета", 810, ("yandex_mart_funnel_daily",)),
    "yandex_view_orders": (
        "Заказы и возвраты Яндекс Маркета",
        820,
        ("mv_pulse_yandex_orders_daily_v1", "mv_pulse_yandex_sku_orders_daily_v1", "mv_pulse_yandex_returns_v1"),
    ),
    "yandex_view_finance": (
        "Продвижение и финансы Яндекс Маркета",
        830,
        (
            "mv_pulse_yandex_services_fact_v1", "mv_pulse_yandex_services_daily_v1",
            "mv_pulse_yandex_transactions_daily_v1", "mv_pulse_yandex_marketing_daily_v1",
            "mv_pulse_yandex_boost_sales_v1", "mv_pulse_yandex_realization_v1",
            "mv_pulse_yandex_lost_items_v1", "mv_pulse_home_yandex_finance_daily_v1",
        ),
    ),
    "yandex_view_stocks": ("Остатки Яндекс Маркета", 840, ("mv_pulse_yandex_stocks_v1",)),
    "yandex_view_quality": (
        "Контроль покрытия Яндекс Маркета",
        850,
        ("yandex_mart_quality", "mv_pulse_yandex_data_coverage_v1"),
    ),
}

VIEW_STATE_TABLE = "trend_view_refresh_state"

WORK_MEM_BY_GROUP = {
    # TOPTOP's Yandex funnel aggregates a large source and otherwise spills to
    # pgsql_tmp on hosts with the default work_mem. Keep the override scoped to
    # this refresh transaction instead of raising memory for every connection.
    "yandex_view_funnel": "1GB",
}


def coverage_date(key: str) -> date:
    if key == "yandex_view_stocks":
        return date.today()
    return date.today() - timedelta(days=1)


def market_for(key: str) -> str:
    return key.split("_", 1)[0]


def tasks(client: str, client_label: str, day) -> list[dict]:
    result = []
    for key, (label, order, relations) in VIEW_GROUPS.items():
        market = market_for(key)
        result.append({
            "id": f"{client}:api:{key}:{day}",
            "row_id": f"views:{key}",
            "row_label": label,
            "row_order": order,
            "stage": "views",
            "source_kind": "database",
            "source_label": ", ".join(relations),
            "client": client,
            "client_label": client_label,
            "key": f"api_{key}",
            "report": label,
            "command": [sys.executable, "-u", str(ROOT / "trend_views.py"), "--client", client, "--group", key],
            "cwd": str(ROOT),
            "env": dict(os.environ),
            "initial_status": "queued",
            "initial_detail": f"Свежесть ещё не проверена: {', '.join(relations)}",
            "initial_progress_text": "Проверить витрину",
            "failure_policy": "continue",
            "supports_resume": True,
            "market": market,
        })
    return result


def refresh_group(client: str, key: str) -> None:
    import pulse_vps_admin as v

    v.configure_scope()
    token = v._USE_WRITER_CONFIG.set(True)
    v.app.CURRENT_CLIENT.set(client)
    os.environ.update(DASHBOARD_CLIENT=client, KM_DB_NAME=client, DASHBOARD_DB_NAME=client)
    label, _order, relations = VIEW_GROUPS[key]
    started = time.monotonic()
    print(f"ПЛАН: {client} | {label} | витрин={len(relations)} | API requests=0", flush=True)
    try:
        if key == "ozon_view_planfact":
            script = ROOT.parent / "scripts" / "rebuild_api_planfact_views.py"
            sys.argv = [str(script)]
            runpy.run_path(str(script), run_name="__main__")
        else:
            with v.app.get_conn() as conn, conn.cursor() as cur:
                if work_mem := WORK_MEM_BY_GROUP.get(key):
                    cur.execute("SET LOCAL work_mem = %s", (work_mem,))
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(%s))",
                    (f"public.{VIEW_STATE_TABLE}",),
                )
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {VIEW_STATE_TABLE} ("
                    "group_key text PRIMARY KEY, coverage_date date NOT NULL, "
                    "refreshed_at timestamptz NOT NULL DEFAULT now(), relation_count integer NOT NULL, "
                    "row_counts jsonb NOT NULL)"
                )
                counts = {}
                for index, relation in enumerate(relations, 1):
                    cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
                    row = cur.fetchone()
                    present = next(iter(row.values())) if isinstance(row, dict) else row[0]
                    if not present:
                        raise RuntimeError(f"Витрина отсутствует: {relation}")
                    cur.execute(sql.SQL("REFRESH MATERIALIZED VIEW public.{}").format(sql.Identifier(relation)))
                    cur.execute(sql.SQL("ANALYZE public.{}").format(sql.Identifier(relation)))
                    cur.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(relation)))
                    row = cur.fetchone()
                    counts[relation] = int((next(iter(row.values())) if isinstance(row, dict) else row[0]) or 0)
                    print(
                        f"ПРОГРЕСС: {index}/{len(relations)} ({index/len(relations)*100:.1f}%) | "
                        f"{relation} | rows={counts[relation]} | errors=0",
                        flush=True,
                    )
                cur.execute(
                    f"INSERT INTO {VIEW_STATE_TABLE}(group_key,coverage_date,relation_count,row_counts) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT(group_key) DO UPDATE SET "
                    "coverage_date=EXCLUDED.coverage_date,refreshed_at=now(),"
                    "relation_count=EXCLUDED.relation_count,row_counts=EXCLUDED.row_counts",
                    (key, coverage_date(key), len(relations), Json(counts)),
                )
                conn.commit()
    finally:
        v._USE_WRITER_CONFIG.reset(token)
    print(f"ИТОГ: {label} | витрин={len(relations)} errors=0 elapsed={time.monotonic()-started:.1f}с", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("toptop", "lera_nena"), required=True)
    parser.add_argument("--group", choices=tuple(VIEW_GROUPS), required=True)
    parser.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    refresh_group(args.client, args.group)


if __name__ == "__main__":
    main()
