#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Rebuild generic plan/fact facts from registered-client marketplace APIs."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import app  # noqa: E402
from import_planfact_reports import CREATE_VIEWS, DDL, DROP_VIEWS  # noqa: E402


OZON_SOURCE_FILE = "api://ozon/finance+funnel+performance"
WB_SOURCE_FILE = "api://wb/analytics-detail-history+statistics-sales+promotion"


def relation_exists(cur, relation: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS relation", (relation,))
    return bool(cur.fetchone()["relation"])


def rebuild() -> dict[str, object]:
    started = time.monotonic()
    config = app.read_db_config()
    expected_db = os.environ.get("DASHBOARD_DB_NAME") or config["database"]
    print(
        f"ПЛАН: БД={expected_db} | Ozon finance/funnel/Performance + "
        "WB statistics/promotion | синхронизация дневного факта по площадкам, "
        "сохранение месячных планов, пересборка 2 materialized views",
        flush=True,
    )

    with psycopg2.connect(**config, cursor_factory=RealDictCursor) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database() AS database_name")
        actual_db = cur.fetchone()["database_name"]
        if actual_db != expected_db:
            raise RuntimeError(f"Защита клиента: ожидалась {expected_db}, открылась {actual_db}")

        cur.execute(DDL)
        print("ПРОГРЕСС: 1/4 (25.0%) | таблицы План/факт готовы | errors=0", flush=True)

        cur.execute("DELETE FROM public.planfact_daily WHERE marketplace = 'ozon'")
        replaced_rows = cur.rowcount
        campaign_advertising_sql = (
            """SELECT report_date, coalesce(sum(expense_rub), 0) AS ad_spend_rub
               FROM public.ozon_adv_campaign_daily_raw GROUP BY report_date"""
            if relation_exists(cur, "public.ozon_adv_campaign_daily_raw")
            else "SELECT NULL::date AS report_date, NULL::numeric AS ad_spend_rub WHERE false"
        )
        cur.execute(
            f"""
            WITH finance AS (
                SELECT
                    operation_date AS report_date,
                    coalesce(sum(amount) FILTER (
                        WHERE line_kind = 'revenue' AND amount > 0
                    ), 0) AS sales_rub
                FROM public.ozon_finance_lines
                GROUP BY operation_date
            ),
            funnel AS (
                SELECT
                    report_date,
                    coalesce(sum(ordered_amount_rub), 0) AS orders_rub
                FROM public.ozon_funnel_daily
                GROUP BY report_date
            ),
            sku_advertising AS (
                SELECT
                    report_date,
                    coalesce(sum(coalesce(fact_expense_rub, expense_rub, 0)), 0) AS ad_spend_rub
                FROM public.ozon_adv_daily_raw
                GROUP BY report_date
            ),
            campaign_advertising AS (
                {campaign_advertising_sql}
            ),
            advertising AS (
                SELECT
                    coalesce(s.report_date, c.report_date) AS report_date,
                    coalesce(nullif(s.ad_spend_rub, 0), c.ad_spend_rub, 0) AS ad_spend_rub
                FROM sku_advertising s
                FULL JOIN campaign_advertising c USING (report_date)
            ),
            bounds AS (
                SELECT min(report_date) AS date_from, max(report_date) AS date_to
                FROM (
                    SELECT report_date FROM finance
                    UNION ALL SELECT report_date FROM funnel
                    UNION ALL SELECT report_date FROM advertising
                ) source_dates
            ),
            calendar AS (
                SELECT generate_series(date_from, date_to, interval '1 day')::date AS report_date
                FROM bounds
                WHERE date_from IS NOT NULL AND date_to IS NOT NULL
            )
            INSERT INTO public.planfact_daily (
                marketplace, report_date, orders_rub, sales_rub, ad_spend_rub,
                source_file, source_sheet, source_row_num
            )
            SELECT
                'ozon',
                c.report_date,
                coalesce(fu.orders_rub, 0),
                coalesce(fi.sales_rub, 0),
                coalesce(ad.ad_spend_rub, 0),
                %s,
                'API daily facts',
                NULL
            FROM calendar c
            LEFT JOIN finance fi USING (report_date)
            LEFT JOIN funnel fu USING (report_date)
            LEFT JOIN advertising ad USING (report_date)
            ORDER BY c.report_date
            """,
            (OZON_SOURCE_FILE,),
        )
        ozon_loaded_rows = cur.rowcount
        print(
            f"ПРОГРЕСС: 2/4 (50.0%) | Ozon факт синхронизирован | "
            f"deleted={replaced_rows} loaded={ozon_loaded_rows} errors=0",
            flush=True,
        )

        wb_deleted_rows = 0
        wb_loaded_rows = 0
        if relation_exists(cur, "public.wb_api_entities") and relation_exists(cur, "public.wb_funnel_daily"):
            cur.execute("DELETE FROM public.planfact_daily WHERE marketplace = 'wb'")
            wb_deleted_rows = cur.rowcount
            cur.execute(
                """
                WITH orders AS (
                    SELECT
                        report_date,
                        coalesce(sum(ordered_amount_rub), 0) AS orders_rub
                    FROM public.wb_funnel_daily
                    WHERE report_date IS NOT NULL
                    GROUP BY report_date
                ),
                sales AS (
                    SELECT
                        record_date AS report_date,
                        coalesce(sum(nullif(payload->>'priceWithDisc', '')::numeric), 0) AS sales_rub
                    FROM public.wb_api_entities
                    WHERE source_key = 'statistics.sales'
                      AND record_date IS NOT NULL
                    GROUP BY record_date
                ),
                advertising AS (
                    SELECT
                        (day_payload->>'date')::date AS report_date,
                        coalesce(sum(nullif(day_payload->>'sum', '')::numeric), 0) AS ad_spend_rub
                    FROM public.wb_api_entities
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(payload->'days') = 'array' THEN payload->'days'
                            ELSE '[]'::jsonb
                        END
                    ) AS day_payload
                    WHERE source_key = 'promotion.fullstats'
                      AND nullif(day_payload->>'date', '') IS NOT NULL
                    GROUP BY (day_payload->>'date')::date
                ),
                bounds AS (
                    SELECT min(report_date) AS date_from, max(report_date) AS date_to
                    FROM (
                        SELECT report_date FROM orders
                        UNION ALL SELECT report_date FROM sales
                        UNION ALL SELECT report_date FROM advertising
                    ) source_dates
                ),
                calendar AS (
                    SELECT generate_series(date_from, date_to, interval '1 day')::date AS report_date
                    FROM bounds
                    WHERE date_from IS NOT NULL AND date_to IS NOT NULL
                )
                INSERT INTO public.planfact_daily (
                    marketplace, report_date, orders_rub, sales_rub, ad_spend_rub,
                    source_file, source_sheet, source_row_num
                )
                SELECT
                    'wb',
                    c.report_date,
                    coalesce(o.orders_rub, 0),
                    coalesce(s.sales_rub, 0),
                    coalesce(a.ad_spend_rub, 0),
                    %s,
                    'API daily facts',
                    NULL
                FROM calendar c
                LEFT JOIN orders o USING (report_date)
                LEFT JOIN sales s USING (report_date)
                LEFT JOIN advertising a USING (report_date)
                ORDER BY c.report_date
                """,
                (WB_SOURCE_FILE,),
            )
            wb_loaded_rows = cur.rowcount
        print(
            f"ПРОГРЕСС: 3/4 (75.0%) | WB факт синхронизирован | "
            f"deleted={wb_deleted_rows} loaded={wb_loaded_rows} errors=0",
            flush=True,
        )

        cur.execute(DROP_VIEWS)
        cur.execute(CREATE_VIEWS)
        cur.execute(
            """
            SELECT
                marketplace,
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                count(*) AS rows,
                coalesce(sum(orders_rub), 0) AS orders_rub,
                coalesce(sum(sales_rub), 0) AS sales_rub,
                coalesce(sum(ad_spend_rub), 0) AS ad_spend_rub
            FROM public.planfact_daily
            WHERE marketplace IN ('ozon', 'wb')
            GROUP BY marketplace
            ORDER BY marketplace
            """
        )
        summaries = {row["marketplace"]: dict(row) for row in cur.fetchall()}
        conn.commit()

    elapsed = time.monotonic() - started
    print(
        f"ПРОГРЕСС: 4/4 (100.0%) | 2 materialized views готовы | errors=0 | "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )
    print(
        "ИТОГ: "
        + " | ".join(
            f"{marketplace}: rows={summary['rows']} "
            f"period={summary['date_from']}..{summary['date_to']} "
            f"orders_rub={summary['orders_rub']} sales_rub={summary['sales_rub']} "
            f"ad_spend_rub={summary['ad_spend_rub']}"
            for marketplace, summary in summaries.items()
        )
        + " | errors=0 partial=no | "
        f"output_db={expected_db} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return summaries


if __name__ == "__main__":
    rebuild()
