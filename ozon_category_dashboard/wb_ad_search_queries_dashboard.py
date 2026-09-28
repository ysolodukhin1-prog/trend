#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only API payload for the shared WB advertising search-query dashboard."""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

import psycopg2


SORT_COLUMNS = {
    "spend_rub": "spend_rub",
    "views": "views",
    "clicks": "clicks",
    "orders": "orders",
    "ordered_units": "ordered_units",
    "ctr_pct": "ctr_pct",
    "cpc_rub": "cpc_rub",
    "cvr_pct": "cvr_pct",
    "atbs": "atbs",
    "atb_rate_pct": "atb_rate_pct",
    "cpo_rub": "cpo_rub",
    "avg_position": "avg_position",
    "norm_query": "norm_query",
    "campaign_name": "campaign_name",
    "product_name": "product_name",
    "bid_rub": "bid_rub",
    "status_label": "status_label",
    "recommendation": "recommendation",
}

TABLE_FILTER_COLUMNS = {
    "product_name": ("product_name", False),
    "campaign_name": ("campaign_name", False),
    "norm_query": ("norm_query", False),
    "spend_rub": ("spend_rub", True),
    "views": ("views", True),
    "clicks": ("clicks", True),
    "ctr_pct": ("ctr_pct", True),
    "cpc_rub": ("cpc_rub", True),
    "atbs": ("atbs", True),
    "cvr_pct": ("cvr_pct", True),
    "atb_rate_pct": ("atb_rate_pct", True),
    "orders": ("orders", True),
    "cpo_rub": ("cpo_rub", True),
    "avg_position": ("avg_position", True),
    "bid_rub": ("bid_rub", True),
    "status_label": ("status_label", False),
    "recommendation": ("recommendation", False),
}

CAMPAIGN_STATUSES = {9: "Активные", 11: "На паузе", 7: "Завершённые", 4: "Готовы к запуску", 8: "Отклонённые", -1: "Удаляются"}


def campaign_metadata(cur):
    """Current campaign snapshot, never inferred from historical activity."""
    if not _relation_exists(cur, "wb_api_entities"):
        return {}
    cur.execute("""SELECT DISTINCT ON (payload->>'id') payload, captured_at
        FROM public.wb_api_entities WHERE source_key='promotion.campaigns'
        AND payload->>'id' IS NOT NULL ORDER BY payload->>'id', captured_at DESC""")
    result = {}
    for raw, captured in cur.fetchall():
        code = raw.get("status")
        key = str(code) if code is not None else "unknown"
        result[_int(raw.get("id"))] = {
            "campaign_status": key,
            "campaign_status_label": CAMPAIGN_STATUSES.get(_int(code, -999), "Статус неизвестен"),
            "campaign_captured_at": captured.isoformat() if captured else None,
            "bids": {_int(p.get("nm_id")): (p.get("bids_kopecks") or {}).get("search") for p in (raw.get("nm_settings") or [])},
        }
    return result


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key) or []
    return str(values[0]).strip() if values else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _table_filter_sql(query: dict[str, list[str]]) -> tuple[str, list[Any]]:
    raw = _first(query, "column_filters")
    if not raw:
        return "", []
    try:
        items = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "", []
    if isinstance(items, dict):
        items = [
            {"column": column, **config}
            for column, config in items.items()
            if isinstance(config, dict)
        ]
    if not isinstance(items, list):
        return "", []

    conditions: list[str] = []
    values: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        op = str(item.get("op") or "").strip()
        value = str(item.get("value") or "").strip()
        spec = TABLE_FILTER_COLUMNS.get(column)
        if not spec or not value:
            continue
        expression, numeric = spec
        if numeric:
            try:
                typed_value: Any = float(value.replace(",", "."))
                if not math.isfinite(typed_value):
                    continue
            except ValueError:
                continue
            operator = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(op)
            if operator:
                conditions.append(f"{expression} {operator} %s")
                values.append(typed_value)
        else:
            operator = {"contains": "ILIKE", "not_contains": "NOT ILIKE", "eq": "=", "neq": "<>"}.get(op)
            if operator:
                conditions.append(f"coalesce({expression}::text, '') {operator} %s")
                values.append(f"%{value}%" if op in {"contains", "not_contains"} else value)
    return ("WHERE " + " AND ".join(conditions) if conditions else ""), values


def _row_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in zip(columns, row):
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            value = None
        payload[key] = value
    return payload


def _relation_exists(cur: Any, relation: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
    return cur.fetchone()[0] is not None


def _source_relation(cur: Any) -> str:
    if _relation_exists(cur, "mv_wb_ad_search_query_daily"):
        return "public.mv_wb_ad_search_query_daily"
    if _relation_exists(cur, "mv_konstex_wb_ad_search_query_daily"):
        return "public.mv_konstex_wb_ad_search_query_daily"
    if _relation_exists(cur, "konstex_wb_ad_search_query_daily"):
        return "public.konstex_wb_ad_search_query_daily"
    raise RuntimeError(
        "Рекламные поисковые запросы ещё не загружены. "
        "Запустите импорт поисковых запросов рекламы WB в админке."
    )


def _unavailable(message: str) -> dict[str, Any]:
    return {
        "ok": True,
        "available": False,
        "data_status": "unavailable",
        "reason_code": "source_not_loaded",
        "message": message,
        "period": {"date_from": "", "date_to": "", "available_from": "", "available_to": ""},
        "filters": {"campaigns": [], "products": [], "statuses": [], "payment_types": [], "bid_types": [], "conversions": []},
        "summary": {},
        "daily": [],
        "rankings": {},
        "rows": [],
        "page": 1,
        "page_size": 50,
        "total": 0,
        "total_pages": 1,
        "notes": [],
    }


def payload(
    get_conn: Callable[[], Any],
    query: dict[str, list[str]],
) -> dict[str, Any]:
    marketplace = _first(query, "marketplace", "wb")
    if marketplace == "ozon":
        import sys
        # app.py can run as __main__; importing app would create a second,
        # unhydrated registry and lose the request's client ContextVar.
        app = sys.modules[get_conn.__module__]
        from ozon_ad_search_queries import payload as ozon_payload
        return ozon_payload(app, query)
    if marketplace != "wb":
        result = _unavailable("Яндекс Маркет не предоставляет подтверждённый рекламный отчёт по поисковым фразам в подключённом API. Доступны отдельные отчёты по бусту и общая аналитика запросов.")
        result.update(marketplace=marketplace, reason_code="query_grain_unavailable")
        return result
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
        try:
            source = _source_relation(cur)
        except RuntimeError as exc:
            return _unavailable(str(exc))
        cur.execute(f"SELECT min(report_date), max(report_date) FROM {source}")
        available_from, available_to = cur.fetchone()
        if available_to is None:
            return _unavailable("WB не вернул ни одной строки статистики поисковых кластеров.")

        requested_from = _date(_first(query, "date_from"))
        requested_to = _date(_first(query, "date_to"))
        date_to = min(requested_to or available_to, available_to)
        date_from = requested_from or max(available_from, date_to - timedelta(days=29))
        date_from = max(date_from, available_from)
        if date_from > date_to:
            date_from = max(available_from, date_to - timedelta(days=29))

        campaign_id = _int(_first(query, "campaign_id"))
        nm_id = _int(_first(query, "nm_id"))
        search_text = _first(query, "query").lower()
        status_filter = _first(query, "status").lower()
        payment_type = _first(query, "payment_type").lower()
        bid_type = _first(query, "bid_type").lower()
        conversion_filter = _first(query, "conversion").lower()
        page = max(1, _int(_first(query, "page"), 1))
        page_size = min(300, max(10, _int(_first(query, "page_size"), 50)))
        sort_key = _first(query, "sort", "spend_rub")
        sort_column = SORT_COLUMNS.get(sort_key, "spend_rub")
        sort_dir = "asc" if _first(query, "sort_dir", "desc").lower() == "asc" else "desc"

        metadata = campaign_metadata(cur)
        campaign_ids = [_int(v) for v in _first(query, "campaign_ids").split(",") if _int(v)]
        campaign_statuses = [v for v in _first(query, "campaign_status").split(",") if v]
        where = ["report_date BETWEEN %s AND %s"]
        params: list[Any] = [date_from, date_to]
        if campaign_id:
            where.append("advert_id = %s")
            params.append(campaign_id)
        if campaign_ids:
            where.append("advert_id = ANY(%s)")
            params.append(campaign_ids)
        if campaign_statuses:
            matched = [key for key, value in metadata.items() if value["campaign_status"] in campaign_statuses]
            if "unknown" in campaign_statuses:
                where.append("(advert_id = ANY(%s) OR NOT (advert_id = ANY(%s)))")
                params.extend([matched, list(metadata)])
            else:
                where.append("advert_id = ANY(%s)")
                params.append(matched)
        if nm_id:
            where.append("nm_id = %s")
            params.append(nm_id)
        if search_text:
            where.append("lower(norm_query) LIKE %s")
            params.append(f"%{search_text}%")
        if payment_type:
            where.append("lower(coalesce(payment_type, '')) = %s")
            params.append(payment_type)
        if bid_type:
            where.append("lower(coalesce(bid_type, '')) = %s")
            params.append(bid_type)
        where_sql = " AND ".join(where)
        daily_where_sql = re.sub(r"\b(report_date|advert_id|nm_id|norm_query|payment_type|bid_type)\b", r"d.\1", where_sql)

        cluster_view = _first(query, "group_by") == "cluster"
        grouped_cte = f"""
            WITH placements AS (
              SELECT
                advert_id,
                coalesce(nullif(max(campaign_name), ''), 'Кампания ' || advert_id::text) AS campaign_name,
                nm_id,
                coalesce(nullif(max(product_name), ''), 'WB ' || nm_id::text) AS product_name,
                coalesce(nullif(max(seller_article), ''), '') AS seller_article,
                norm_query,
                coalesce(nullif(max(payment_type), ''), 'unknown') AS payment_type,
                coalesce(nullif(max(bid_type), ''), 'unknown') AS bid_type,
                sum(coalesce(views_qty, 0))::bigint AS views,
                sum(coalesce(clicks_qty, 0))::bigint AS clicks,
                sum(coalesce(atb_qty, 0))::bigint AS atbs,
                sum(coalesce(orders_qty, 0))::bigint AS orders,
                sum(coalesce(ordered_units_qty, 0))::bigint AS ordered_units,
                round(sum(coalesce(spend_rub, 0))::numeric, 2) AS spend_rub,
                round(
                  sum(coalesce(avg_position, 0) * greatest(coalesce(views_qty, 0), 1))
                  / nullif(sum(greatest(coalesce(views_qty, 0), 1)), 0),
                  2
                ) AS avg_position,
                max(bid_kopecks) AS bid_kopecks,
                bool_or(coalesce(is_excluded, false)) AS is_excluded,
                bool_or(coalesce(is_active, false)) AS is_active
              FROM {source}
              WHERE {where_sql}
              GROUP BY advert_id, nm_id, norm_query
            ),
            grouped AS (SELECT *, 1::bigint AS placement_count FROM placements),
            scored AS (
              SELECT
                *,
                round(clicks * 100.0 / nullif(views, 0), 2) AS ctr_pct,
                round(spend_rub / nullif(clicks, 0), 2) AS cpc_rub,
                round(orders * 100.0 / nullif(clicks, 0), 2) AS cvr_pct,
                round(atbs * 100.0 / nullif(clicks, 0), 2) AS atb_rate_pct,
                round(spend_rub / nullif(orders, 0), 2) AS cpo_rub,
                round(spend_rub / nullif(ordered_units, 0), 2) AS cpu_rub,
                round(bid_kopecks / 100.0, 2) AS bid_rub,
                CASE
                  WHEN is_excluded THEN 'excluded'
                  WHEN orders = 0 AND spend_rub >= 500 THEN 'waste'
                  WHEN clicks >= 20 AND orders = 0 THEN 'no_conversion'
                  WHEN views >= 500 AND clicks * 100.0 / nullif(views, 0) < 1 THEN 'low_ctr'
                  WHEN orders >= 2 THEN 'converting'
                  ELSE 'monitor'
                END AS status
              FROM grouped
            ),
            visible AS (
              SELECT
                *,
                CASE status
                  WHEN 'converting' THEN 'Конвертирует'
                  WHEN 'waste' THEN 'Расход без заказов'
                  WHEN 'no_conversion' THEN 'Клики без заказов'
                  WHEN 'low_ctr' THEN 'Низкий CTR'
                  WHEN 'monitor' THEN 'Накопить данные'
                  WHEN 'excluded' THEN 'Исключён'
                  ELSE status
                END AS status_label,
                CASE status
                  WHEN 'excluded' THEN 'Уже исключён из показов'
                  WHEN 'waste' THEN 'Проверить релевантность и добавить в минус-фразы либо снизить ставку'
                  WHEN 'no_conversion' THEN 'Есть клики без заказов: проверить карточку, цену и запрос'
                  WHEN 'low_ctr' THEN 'Низкий CTR: снизить ставку или улучшить главное фото и заголовок'
                  WHEN 'converting' THEN 'Запрос конвертирует: проверить запас и возможность повысить ставку'
                  ELSE 'Накопить больше данных'
                END AS recommendation
              FROM scored
            )
        """
        if cluster_view:
            grouped_cte = grouped_cte.replace(
                "grouped AS (SELECT *, 1::bigint AS placement_count FROM placements)",
                """grouped AS (SELECT 0::bigint advert_id, count(DISTINCT advert_id)::text || ' кампаний' campaign_name,
                0::bigint nm_id, count(DISTINCT nm_id)::text || ' товаров' product_name, ''::text seller_article,
                norm_query, ''::text payment_type, ''::text bid_type, sum(views)::bigint views,
                sum(clicks)::bigint clicks, sum(atbs)::bigint atbs, sum(orders)::bigint orders,
                sum(ordered_units)::bigint ordered_units, sum(spend_rub) spend_rub,
                NULL::numeric avg_position, NULL::numeric bid_kopecks, bool_and(is_excluded) is_excluded,
                bool_or(is_active) is_active, count(*)::bigint placement_count FROM placements GROUP BY norm_query)""")

        status_params: list[Any] = []
        status_conditions: list[str] = []
        if status_filter:
            status_conditions.append("status = %s")
            status_params.append(status_filter)
        if conversion_filter == "with_orders":
            status_conditions.append("orders > 0")
        elif conversion_filter == "without_orders":
            status_conditions.append("orders = 0")
        status_where = "WHERE " + " AND ".join(status_conditions) if status_conditions else ""
        table_filter_where, table_filter_params = _table_filter_sql(query)
        if table_filter_where:
            status_where += (" AND " if status_where else "WHERE ") + table_filter_where.removeprefix("WHERE ")
            status_params += table_filter_params

        cur.execute(
            grouped_cte
            + f"""
              SELECT
                round(coalesce(sum(spend_rub), 0), 2) AS spend_rub,
                coalesce(sum(views), 0)::bigint AS views,
                count(DISTINCT norm_query)::bigint AS query_count,
                coalesce(sum(placement_count), 0)::bigint AS placement_count,
                count(*) FILTER (WHERE orders > 0)::bigint AS converting_query_count,
                count(*) FILTER (WHERE orders = 0)::bigint AS zero_order_query_count,
                coalesce(sum(clicks), 0)::bigint AS clicks,
                coalesce(sum(atbs), 0)::bigint AS atbs,
                coalesce(sum(orders), 0)::bigint AS orders,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(coalesce(sum(clicks) * 100.0 / nullif(sum(views), 0), 0), 2) AS ctr_pct,
                round(coalesce(sum(spend_rub) / nullif(sum(clicks), 0), 0), 2) AS cpc_rub,
                round(coalesce(sum(spend_rub) / nullif(sum(orders), 0), 0), 2) AS cpo_rub,
                round(coalesce(sum(orders) * 100.0 / nullif(sum(clicks), 0), 0), 2) AS cvr_pct,
                round(coalesce(sum(spend_rub) FILTER (WHERE orders = 0), 0), 2) AS waste_spend_rub,
                round(
                  coalesce(sum(spend_rub) FILTER (WHERE orders = 0), 0) * 100.0
                  / nullif(sum(spend_rub), 0),
                  2
                ) AS waste_share_pct
              FROM visible
              {status_where}
            """,
            params + status_params,
        )
        summary_columns = [
            "spend_rub",
            "views",
            "query_count",
            "placement_count",
            "converting_query_count",
            "zero_order_query_count",
            "clicks",
            "atbs",
            "orders",
            "ordered_units",
            "ctr_pct",
            "cpc_rub",
            "cpo_rub",
            "cvr_pct",
            "waste_spend_rub",
            "waste_share_pct",
        ]
        summary = _row_dict(summary_columns, cur.fetchone())

        cur.execute(
            grouped_cte
            + f""",
            selected AS (
              SELECT advert_id, nm_id, norm_query
              FROM visible
              {status_where}
            ),
            daily_base AS (
              SELECT
                d.report_date AS date,
                round(sum(coalesce(d.spend_rub, 0))::numeric, 2) AS spend_rub,
                sum(coalesce(d.views_qty, 0))::bigint AS views,
                sum(coalesce(d.clicks_qty, 0))::bigint AS clicks,
                sum(coalesce(d.atb_qty, 0))::bigint AS atbs,
                sum(coalesce(d.orders_qty, 0))::bigint AS orders,
                sum(coalesce(d.ordered_units_qty, 0))::bigint AS ordered_units,
                round(
                  sum(coalesce(d.avg_position, 0) * greatest(coalesce(d.views_qty, 0), 1))
                  / nullif(sum(greatest(coalesce(d.views_qty, 0), 1)), 0),
                  2
                ) AS avg_position
              FROM {source} d
              JOIN selected q
                ON (q.advert_id = 0 OR q.advert_id = d.advert_id)
               AND (q.nm_id = 0 OR q.nm_id = d.nm_id)
               AND q.norm_query = d.norm_query
              WHERE {daily_where_sql}
              GROUP BY d.report_date
            )
            SELECT
              date,
              spend_rub,
              views,
              clicks,
              atbs,
              orders,
              ordered_units,
              round(coalesce(clicks * 100.0 / nullif(views, 0), 0), 2) AS ctr_pct,
              round(coalesce(spend_rub / nullif(clicks, 0), 0), 2) AS cpc_rub,
              round(coalesce(orders * 100.0 / nullif(clicks, 0), 0), 2) AS cvr_pct,
              round(coalesce(spend_rub / nullif(orders, 0), 0), 2) AS cpo_rub,
              avg_position
            FROM daily_base
            ORDER BY date
            """,
            params + status_params + params,
        )
        daily_columns = [
            "date", "spend_rub", "views", "clicks", "atbs", "orders",
            "ordered_units", "ctr_pct", "cpc_rub", "cvr_pct", "cpo_rub", "avg_position",
        ]
        daily = [_row_dict(daily_columns, row) for row in cur.fetchall()]
        table_filter_where, table_filter_params = "", []
        visible_where = status_where
        if table_filter_where:
            visible_where += (" AND " if visible_where else "WHERE ") + table_filter_where.removeprefix("WHERE ")
        cur.execute(grouped_cte + f"SELECT count(*) FROM visible {visible_where}", params + status_params + table_filter_params)
        total = int(cur.fetchone()[0] or 0)
        page = min(page, max(1, math.ceil(total / page_size)))
        offset = (page - 1) * page_size
        cur.execute(
            grouped_cte
            + f"""
              SELECT
                norm_query,
                advert_id,
                campaign_name,
                nm_id,
                product_name,
                seller_article,
                payment_type,
                bid_type,
                views,
                clicks,
                atbs,
                orders,
                ordered_units,
                spend_rub,
                ctr_pct,
                cpc_rub,
                cvr_pct,
                atb_rate_pct,
                cpo_rub,
                cpu_rub AS cost_per_ordered_unit_rub,
                avg_position,
                bid_rub,
                is_excluded,
                is_active,
                status,
                status_label,
                recommendation
              FROM visible
              {visible_where}
              ORDER BY {sort_column} {sort_dir} NULLS LAST, spend_rub DESC, norm_query, advert_id, nm_id
              LIMIT %s OFFSET %s
            """,
            params + status_params + table_filter_params + [page_size, offset],
        )
        row_columns = [
            "norm_query",
            "advert_id",
            "campaign_name",
            "nm_id",
            "product_name",
            "seller_article",
            "payment_type",
            "bid_type",
            "views",
            "clicks",
            "atbs",
            "orders",
            "ordered_units",
            "spend_rub",
            "ctr_pct",
            "cpc_rub",
            "cvr_pct",
            "atb_rate_pct",
            "cpo_rub",
            "cost_per_ordered_unit_rub",
            "avg_position",
            "bid_rub",
            "is_excluded",
            "is_active",
            "status",
            "status_label",
            "recommendation",
        ]
        rows = [_row_dict(row_columns, row) for row in cur.fetchall()]

        ranking_columns = [
            "norm_query", "campaign_name", "product_name", "spend_rub", "clicks",
            "orders", "ctr_pct", "cvr_pct", "cpo_rub", "avg_position",
        ]
        ranking_specs = {
            "top_spend": ("", "spend_rub DESC, clicks DESC"),
            "waste_spend": ("orders = 0 AND spend_rub > 0", "spend_rub DESC, clicks DESC"),
            "top_conversion": ("clicks >= 5 AND orders > 0", "cvr_pct DESC NULLS LAST, orders DESC, spend_rub DESC"),
            "anti_conversion": ("clicks >= 5", "cvr_pct ASC NULLS FIRST, spend_rub DESC"),
        }
        rankings: dict[str, list[dict[str, Any]]] = {}
        for ranking_key, (extra_condition, order_sql) in ranking_specs.items():
            ranking_where = status_where
            if extra_condition:
                ranking_where += (" AND " if ranking_where else "WHERE ") + extra_condition
            cur.execute(
                grouped_cte
                + f"""
                  SELECT
                    norm_query, campaign_name, product_name, spend_rub, clicks,
                    orders, ctr_pct, cvr_pct, cpo_rub, avg_position
                  FROM scored
                  {ranking_where}
                  ORDER BY {order_sql}, norm_query
                  LIMIT 5
                """,
                params + status_params,
            )
            rankings[ranking_key] = [_row_dict(ranking_columns, row) for row in cur.fetchall()]
        filter_params: list[Any] = [date_from, date_to]
        cur.execute(
            f"""
            SELECT DISTINCT advert_id,
                   coalesce(nullif(campaign_name, ''), 'Кампания ' || advert_id::text)
            FROM {source}
            WHERE report_date BETWEEN %s AND %s
            ORDER BY 2, 1
            """,
            filter_params,
        )
        campaigns = [{"id": int(row[0]), "name": row[1], **{k: v for k, v in metadata.get(int(row[0]), {"campaign_status": "unknown", "campaign_status_label": "Статус неизвестен"}).items() if k != "bids"}} for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT DISTINCT nm_id,
                   coalesce(nullif(product_name, ''), 'WB ' || nm_id::text)
            FROM {source}
            WHERE report_date BETWEEN %s AND %s
            ORDER BY 2, 1
            """,
            filter_params,
        )
        products = [{"nm_id": int(row[0]), "name": row[1]} for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT DISTINCT lower(coalesce(payment_type, ''))
            FROM {source}
            WHERE report_date BETWEEN %s AND %s AND coalesce(payment_type, '') <> ''
            ORDER BY 1
            """,
            filter_params,
        )
        payment_types = [str(row[0]) for row in cur.fetchall()]
        cur.execute(
            f"""
            SELECT DISTINCT lower(coalesce(bid_type, ''))
            FROM {source}
            WHERE report_date BETWEEN %s AND %s AND coalesce(bid_type, '') <> ''
            ORDER BY 1
            """,
            filter_params,
        )
        bid_types = [str(row[0]) for row in cur.fetchall()]

    for row in rows:
        meta = metadata.get(row["advert_id"], {})
        row.update({k: v for k, v in meta.items() if k != "bids"})
        row.setdefault("campaign_status", "unknown")
        row.setdefault("campaign_status_label", "Статус неизвестен")
        current_bid = meta.get("bids", {}).get(row["nm_id"])
        row["bid_rub"] = float(current_bid) / 100 if current_bid is not None else None
        if row["views"] == 0 and row["clicks"] > 0:
            row["views"] = None
            row["ctr_pct"] = None
    summary["atb_rate_pct"] = round(summary["atbs"] * 100 / summary["clicks"], 2) if summary.get("clicks") and summary.get("atbs") is not None else None
    if not summary.get("views") and summary.get("clicks"):
        summary["views"] = summary["ctr_pct"] = None
    for key, denominator in [("cpc_rub", "clicks"), ("cpo_rub", "orders"), ("cvr_pct", "clicks")]:
        if not summary.get(denominator):
            summary[key] = None
    return {
        "ok": True,
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "available_from": available_from.isoformat(),
            "available_to": available_to.isoformat(),
        },
        "filters": {
            "campaigns": campaigns,
            "products": products,
            "statuses": [
                "converting",
                "waste",
                "no_conversion",
                "low_ctr",
                "monitor",
                "excluded",
            ],
            "payment_types": payment_types,
            "bid_types": bid_types,
            "conversions": ["with_orders", "without_orders"],
        },
        "summary": summary,
        "daily": daily,
        "rankings": rankings,
        "rows": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, math.ceil(total / page_size)),
        "notes": [
            "WB отдаёт нормализованные поисковые кластеры, а не каждую исходную фразу покупателя.",
            "Точный расход, клики, заказы и CPO рассчитаны по данным Promotion API.",
            "Выручка по кластеру API не возвращается, поэтому ROAS и ДРР здесь не оцениваются.",
        ],
    }
