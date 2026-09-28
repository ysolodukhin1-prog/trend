"""Marketplace-level WB search-demand comparison for the Sportmaster SEO report."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable


MARKET_SEARCH_VIEW = "mv_wb_market_search_category_query_period"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def build_market_search_comparison(
    cur: Any,
    params: dict[str, list[str]],
    date_from: str,
    date_to: str,
    client_key: str,
    *,
    relation_exists: Callable[[Any, str], bool],
    category_rank_source: Callable[..., tuple[str, list[Any]]],
    frequency_selection_cte: Callable[..., tuple[str, list[Any]]],
    selected_frequency_tiers: Callable[[dict[str, list[str]]], list[str]],
    repeated_values: Callable[[dict[str, list[str]], str], list[str]],
    category_frequency_rank_ctes: str,
) -> dict[str, Any]:
    """Return apples-to-apples keyword and demand coverage for the latest Serp period."""
    if client_key != "sportmaster" or not relation_exists(cur, MARKET_SEARCH_VIEW):
        return {"available": False}

    cur.execute(
        f"""
        SELECT report_date_from, report_date_to
        FROM (
            SELECT DISTINCT report_date_from, report_date_to
            FROM public.{MARKET_SEARCH_VIEW}
            WHERE report_date_from <= %s::date AND report_date_to >= %s::date
        ) periods
        ORDER BY
            CASE
                WHEN report_date_from >= %s::date AND report_date_to <= %s::date
                THEN 0 ELSE 1
            END,
            report_date_to DESC,
            report_date_from DESC
        LIMIT 1
        """,
        [date_to, date_from, date_from, date_to],
    )
    period = cur.fetchone()
    if not period:
        return {"available": False}
    market_date_from = period["report_date_from"].isoformat()
    market_date_to = period["report_date_to"].isoformat()

    category_source_ctes, category_values = category_rank_source(
        params,
        market_date_from,
        market_date_to,
        relation_exists(cur, "mv_wb_search_category_query_daily"),
        client_key,
    )
    frequency_sql, frequency_values = frequency_selection_cte(params)

    platform_clauses = [
        "p.report_date_from = %s::date",
        "p.report_date_to = %s::date",
    ]
    platform_values: list[Any] = [market_date_from, market_date_to]
    categories = repeated_values(params, "categories")
    if categories:
        platform_clauses.append("p.subject_name = ANY(%s)")
        platform_values.append(categories)
    tiers = selected_frequency_tiers(params)
    if tiers:
        platform_clauses.append("p.frequency_tier = ANY(%s)")
        platform_values.append(tiers)
    search_query = params.get("q", [""])[0].strip()
    if search_query:
        platform_clauses.append("p.search_query ILIKE %s")
        platform_values.append(f"%{search_query}%")

    cur.execute(
        f"""
        WITH
        {category_source_ctes},
        {category_frequency_rank_ctes},
        {frequency_sql},
        own_queries AS MATERIALIZED (
            SELECT DISTINCT
                subject_name,
                regexp_replace(
                    replace(lower(trim(search_query)), 'ё', 'е'),
                    '[[:space:]]+', ' ', 'g'
                ) AS search_query_key
            FROM selected_frequency_queries
        ),
        platform AS MATERIALIZED (
            SELECT *
            FROM public.{MARKET_SEARCH_VIEW} p
            WHERE {' AND '.join(platform_clauses)}
        )
        SELECT
            count(*)::bigint AS platform_query_count,
            count(*) FILTER (WHERE o.search_query_key IS NOT NULL)::bigint
                AS covered_platform_query_count,
            coalesce(sum(p.query_count), 0)::bigint AS platform_search_demand,
            coalesce(sum(p.query_count_prev), 0)::bigint AS platform_search_demand_prev,
            coalesce(sum(p.query_count) FILTER (WHERE o.search_query_key IS NOT NULL), 0)::bigint
                AS covered_platform_search_demand,
            coalesce(sum(p.query_count_prev) FILTER (WHERE o.search_query_key IS NOT NULL), 0)::bigint
                AS covered_platform_search_demand_prev,
            count(*) FILTER (WHERE p.frequency_tier = 'ВЧ')::bigint
                AS platform_high_frequency_query_count,
            count(*) FILTER (WHERE p.frequency_tier = 'СЧ')::bigint
                AS platform_medium_frequency_query_count,
            count(*) FILTER (WHERE p.frequency_tier = 'НЧ')::bigint
                AS platform_low_frequency_query_count
        FROM platform p
        LEFT JOIN own_queries o
          ON o.subject_name = p.subject_name
         AND o.search_query_key = p.search_query_key
        """,
        list(category_values) + list(frequency_values) + platform_values,
    )
    row = {key: _normalize_value(value) for key, value in dict(cur.fetchone()).items()}
    row.update(
        {
            "available": True,
            "report_date_from": market_date_from,
            "report_date_to": market_date_to,
            "query_coverage_pct": _ratio(
                row.get("covered_platform_query_count"),
                row.get("platform_query_count"),
            ),
            "demand_coverage_pct": _ratio(
                row.get("covered_platform_search_demand"),
                row.get("platform_search_demand"),
            ),
        }
    )
    return row


def build_market_metric_group(comparison: dict[str, Any]) -> dict[str, Any] | None:
    if not comparison.get("available"):
        return None
    date_from = datetime.strptime(comparison["report_date_from"], "%Y-%m-%d").strftime("%d.%m.%Y")
    date_to = datetime.strptime(comparison["report_date_to"], "%Y-%m-%d").strftime("%d.%m.%Y")

    def item(key: str, label: str, format_name: str = "compact") -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "format": format_name,
            "digits": 1 if format_name == "pct" else 0,
            "value": float(comparison.get(key) or 0),
        }

    demand = item("platform_search_demand", "Частотность площадки")
    demand["previous"] = float(comparison.get("platform_search_demand_prev") or 0)
    demand["change_value"] = _pct_change(demand["value"], demand["previous"])
    demand["change_suffix"] = "%"
    covered_demand = item(
        "covered_platform_search_demand",
        "Частотность охваченных ключей",
    )
    covered_demand["previous"] = float(
        comparison.get("covered_platform_search_demand_prev") or 0
    )
    covered_demand["change_value"] = _pct_change(
        covered_demand["value"], covered_demand["previous"]
    )
    covered_demand["change_suffix"] = "%"

    return {
        "key": "platform_comparison",
        "title": f"Сравнение с площадкой · {date_from}—{date_to}",
        "items": [
            item("platform_query_count", "Ключей на площадке"),
            item("covered_platform_query_count", "Ключей охвачено товарами"),
            item("query_coverage_pct", "Охват ключей площадки", "pct"),
            demand,
            covered_demand,
            item("demand_coverage_pct", "Охват частотности", "pct"),
            item("platform_high_frequency_query_count", "Ключи площадки: ВЧ"),
            item("platform_medium_frequency_query_count", "Ключи площадки: СЧ"),
            item("platform_low_frequency_query_count", "Ключи площадки: НЧ"),
        ],
    }


def _ratio(value: Any, base: Any) -> float:
    denominator = float(base or 0)
    return round(float(value or 0) / denominator * 100, 2) if denominator else 0.0


def _pct_change(current: Any, previous: Any) -> float | None:
    previous_value = float(previous or 0)
    if not previous_value:
        return None
    return round((float(current or 0) / previous_value - 1) * 100, 1)
