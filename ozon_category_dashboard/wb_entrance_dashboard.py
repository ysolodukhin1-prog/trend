"""Read-only API handlers for the Sportmaster WB entry-point dashboard."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import parse_qs

DETAIL_COLUMNS = [
    {"key": "section_name", "label": "Раздел", "type": "text"},
    {"key": "entry_point", "label": "Точка входа", "type": "text"},
    {"key": "sku_count", "label": "SKU", "type": "number"},
    {"key": "impressions", "label": "Показы", "type": "number"},
    {"key": "card_visits", "label": "Переходы в карточку", "type": "number"},
    {"key": "ctr_pct", "label": "CTR, %", "type": "number"},
    {"key": "cart_adds", "label": "Добавления в корзину", "type": "number"},
    {"key": "card_to_cart_pct", "label": "Конверсия в корзину, %", "type": "number"},
    {"key": "ordered_units", "label": "Заказы, шт", "type": "number"},
    {"key": "cart_to_order_pct", "label": "Конверсия в заказ, %", "type": "number"},
    {"key": "visit_to_order_pct", "label": "Переход → заказ, %", "type": "number"},
    {"key": "visit_share_pct", "label": "Доля переходов, %", "type": "number"},
    {"key": "order_share_pct", "label": "Доля заказов, %", "type": "number"},
]
ALLOWED_SORT = {column["key"] for column in DETAIL_COLUMNS}
SPORTMASTER_FILTERS = (
    "sm_subcategory", "sm_brand", "sm_model", "sm_gender",
    "sm_age", "sm_collection", "sm_season", "sm_sport",
)

METRIC_DEFINITIONS = [
    ("coverage", "Охват", [
        ("sku_count", "SKU в отчёте", "number"),
        ("section_count", "Разделы WB", "number"),
        ("entry_point_count", "Точки входа", "number"),
        ("impressions", "Показы", "compact"),
    ]),
    ("engagement", "Переходы и интерес", [
        ("card_visits", "Переходы в карточку", "compact"),
        ("ctr_pct", "CTR", "pct"),
        ("search_visit_share_pct", "Доля переходов из поиска", "pct"),
    ]),
    ("conversion", "Корзина и заказ", [
        ("cart_adds", "Добавления в корзину", "compact"),
        ("card_to_cart_pct", "Конверсия в корзину", "pct"),
        ("ordered_units", "Заказы", "compact"),
        ("cart_to_order_pct", "Конверсия корзина → заказ", "pct"),
        ("visit_to_order_pct", "Конверсия переход → заказ", "pct"),
        ("search_order_share_pct", "Доля заказов из поиска", "pct"),
    ]),
]


def repeated_values(params: dict[str, list[str]], key: str) -> list[str]:
    result: list[str] = []
    for raw in params.get(key, []):
        value = str(raw).strip()
        if value and value not in result:
            result.append(value)
    return result


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def normalize_row(row: Any) -> dict[str, Any]:
    return {key: normalize_value(value) for key, value in dict(row).items()}


def normalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [normalize_row(row) for row in rows]


def relation_exists(cur: Any, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS relation_name", (f"public.{name}",))
    return bool(cur.fetchone()["relation_name"])


def empty_filters() -> dict[str, Any]:
    return {
        "category_names": [],
        "product_names": [],
        "date_from": "",
        "date_to": "",
        "marketplaces": [{"id": "wb", "label": "Wildberries"}],
        "marketplace": "wb",
        "wb_entrance_section_values": [],
        "wb_entrance_point_values": [],
        "view": "public.mv_wb_entrance_daily_summary",
    }


def empty_dashboard(message: str = "Данные точек входа WB пока не импортированы") -> dict[str, Any]:
    return {
        "summary": {}, "metric_groups": [], "daily": [], "section_daily": [],
        "top_entry_points": [], "top_products": [], "rows": [], "columns": DETAIL_COLUMNS,
        "page": 1, "page_size": 50, "total": 0, "total_pages": 1,
        "sort_col": "ordered_units", "sort_dir": "desc", "date_from": "", "date_to": "",
        "data_note": message,
    }


def product_option(row: Any) -> str:
    item = normalize_row(row)
    parts = [f"WB {item.get('wb_sku')}", item.get("product_name") or "Без названия"]
    if item.get("seller_article"):
        parts.append(item["seller_article"])
    return " · ".join(str(part) for part in parts)


def append_sportmaster_filters(
    clauses: list[str], values: list[Any], params: dict[str, list[str]], sku_expr: str,
) -> None:
    if not any(repeated_values(params, key) for key in SPORTMASTER_FILTERS):
        return
    from app import sportmaster_filter_sql

    clauses.extend(sportmaster_filter_sql(params, values, "wb", sku_expr=sku_expr))


def raw_where(
    params: dict[str, list[str]], date_from: str, date_to: str, alias: str = "r",
) -> tuple[str, list[Any]]:
    clauses = [f"{alias}.report_date BETWEEN %s AND %s"]
    values: list[Any] = [date_from, date_to]
    categories = repeated_values(params, "categories")
    if categories:
        clauses.append(f"{alias}.subject_name = ANY(%s)")
        values.append(categories)
    sections = repeated_values(params, "wb_entrance_section")
    if sections:
        clauses.append(f"{alias}.section_name = ANY(%s)")
        values.append(sections)
    points = repeated_values(params, "wb_entrance_point")
    if points:
        clauses.append(f"{alias}.entry_point = ANY(%s)")
        values.append(points)
    product = params.get("product", [""])[0].strip()
    if product:
        prefix = product.split("·", 1)[0].strip()
        sku = prefix.removeprefix("WB ").strip() if prefix.startswith("WB ") else ""
        if sku.isdigit():
            clauses.append(f"{alias}.wb_sku = %s")
            values.append(int(sku))
        else:
            clauses.append(
                f"({alias}.product_name ILIKE %s OR {alias}.seller_article ILIKE %s OR {alias}.wb_sku::text ILIKE %s)"
            )
            values.extend([f"%{product}%"] * 3)
    article = params.get("article", [""])[0].strip()
    if article:
        clauses.append(
            f"({alias}.seller_article ILIKE %s OR {alias}.wb_sku::text ILIKE %s OR {alias}.product_name ILIKE %s)"
        )
        values.extend([f"%{article}%"] * 3)
    append_sportmaster_filters(clauses, values, params, f"{alias}.wb_sku")
    return " AND ".join(clauses), values


def handle_filters(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, Any]:
    payload = empty_filters()
    if client_key != "sportmaster":
        return payload
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "wb_entrance_daily"):
            return payload
        cur.execute(
            """
            SELECT min(report_date) AS date_from, max(report_date) AS date_to,
                array_remove(array_agg(DISTINCT subject_name ORDER BY subject_name), NULL) AS categories,
                array_remove(array_agg(DISTINCT section_name ORDER BY section_name), NULL) AS sections,
                array_remove(array_agg(DISTINCT entry_point ORDER BY entry_point), NULL) AS points
            FROM public.wb_entrance_daily
            """
        )
        row = cur.fetchone()
        payload["date_from"] = normalize_value(row["date_from"])
        payload["date_to"] = normalize_value(row["date_to"])
        payload["category_names"] = row["categories"] or []
        payload["wb_entrance_section_values"] = row["sections"] or []
        payload["wb_entrance_point_values"] = row["points"] or []
        from app import add_sportmaster_filter_options

        add_sportmaster_filter_options(cur, payload, "wb", parsed.query)
    return payload


def handle_products(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, list[str]]:
    if client_key != "sportmaster":
        return {"product_names": []}
    params = parse_qs(parsed.query)
    date_from = params.get("date_from", ["1900-01-01"])[0]
    date_to = params.get("date_to", ["2999-12-31"])[0]
    where, values = raw_where(params, date_from, date_to)
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "wb_entrance_daily"):
            return {"product_names": []}
        cur.execute(
            f"""
            SELECT wb_sku, max(product_name) AS product_name, max(seller_article) AS seller_article
            FROM public.wb_entrance_daily r WHERE {where}
            GROUP BY wb_sku ORDER BY lower(max(product_name)), wb_sku LIMIT 150
            """,
            values,
        )
        return {"product_names": [product_option(row) for row in cur.fetchall()]}


def metric_groups(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "title": title,
            "items": [
                {"key": field, "label": label, "format": format_name, "value": summary.get(field) or 0}
                for field, label, format_name in items
            ],
        }
        for key, title, items in METRIC_DEFINITIONS
    ]


def int_param(params: dict[str, list[str]], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(params.get(key, [str(default)])[0]), maximum))
    except (TypeError, ValueError):
        return default



def has_dashboard_filters(params: dict[str, list[str]]) -> bool:
    repeated_keys = (
        "categories", "wb_entrance_section", "wb_entrance_point", *SPORTMASTER_FILTERS,
    )
    return (
        any(repeated_values(params, key) for key in repeated_keys)
        or bool(params.get("product", [""])[0].strip())
        or bool(params.get("article", [""])[0].strip())
    )


def handle_full_period_dashboard(
    cur: Any,
    page_size: int,
    page: int,
    sort_col: str,
    sort_dir: str,
    direction: str,
    nulls: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
            (SELECT count(*) FROM public.mv_wb_entrance_product_period)::bigint AS sku_count,
            (SELECT count(DISTINCT section_name) FROM public.mv_wb_entrance_entry_period)::bigint AS section_count,
            (SELECT count(*) FROM public.mv_wb_entrance_entry_period)::bigint AS entry_point_count,
            coalesce(sum(impressions), 0)::bigint AS impressions,
            coalesce(sum(card_visits), 0)::bigint AS card_visits,
            coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
            coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
            round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
            round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
            round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
            round(sum(ordered_units)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS visit_to_order_pct,
            round((
                SELECT coalesce(sum(card_visits), 0)
                FROM public.mv_wb_entrance_entry_period WHERE section_name = 'Поиск'
            )::numeric / nullif(sum(card_visits), 0) * 100, 2) AS search_visit_share_pct,
            round((
                SELECT coalesce(sum(ordered_units), 0)
                FROM public.mv_wb_entrance_entry_period WHERE section_name = 'Поиск'
            )::numeric / nullif(sum(ordered_units), 0) * 100, 2) AS search_order_share_pct
        FROM public.mv_wb_entrance_daily_summary
        """
    )
    summary = normalize_row(cur.fetchone())

    cur.execute(
        """
        SELECT * FROM public.mv_wb_entrance_daily_summary
        ORDER BY report_date
        """
    )
    daily = normalize_rows(cur.fetchall())

    labeled_cte = """
        WITH totals AS (
            SELECT sum(card_visits) AS card_visits, sum(ordered_units) AS ordered_units
            FROM public.mv_wb_entrance_entry_period
        ), labeled AS (
            SELECT e.*,
                round(e.card_visits::numeric / nullif(t.card_visits, 0) * 100, 2) AS visit_share_pct,
                round(e.ordered_units::numeric / nullif(t.ordered_units, 0) * 100, 2) AS order_share_pct
            FROM public.mv_wb_entrance_entry_period e CROSS JOIN totals t
        )
    """
    cur.execute(
        labeled_cte
        + """
        SELECT * FROM labeled
        ORDER BY ordered_units DESC, card_visits DESC, impressions DESC
        LIMIT 12
        """
    )
    top_entry_points = normalize_rows(cur.fetchall())

    cur.execute(
        """
        SELECT report_date, section_name,
            coalesce(sum(impressions), 0)::bigint AS impressions,
            coalesce(sum(card_visits), 0)::bigint AS card_visits,
            coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
            coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
            round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
            round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
            round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct
        FROM public.mv_wb_entrance_entry_daily
        GROUP BY report_date, section_name
        ORDER BY report_date, section_name
        """
    )
    section_daily = normalize_rows(cur.fetchall())

    cur.execute(
        """
        SELECT * FROM public.mv_wb_entrance_product_period
        ORDER BY ordered_units DESC, card_visits DESC, impressions DESC
        LIMIT 12
        """
    )
    top_products = normalize_rows(cur.fetchall())

    cur.execute("SELECT count(*) AS total FROM public.mv_wb_entrance_entry_period")
    total = int(cur.fetchone()["total"] or 0)
    total_pages = max(1, math.ceil(total / page_size))
    page = min(page, total_pages)
    cur.execute(
        labeled_cte
        + f"""
        SELECT * FROM labeled
        ORDER BY {sort_col} {direction} {nulls}, section_name, entry_point
        LIMIT %s OFFSET %s
        """,
        [page_size, (page - 1) * page_size],
    )
    rows = normalize_rows(cur.fetchall())
    return {
        "summary": summary,
        "metric_groups": metric_groups(summary),
        "daily": daily,
        "section_daily": section_daily,
        "top_entry_points": top_entry_points,
        "top_products": top_products,
        "rows": rows,
        "columns": DETAIL_COLUMNS,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        "date_from": date_from,
        "date_to": date_to,
        "data_note": (
            "Источник — детальный лист WB на зерне дата × раздел × точка входа × SKU. "
            "CTR и конверсии пересчитаны из аддитивных показов, переходов, корзин и заказов; "
            "каждый файл перед загрузкой сверен с агрегированным листом. "
            "Полный период обслуживается точными периодными витринами."
        ),
    }


def handle_dashboard(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, Any]:
    if client_key != "sportmaster":
        return empty_dashboard("Отчет доступен только для Спортмастера")
    params = parse_qs(parsed.query)
    page_size = int_param(params, "limit", 50, 10, 200)
    page = int_param(params, "page", 1, 1, 1_000_000)
    sort_col = params.get("sort_col", ["ordered_units"])[0]
    if sort_col not in ALLOWED_SORT:
        sort_col = "ordered_units"
    default_dir = "asc" if sort_col in {"section_name", "entry_point"} else "desc"
    sort_dir = "asc" if params.get("sort_dir", [default_dir])[0].lower() == "asc" else "desc"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    nulls = "NULLS FIRST" if sort_dir == "asc" else "NULLS LAST"

    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "wb_entrance_daily"):
            return empty_dashboard()
        cur.execute("SELECT min(report_date) AS date_from, max(report_date) AS date_to FROM public.wb_entrance_daily")
        bounds = cur.fetchone()
        full_date_from = bounds["date_from"].isoformat()
        full_date_to = bounds["date_to"].isoformat()
        date_from = params.get("date_from", [""])[0].strip() or full_date_from
        date_to = params.get("date_to", [""])[0].strip() or full_date_to
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        period_views = ("mv_wb_entrance_entry_period", "mv_wb_entrance_product_period")
        if (
            date_from == full_date_from
            and date_to == full_date_to
            and not has_dashboard_filters(params)
            and all(relation_exists(cur, view) for view in period_views)
        ):
            return handle_full_period_dashboard(
                cur, page_size, page, sort_col, sort_dir, direction, nulls, date_from, date_to,
            )
        where, values = raw_where(params, date_from, date_to)
        with_base = f"WITH base AS MATERIALIZED (SELECT * FROM public.wb_entrance_daily r WHERE {where})"

        cur.execute(
            f"""
            {with_base}
            SELECT count(DISTINCT wb_sku)::bigint AS sku_count,
                count(DISTINCT section_name)::bigint AS section_count,
                count(DISTINCT (section_name, entry_point))::bigint AS entry_point_count,
                coalesce(sum(impressions), 0)::bigint AS impressions,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
                round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
                round(sum(ordered_units)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS visit_to_order_pct,
                round(sum(card_visits) FILTER (WHERE section_name = 'Поиск')::numeric / nullif(sum(card_visits), 0) * 100, 2) AS search_visit_share_pct,
                round(sum(ordered_units) FILTER (WHERE section_name = 'Поиск')::numeric / nullif(sum(ordered_units), 0) * 100, 2) AS search_order_share_pct
            FROM base
            """,
            values,
        )
        summary = normalize_row(cur.fetchone())

        cur.execute(
            f"""
            {with_base}
            SELECT report_date,
                count(DISTINCT wb_sku)::bigint AS sku_count,
                count(DISTINCT section_name)::bigint AS section_count,
                count(DISTINCT (section_name, entry_point))::bigint AS entry_point_count,
                coalesce(sum(impressions), 0)::bigint AS impressions,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
                round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
                round(sum(ordered_units)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS visit_to_order_pct,
                round(sum(card_visits) FILTER (WHERE section_name = 'Поиск')::numeric / nullif(sum(card_visits), 0) * 100, 2) AS search_visit_share_pct,
                round(sum(ordered_units) FILTER (WHERE section_name = 'Поиск')::numeric / nullif(sum(ordered_units), 0) * 100, 2) AS search_order_share_pct
            FROM base GROUP BY report_date ORDER BY report_date
            """,
            values,
        )
        daily = normalize_rows(cur.fetchall())

        period_sql = """
            SELECT section_name, entry_point,
                count(DISTINCT wb_sku)::bigint AS sku_count,
                coalesce(sum(impressions), 0)::bigint AS impressions,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
                round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
                round(sum(ordered_units)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS visit_to_order_pct
            FROM base GROUP BY section_name, entry_point
        """
        cur.execute(
            f"""
            {with_base}, period AS ({period_sql}), totals AS (
                SELECT sum(card_visits) AS card_visits, sum(ordered_units) AS ordered_units FROM period
            )
            SELECT p.*,
                round(p.card_visits::numeric / nullif(t.card_visits, 0) * 100, 2) AS visit_share_pct,
                round(p.ordered_units::numeric / nullif(t.ordered_units, 0) * 100, 2) AS order_share_pct
            FROM period p CROSS JOIN totals t
            ORDER BY p.ordered_units DESC, p.card_visits DESC, p.impressions DESC
            LIMIT 12
            """,
            values,
        )
        top_entry_points = normalize_rows(cur.fetchall())

        cur.execute(
            f"""
            {with_base}
            SELECT report_date, section_name,
                coalesce(sum(impressions), 0)::bigint AS impressions,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
                round(sum(cart_adds)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(sum(ordered_units)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct
            FROM base GROUP BY report_date, section_name ORDER BY report_date, section_name
            """,
            values,
        )
        section_daily = normalize_rows(cur.fetchall())

        cur.execute(
            f"""
            {with_base}
            SELECT wb_sku, max(seller_article) AS seller_article,
                max(product_name) AS product_name, max(subject_name) AS subject_name, max(brand) AS brand,
                count(DISTINCT (section_name, entry_point))::bigint AS entry_point_count,
                coalesce(sum(impressions), 0)::bigint AS impressions,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                round(sum(card_visits)::numeric / nullif(sum(impressions), 0) * 100, 2) AS ctr_pct,
                round(sum(ordered_units)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS visit_to_order_pct
            FROM base GROUP BY wb_sku
            ORDER BY ordered_units DESC, card_visits DESC, impressions DESC LIMIT 12
            """,
            values,
        )
        top_products = normalize_rows(cur.fetchall())

        cur.execute(f"{with_base}, period AS ({period_sql}) SELECT count(*) AS total FROM period", values)
        total = int(cur.fetchone()["total"] or 0)
        total_pages = max(1, math.ceil(total / page_size))
        page = min(page, total_pages)
        cur.execute(
            f"""
            {with_base}, period AS ({period_sql}), totals AS (
                SELECT sum(card_visits) AS card_visits, sum(ordered_units) AS ordered_units FROM period
            ), labeled AS (
                SELECT p.*,
                    round(p.card_visits::numeric / nullif(t.card_visits, 0) * 100, 2) AS visit_share_pct,
                    round(p.ordered_units::numeric / nullif(t.ordered_units, 0) * 100, 2) AS order_share_pct
                FROM period p CROSS JOIN totals t
            )
            SELECT * FROM labeled
            ORDER BY {sort_col} {direction} {nulls}, section_name, entry_point
            LIMIT %s OFFSET %s
            """,
            values + [page_size, (page - 1) * page_size],
        )
        rows = normalize_rows(cur.fetchall())

    return {
        "summary": summary,
        "metric_groups": metric_groups(summary),
        "daily": daily,
        "section_daily": section_daily,
        "top_entry_points": top_entry_points,
        "top_products": top_products,
        "rows": rows,
        "columns": DETAIL_COLUMNS,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        "date_from": date_from,
        "date_to": date_to,
        "data_note": (
            "Источник — детальный лист WB на зерне дата × раздел × точка входа × SKU. "
            "CTR и конверсии пересчитаны из аддитивных показов, переходов, корзин и заказов; "
            "каждый файл перед загрузкой сверен с агрегированным листом."
        ),
    }
