"""Read-only API handlers for the multi-client WB search-query dashboard."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import parse_qs


DETAIL_COLUMNS = [
    {"key": "search_query", "label": "Поисковый запрос", "type": "text"},
    {"key": "subject_name", "label": "Категория товара", "type": "text"},
    {"key": "brand_class_label", "label": "Брендовость", "type": "text"},
    {"key": "brand_name_label", "label": "Бренд", "type": "text"},
    {"key": "query_type_label", "label": "Тип запроса", "type": "text"},
    {"key": "audience_label", "label": "Аудитория", "type": "text"},
    {"key": "scenario_labels", "label": "Сценарий", "type": "text"},
    {"key": "specificity_label", "label": "Конкретность", "type": "text"},
    {"key": "frequency_tier", "label": "Ранг частотности", "type": "text"},
    {"key": "category_rank", "label": "Место в категории", "type": "number"},
    {"key": "search_demand", "label": "Количество запросов (частотность)", "type": "number"},
    {"key": "sku_count", "label": "SKU в выдаче, макс.", "type": "number"},
    {"key": "visibility_pct", "label": "Видимость, %", "type": "number"},
    {"key": "average_position", "label": "Средняя позиция", "type": "number"},
    {"key": "best_position", "label": "Лучшая позиция", "type": "number"},
    {"key": "card_visits", "label": "Переходы", "type": "number"},
    {"key": "cart_adds", "label": "В корзину", "type": "number"},
    {"key": "card_to_cart_pct", "label": "Конверсия в корзину, %", "type": "number"},
    {"key": "ordered_units", "label": "Заказы, шт", "type": "number"},
    {"key": "cart_to_order_pct", "label": "Конверсия в заказ, %", "type": "number"},
]

ALLOWED_SORT_COLUMNS = {column["key"] for column in DETAIL_COLUMNS}
SORT_COLUMN_EXPRESSIONS = {
    "brand_class_label": "brand_class",
    "brand_name_label": "brand_name",
    "query_type_label": "query_type",
    "audience_label": "audience",
    "scenario_labels": "scenario_tags",
    "specificity_label": "specificity",
    "frequency_tier": "frequency_tier_order",
}

BRAND_CLASS_LABELS = {
    "gloria_jeans": "Брендовый: Gloria Jeans",
    "sportmaster": "Брендовый: Спортмастер",
    "competitor": "Брендовый: другой бренд",
    "generic": "Без бренда",
    "konstex": "Брендовый: Konstex",
}
QUERY_TYPE_LABELS = {
    "brand": "Брендовый",
    "brand_category": "Бренд + категория",
    "category": "Категорийный",
    "category_attribute": "Категория + уточнение",
    "sku_article": "Артикул / SKU",
    "article": "Артикул / SKU",
    "generic": "Общий запрос",
    "other": "Прочее",
}
AUDIENCE_LABELS = {
    "women": "Женщины",
    "men": "Мужчины",
    "girls": "Девочки",
    "boys": "Мальчики",
    "children": "Дети",
    "baby": "Малыши",
    "teen": "Подростки",
    "unisex": "Унисекс",
    "multiple": "Несколько аудиторий",
    "unspecified": "Не указана",
    "general": "Общая аудитория",
}
SCENARIO_LABELS = {
    "school": "Школа",
    "office": "Офис",
    "sport": "Спорт",
    "home_sleep": "Дом / сон",
    "beach_swim": "Пляж / плавание",
    "festive": "Праздник / выход",
    "summer": "Лето",
    "winter": "Зима",
    "maternity": "Беременность / кормление",
    "running": "Бег",
    "football": "Футбол",
    "fitness": "Фитнес / тренировки",
    "tourism": "Туризм / кемпинг",
    "cycling": "Велоспорт",
    "winter_sport": "Зимний спорт",
}
SPECIFICITY_LABELS = {
    "head": "Короткий (1–2 слова)",
    "middle": "Средний (3–4 слова)",
    "long_tail": "Длинный хвост (5+ слов)",
}
FREQUENCY_TIER_VALUES = ("ВЧ", "СЧ", "НЧ")
FREQUENCY_TIER_LABELS = {
    "ВЧ": "ВЧ · высокочастотные",
    "СЧ": "СЧ · среднечастотные",
    "НЧ": "НЧ · низкочастотные",
}

RAW_REPORT_METRIC_FIELDS = {
    "card_rating", "review_rating", "query_count", "query_count_prev",
    "visibility_pct", "visibility_pct_prev", "average_position", "average_position_prev",
    "median_position", "median_position_prev", "card_visits", "card_visits_prev",
    "card_visits_competitor_percentile", "cart_adds", "cart_adds_prev",
    "cart_adds_competitor_percentile", "card_to_cart_pct", "card_to_cart_pct_prev",
    "card_to_cart_competitor_percentile", "ordered_units", "ordered_units_prev",
    "ordered_units_competitor_percentile", "cart_to_order_pct", "cart_to_order_pct_prev",
    "cart_to_order_competitor_percentile", "min_price_rub", "max_price_rub",
}

METRIC_GROUPS = [
    {"key": "coverage", "title": "Охват и спрос", "items": [
        {"key": "search_query_count", "label": "Уникальные запросы", "format": "number", "source_fields": ["search_query"]},
        {"key": "sku_count", "label": "SKU, максимум в день", "format": "number", "source_fields": ["wb_sku"]},
        {"key": "product_query_pairs", "label": "Связки товар × запрос", "format": "compact", "source_fields": ["wb_sku", "search_query"]},
        {"key": "avg_queries_per_sku", "label": "Ключей на SKU, среднее", "format": "number", "digits": 1, "source_fields": ["wb_sku", "search_query"]},
        {"key": "search_demand", "label": "Количество запросов", "format": "compact", "previous_key": "search_demand_prev", "change": "pct", "source_fields": ["query_count", "query_count_prev"]},
        {"key": "visible_pairs", "label": "Видимые связки", "format": "compact", "source_fields": ["visibility_pct"]},
        {"key": "visible_pairs_pct", "label": "Доля видимых связок", "format": "pct", "digits": 1, "source_fields": ["visibility_pct"]},
        {"key": "top20_pairs", "label": "Связки в топ-20", "format": "compact", "source_fields": ["average_position"]},
        {"key": "top20_pairs_pct", "label": "Доля связок в топ-20", "format": "pct", "digits": 1, "source_fields": ["average_position"]},
    ]},
    {"key": "placement", "title": "Видимость и позиции", "items": [
        {"key": "visibility_pct", "label": "Видимость", "format": "pct", "digits": 1, "previous_key": "visibility_pct_prev", "change": "pp", "source_fields": ["visibility_pct", "visibility_pct_prev"]},
        {"key": "average_position", "label": "Средняя позиция", "format": "number", "digits": 1, "previous_key": "average_position_prev", "change": "position", "source_fields": ["average_position", "average_position_prev"]},
        {"key": "median_position", "label": "Медианная позиция", "format": "number", "digits": 1, "previous_key": "median_position_prev", "change": "position", "source_fields": ["median_position", "median_position_prev"]},
        {"key": "best_position", "label": "Лучшая позиция", "format": "number", "digits": 1, "source_fields": ["average_position"]},
    ]},
    {"key": "funnel", "title": "Воронка из поиска", "items": [
        {"key": "card_visits", "label": "Переходы в карточку", "format": "compact", "previous_key": "card_visits_prev", "change": "pct", "source_fields": ["card_visits", "card_visits_prev"]},
        {"key": "cart_adds", "label": "Положили в корзину", "format": "compact", "previous_key": "cart_adds_prev", "change": "pct", "source_fields": ["cart_adds", "cart_adds_prev"]},
        {"key": "card_to_cart_pct", "label": "Конверсия в корзину", "format": "pct", "digits": 1, "previous_key": "card_to_cart_pct_prev", "change": "pp", "source_fields": ["card_to_cart_pct", "card_to_cart_pct_prev"]},
        {"key": "ordered_units", "label": "Заказали, шт", "format": "compact", "previous_key": "ordered_units_prev", "change": "pct", "source_fields": ["ordered_units", "ordered_units_prev"]},
        {"key": "cart_to_order_pct", "label": "Конверсия в заказ", "format": "pct", "digits": 1, "previous_key": "cart_to_order_pct_prev", "change": "pp", "source_fields": ["cart_to_order_pct", "cart_to_order_pct_prev"]},
    ]},
    {"key": "competition", "title": "Сравнение с конкурентами", "items": [
        {"key": "card_visits_competitor_percentile", "label": "Переходы: лучше карточек", "format": "pct", "digits": 1, "source_fields": ["card_visits_competitor_percentile"]},
        {"key": "cart_adds_competitor_percentile", "label": "Корзины: лучше карточек", "format": "pct", "digits": 1, "source_fields": ["cart_adds_competitor_percentile"]},
        {"key": "card_to_cart_competitor_percentile", "label": "Конверсия в корзину: лучше", "format": "pct", "digits": 1, "source_fields": ["card_to_cart_competitor_percentile"]},
        {"key": "ordered_units_competitor_percentile", "label": "Заказы: лучше карточек", "format": "pct", "digits": 1, "source_fields": ["ordered_units_competitor_percentile"]},
        {"key": "cart_to_order_competitor_percentile", "label": "Конверсия в заказ: лучше", "format": "pct", "digits": 1, "source_fields": ["cart_to_order_competitor_percentile"]},
    ]},
    {"key": "product", "title": "Карточка и цена", "items": [
        {"key": "card_rating", "label": "Рейтинг карточки", "format": "rating", "digits": 1, "suffix": "/10", "source_fields": ["card_rating"]},
        {"key": "review_rating", "label": "Рейтинг по отзывам", "format": "rating", "digits": 1, "suffix": "/5", "source_fields": ["review_rating"]},
        {"key": "min_price_rub", "label": "Минимальная цена", "format": "rub", "source_fields": ["min_price_rub"]},
        {"key": "max_price_rub", "label": "Максимальная цена", "format": "rub", "source_fields": ["max_price_rub"]},
    ]},
]


SUPPORTED_CLIENTS = {"gloria_jeans", "sportmaster", "konstex"}
SPORTMASTER_ASSORTMENT_FILTERS = (
    "sm_subcategory", "sm_brand", "sm_model", "sm_gender",
    "sm_age", "sm_collection", "sm_season", "sm_sport",
)


MAPPING_FILTERS = [
    ("gj_model", "gj_model"),
    ("assortment_bia", "assortment_bia"),
    ("tg", "tg"),
    ("tg_plus", "tg_plus"),
    ("cg", "cg"),
    ("season", "season"),
]
SEO_STATUS_NO_TAG = "__no_tag__"
COLLECTION_STATUS_NO_TAG = "__no_tag__"
COLLECTION_PRIORITY = "priority_collection"
PRIORITY_COLLECTION_SOURCE_TAG = "GJ лето 2026"
PRIORITY_COLLECTION_DISPLAY_TAG = "Glory Jeans Лето 2026"
COLLECTION_TAG_LABELS = {
    PRIORITY_COLLECTION_SOURCE_TAG: PRIORITY_COLLECTION_DISPLAY_TAG,
    "школа": "школа",
    "околошкола": "околошкола",
}
COLLECTION_TAG_PREFIXES = ("GJ ", "Glory Jeans", "Gloria Jeans")


def repeated_values(params: dict[str, list[str]], key: str) -> list[str]:
    values: list[str] = []
    for raw_value in params.get(key, []):
        for value in str(raw_value).split(","):
            normalized = value.strip()
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def sql_text_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def normalize_collection_value(value: str) -> str:
    if value == COLLECTION_PRIORITY:
        return PRIORITY_COLLECTION_SOURCE_TAG
    for source_tag, label in COLLECTION_TAG_LABELS.items():
        if value == label:
            return source_tag
    return value


def is_collection_tag(value: str) -> bool:
    normalized = normalize_collection_value(value)
    return (
        normalized in COLLECTION_TAG_LABELS
        or any(normalized.startswith(prefix) for prefix in COLLECTION_TAG_PREFIXES)
    )


def collection_tag_predicate(alias: str = "st") -> str:
    tag_expr = f"{alias}.tag"
    clauses = [
        f"{tag_expr} = {sql_text_literal(tag)}"
        for tag in sorted(COLLECTION_TAG_LABELS)
    ]
    clauses.extend(
        f"{tag_expr} ILIKE {sql_text_literal(prefix + '%%')}"
        for prefix in COLLECTION_TAG_PREFIXES
    )
    return "(" + " OR ".join(dict.fromkeys(clauses)) + ")"


def append_assortment_filters(
    clauses: list[str],
    values: list[Any],
    params: dict[str, list[str]],
    sku_expr: str,
    client_key: str = "gloria_jeans",
) -> None:
    if client_key == "sportmaster":
        from app import sportmaster_filter_sql

        clauses.extend(sportmaster_filter_sql(params, values, "wb", sku_expr=sku_expr))
        return
    if client_key != "gloria_jeans":
        return
    mapping_clauses: list[str] = []
    for param, column in MAPPING_FILTERS:
        selected = repeated_values(params, param)
        if not selected:
            continue
        if param == "gj_model":
            if len(selected) == 1:
                mapping_clauses.append(f"coalesce(m.{column}, '') ILIKE %s")
                values.append(f"%{selected[0]}%")
            else:
                mapping_clauses.append(f"coalesce(m.{column}, '') ILIKE ANY(%s)")
                values.append([f"%{value}%" for value in selected])
        elif len(selected) == 1:
            mapping_clauses.append(f"m.{column} = %s")
            values.append(selected[0])
        else:
            mapping_clauses.append(f"m.{column} = ANY(%s)")
            values.append(selected)
    if mapping_clauses:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM public.mv_sku_mapping_gj_wb_nmid m "
            f"WHERE m.wb_nmid = {sku_expr}::text "
            f"AND {' AND '.join(mapping_clauses)}"
            ")"
        )

    collection_values = repeated_values(params, "collection_status")
    selected_collections = [
        normalize_collection_value(value)
        for value in collection_values
        if value != COLLECTION_STATUS_NO_TAG
    ]
    collection_clauses: list[str] = []
    if selected_collections:
        collection_clauses.append(
            "EXISTS (SELECT 1 FROM public.marketplace_sku_tags st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr}::text AND st.tag = ANY(%s))"
        )
        values.extend(["wb", selected_collections])
    if COLLECTION_STATUS_NO_TAG in collection_values:
        collection_clauses.append(
            "NOT EXISTS (SELECT 1 FROM public.marketplace_sku_tags st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr}::text "
            f"AND {collection_tag_predicate('st')})"
        )
        values.append("wb")
    if collection_clauses:
        clauses.append("(" + " OR ".join(collection_clauses) + ")")

    seo_values = [
        value
        for value in repeated_values(params, "seo_status")
        if not is_collection_tag(value)
    ]
    selected_tags = [value for value in seo_values if value != SEO_STATUS_NO_TAG]
    seo_clauses: list[str] = []
    if selected_tags:
        seo_clauses.append(
            "EXISTS (SELECT 1 FROM public.marketplace_sku_tags st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr}::text AND st.tag = ANY(%s))"
        )
        values.extend(["wb", selected_tags])
    if SEO_STATUS_NO_TAG in seo_values:
        seo_clauses.append(
            "NOT EXISTS (SELECT 1 FROM public.marketplace_sku_tags st "
            f"WHERE st.marketplace = %s AND st.sku = {sku_expr}::text "
            f"AND NOT {collection_tag_predicate('st')})"
        )
        values.append("wb")
    if seo_clauses:
        clauses.append("(" + " OR ".join(seo_clauses) + ")")


def has_assortment_filters(
    params: dict[str, list[str]],
    client_key: str = "gloria_jeans",
) -> bool:
    if client_key == "sportmaster":
        return any(repeated_values(params, key) for key in SPORTMASTER_ASSORTMENT_FILTERS)
    if client_key != "gloria_jeans":
        return False
    return any(
        repeated_values(params, key)
        for key in [*(param for param, _ in MAPPING_FILTERS), "collection_status", "seo_status"]
    )


def append_query_classification_filters(
    clauses: list[str],
    values: list[Any],
    params: dict[str, list[str]],
    alias: str = "r",
) -> None:
    for param, column in (
        ("query_brand_class", "brand_class"),
        ("query_brand_name", "brand_name"),
        ("query_type", "query_type"),
        ("query_audience", "audience"),
        ("query_specificity", "specificity"),
    ):
        selected = repeated_values(params, param)
        if selected:
            clauses.append(
                "EXISTS (SELECT 1 FROM public.wb_search_query_classification c "
                f"WHERE c.search_query = {alias}.search_query AND c.{column} = ANY(%s))"
            )
            values.append(selected)
    scenarios = repeated_values(params, "query_scenario")
    if scenarios:
        clauses.append(
            "EXISTS (SELECT 1 FROM public.wb_search_query_classification c "
            f"WHERE c.search_query = {alias}.search_query AND c.scenario_tags && %s)"
        )
        values.append(scenarios)


def has_query_classification_filters(params: dict[str, list[str]]) -> bool:
    return any(repeated_values(params, key) for key in (
        "query_brand_class", "query_brand_name", "query_type",
        "query_audience", "query_scenario", "query_specificity",
    ))


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


def enrich_classification_row(row: Any) -> dict[str, Any]:
    result = normalize_row(row)
    result["brand_class_label"] = BRAND_CLASS_LABELS.get(result.get("brand_class"), "Не размечено")
    result["brand_name_label"] = result.get("brand_name") or "—"
    result["query_type_label"] = QUERY_TYPE_LABELS.get(result.get("query_type"), "Не размечено")
    result["audience_label"] = AUDIENCE_LABELS.get(result.get("audience"), "Не указана")
    result["scenario_labels"] = ", ".join(
        SCENARIO_LABELS.get(value, value)
        for value in (result.get("scenario_tags") or [])
    ) or "—"
    result["specificity_label"] = SPECIFICITY_LABELS.get(result.get("specificity"), "—")
    return result


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
        "abc_orders": [],
        "abc_sales": [],
        "abc_stock": [],
        "abc_combined": [],
        "gj_model_values": [],
        "assortment_bia_values": [],
        "tg_values": [],
        "tg_plus_values": [],
        "cg_values": [],
        "season_values": [],
        "collection_status_values": [],
        "collection_status_labels": {},
        "seo_status_values": [],
        "seo_status_labels": {},
        "query_brand_class_values": [],
        "query_brand_class_labels": BRAND_CLASS_LABELS,
        "query_brand_name_values": [],
        "query_type_values": [],
        "query_type_labels": QUERY_TYPE_LABELS,
        "query_audience_values": [],
        "query_audience_labels": AUDIENCE_LABELS,
        "query_scenario_values": [],
        "query_scenario_labels": SCENARIO_LABELS,
        "query_specificity_values": [],
        "query_specificity_labels": SPECIFICITY_LABELS,
        "frequency_tier_values": list(FREQUENCY_TIER_VALUES),
        "frequency_tier_labels": FREQUENCY_TIER_LABELS,
        "view": "public.mv_wb_search_queries_daily_summary",
    }


def empty_dashboard(message: str = "Данные поисковых запросов WB пока не импортированы") -> dict[str, Any]:
    return {
        "summary": {},
        "daily": [],
        "top_queries": [],
        "top_products": [],
        "position_growth": [],
        "position_decline": [],
        "rows": [],
        "columns": DETAIL_COLUMNS,
        "page": 1,
        "page_size": 50,
        "total": 0,
        "total_pages": 1,
        "sort_col": "category_rank",
        "sort_dir": "asc",
        "date_from": "",
        "date_to": "",
        "data_note": message,
    }


def handle_filters(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, Any]:
    payload = empty_filters()
    if client_key not in SUPPORTED_CLIENTS:
        return payload
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "wb_search_queries_daily"):
            return payload
        cur.execute(
            """
            SELECT min(report_date) AS date_from, max(report_date) AS date_to
            FROM public.wb_search_queries_daily
            """
        )
        bounds = cur.fetchone()
        cur.execute(
            """
            SELECT DISTINCT subject_name
            FROM public.wb_search_queries_daily
            WHERE nullif(subject_name, '') IS NOT NULL
            ORDER BY subject_name
            """
        )
        payload["category_names"] = [row["subject_name"] for row in cur.fetchall()]
        if client_key == "gloria_jeans" and relation_exists(cur, "mv_sku_mapping_gj_wb_nmid"):
            option_selects = ", ".join(
                f"array_remove(array_agg(DISTINCT m.{column} ORDER BY m.{column}), NULL) "
                f"AS {param}_values"
                for param, column in MAPPING_FILTERS
                if param != "gj_model"
            )
            cur.execute(
                f"""
                SELECT {option_selects}
                FROM public.mv_sku_mapping_gj_wb_nmid m
                WHERE EXISTS (
                    SELECT 1
                    FROM public.mv_wb_search_product_daily p
                    WHERE p.wb_sku::text = m.wb_nmid
                )
                """
            )
            option_row = cur.fetchone()
            for param, _ in MAPPING_FILTERS:
                payload[f"{param}_values"] = (
                    []
                    if param == "gj_model"
                    else option_row[f"{param}_values"] or []
                )
        if client_key == "gloria_jeans" and relation_exists(cur, "marketplace_sku_tags"):
            tag_scope = """
                st.marketplace = 'wb'
                AND EXISTS (
                    SELECT 1
                    FROM public.mv_wb_search_product_daily p
                    WHERE p.wb_sku::text = st.sku
                )
            """
            cur.execute(
                f"""
                SELECT array_remove(array_agg(DISTINCT st.tag ORDER BY st.tag), NULL) AS values
                FROM public.marketplace_sku_tags st
                WHERE {tag_scope}
                  AND {collection_tag_predicate('st')}
                """
            )
            payload["collection_status_values"] = (cur.fetchone() or {}).get("values") or []
            payload["collection_status_labels"] = {
                tag: COLLECTION_TAG_LABELS.get(tag, tag)
                for tag in payload["collection_status_values"]
            }
            cur.execute(
                f"""
                SELECT array_remove(array_agg(DISTINCT st.tag ORDER BY st.tag), NULL) AS values
                FROM public.marketplace_sku_tags st
                WHERE {tag_scope}
                  AND NOT {collection_tag_predicate('st')}
                """
            )
            payload["seo_status_values"] = (cur.fetchone() or {}).get("values") or []
        if relation_exists(cur, "wb_search_query_classification"):
            cur.execute(
                """
                SELECT
                    (SELECT array_agg(DISTINCT brand_class ORDER BY brand_class)
                     FROM public.wb_search_query_classification) AS brand_classes,
                    (SELECT array_agg(DISTINCT brand_name ORDER BY brand_name)
                     FROM public.wb_search_query_classification
                     WHERE nullif(brand_name, '') IS NOT NULL) AS brand_names,
                    (SELECT array_agg(DISTINCT query_type ORDER BY query_type)
                     FROM public.wb_search_query_classification) AS query_types,
                    (SELECT array_agg(DISTINCT audience ORDER BY audience)
                     FROM public.wb_search_query_classification) AS audiences,
                    (SELECT array_agg(DISTINCT scenario ORDER BY scenario)
                     FROM public.wb_search_query_classification,
                     LATERAL unnest(scenario_tags) scenario) AS scenarios,
                    (SELECT array_agg(DISTINCT specificity ORDER BY specificity)
                     FROM public.wb_search_query_classification) AS specificities
                """
            )
            classification_options = cur.fetchone()
            payload["query_brand_class_values"] = classification_options["brand_classes"] or []
            payload["query_brand_name_values"] = classification_options["brand_names"] or []
            payload["query_type_values"] = classification_options["query_types"] or []
            payload["query_audience_values"] = classification_options["audiences"] or []
            payload["query_scenario_values"] = classification_options["scenarios"] or []
            payload["query_specificity_values"] = classification_options["specificities"] or []
        if client_key == "sportmaster":
            from app import add_sportmaster_filter_options

            add_sportmaster_filter_options(cur, payload, "wb", parsed.query)
        payload["date_from"] = normalize_value(bounds["date_from"])
        payload["date_to"] = normalize_value(bounds["date_to"])
    return payload


def format_product_option(row: Any) -> str:
    product = normalize_row(row)
    parts = [f"WB {product.get('wb_sku')}", product.get("product_name") or "Без названия"]
    if product.get("seller_article"):
        parts.append(product["seller_article"])
    return " · ".join(str(part) for part in parts)


def handle_products(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, list[str]]:
    if client_key not in SUPPORTED_CLIENTS:
        return {"product_names": []}
    params = parse_qs(parsed.query)
    clauses = ["nullif(p.product_name, '') IS NOT NULL"]
    values: list[Any] = []
    date_from = params.get("date_from", [""])[0].strip()
    date_to = params.get("date_to", [""])[0].strip()
    if date_from:
        clauses.append("p.report_date >= %s")
        values.append(date_from)
    if date_to:
        clauses.append("p.report_date <= %s")
        values.append(date_to)
    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    if categories:
        clauses.append("p.subject_name = ANY(%s)")
        values.append(categories)
    query = params.get("q", [""])[0].strip()
    if query:
        clauses.append("(p.product_name ILIKE %s OR p.seller_article ILIKE %s OR p.wb_sku::text ILIKE %s)")
        values.extend([f"%{query}%"] * 3)
    append_assortment_filters(clauses, values, params, "p.wb_sku", client_key)
    where = " AND ".join(clauses)
    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "mv_wb_search_product_daily"):
            return {"product_names": []}
        cur.execute(
            f"""
            SELECT
                p.wb_sku,
                max(p.product_name) AS product_name,
                max(p.seller_article) AS seller_article
            FROM public.mv_wb_search_product_daily p
            WHERE {where}
            GROUP BY p.wb_sku
            ORDER BY lower(max(p.product_name)), p.wb_sku
            LIMIT 120
            """,
            values,
        )
        return {"product_names": [format_product_option(row) for row in cur.fetchall()]}


def int_param(params: dict[str, list[str]], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(params.get(key, [str(default)])[0]), maximum))
    except (TypeError, ValueError):
        return default


def raw_filters(
    params: dict[str, list[str]],
    date_from: str,
    date_to: str,
    client_key: str = "gloria_jeans",
) -> tuple[str, list[Any]]:
    clauses = ["r.report_date BETWEEN %s AND %s"]
    values: list[Any] = [date_from, date_to]
    categories = [value.strip() for value in params.get("categories", []) if value.strip()]
    if categories:
        clauses.append("r.subject_name = ANY(%s)")
        values.append(categories)
    product = params.get("product", [""])[0].strip()
    if product:
        product_prefix = product.split("·", 1)[0].strip()
        product_sku = product_prefix.removeprefix("WB ").strip() if product_prefix.startswith("WB ") else ""
        if product_sku.isdigit():
            clauses.append("r.wb_sku = %s")
            values.append(int(product_sku))
        else:
            clauses.append(
                "(r.product_name ILIKE %s OR r.seller_article ILIKE %s OR r.wb_sku::text ILIKE %s)"
            )
            values.extend([f"%{product}%"] * 3)
    search_query = params.get("q", [""])[0].strip()
    if search_query:
        clauses.append("r.search_query ILIKE %s")
        values.append(f"%{search_query}%")
    article = params.get("article", [""])[0].strip()
    if article:
        clauses.append(
            "(r.seller_article ILIKE %s OR r.wb_sku::text ILIKE %s "
            "OR r.product_name ILIKE %s OR r.search_query ILIKE %s)"
        )
        values.extend([f"%{article}%"] * 4)
    append_assortment_filters(clauses, values, params, "r.wb_sku", client_key)
    append_query_classification_filters(clauses, values, params)
    return " AND ".join(clauses), values


QUERY_DAY_RAW_SQL = """
    SELECT
        report_date,
        search_query,
        max(query_count)::bigint AS query_count,
        max(query_count_prev)::bigint AS query_count_prev,
        count(*)::bigint AS product_query_pairs,
        count(DISTINCT wb_sku)::bigint AS sku_count,
        count(*) FILTER (WHERE coalesce(visibility_pct, 0) > 0)::bigint AS visible_sku_count,
        round(sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)), 0), 2) AS visibility_pct,
        round(sum(average_position * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
        min(average_position) AS best_position,
        coalesce(sum(card_visits), 0)::bigint AS card_visits,
        coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
        coalesce(sum(ordered_units), 0)::bigint AS ordered_units
    FROM base
    GROUP BY report_date, search_query
"""

CATEGORY_QUERY_DAY_RAW_SQL = """
    SELECT
        report_date,
        coalesce(nullif(subject_name, ''), 'Без категории') AS subject_name,
        search_query,
        max(query_count)::bigint AS query_count,
        count(*)::bigint AS product_query_pairs,
        count(DISTINCT wb_sku)::bigint AS sku_count,
        round(sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)), 0), 2) AS visibility_pct,
        round(sum(average_position * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
        min(average_position) AS best_position,
        coalesce(sum(card_visits), 0)::bigint AS card_visits,
        coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
        coalesce(sum(ordered_units), 0)::bigint AS ordered_units
    FROM base
    GROUP BY report_date, coalesce(nullif(subject_name, ''), 'Без категории'), search_query
"""

CATEGORY_FREQUENCY_RANK_CTES = """
    category_frequency_period AS MATERIALIZED (
        SELECT
            subject_name,
            search_query,
            coalesce(sum(query_count), 0)::bigint AS search_demand
        FROM category_query_day
        GROUP BY subject_name, search_query
    ),
    category_frequency_ranked AS MATERIALIZED (
        SELECT
            p.*,
            rank() OVER (
                PARTITION BY subject_name
                ORDER BY search_demand DESC
            )::bigint AS category_rank,
            count(*) OVER (PARTITION BY subject_name)::bigint AS category_query_count,
            coalesce((
                sum(search_demand) OVER (
                    PARTITION BY subject_name
                    ORDER BY search_demand DESC
                    RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                )
                - sum(search_demand) OVER (PARTITION BY subject_name, search_demand)
            )::numeric / nullif(
                sum(search_demand) OVER (PARTITION BY subject_name), 0
            ), 1) AS cumulative_demand_share_before
        FROM category_frequency_period p
    ),
    category_frequency_labeled AS MATERIALIZED (
        SELECT
            r.*,
            CASE
                WHEN cumulative_demand_share_before < 0.80 THEN 'ВЧ'
                WHEN cumulative_demand_share_before < 0.95 THEN 'СЧ'
                ELSE 'НЧ'
            END AS frequency_tier,
            CASE
                WHEN cumulative_demand_share_before < 0.80 THEN 1
                WHEN cumulative_demand_share_before < 0.95 THEN 2
                ELSE 3
            END AS frequency_tier_order
        FROM category_frequency_ranked r
    )
"""


PRODUCT_DAY_RAW_SQL = """
    SELECT
        report_date,
        wb_sku,
        max(seller_article) AS seller_article,
        max(product_name) AS product_name,
        max(subject_name) AS subject_name,
        max(brand) AS brand,
        max(card_rating) AS card_rating,
        max(review_rating) AS review_rating,
        count(DISTINCT search_query)::bigint AS query_count,
        coalesce(sum(query_count), 0)::bigint AS search_demand,
        coalesce(sum(query_count_prev), 0)::bigint AS search_demand_prev,
        round(sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)), 0), 2) AS visibility_pct,
        round(sum(average_position * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
        min(average_position) AS best_position,
        coalesce(sum(card_visits), 0)::bigint AS card_visits,
        coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
        coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
        min(nullif(min_price_rub, 0)) AS min_price_rub,
        max(nullif(max_price_rub, 0)) AS max_price_rub
    FROM base
    GROUP BY report_date, wb_sku
"""

PAIR_DAY_RAW_SQL = """
    SELECT
        report_date,
        count(*)::bigint AS product_query_pairs,
        count(DISTINCT wb_sku)::bigint AS sku_count,
        count(DISTINCT search_query)::bigint AS search_query_count,
        count(*) FILTER (WHERE coalesce(visibility_pct, 0) > 0)::bigint AS visible_pairs,
        count(*) FILTER (WHERE coalesce(visibility_pct_prev, 0) > 0)::bigint AS visible_pairs_prev,
        count(*) FILTER (WHERE average_position <= 20)::bigint AS top20_pairs,
        count(*) FILTER (WHERE average_position_prev <= 20)::bigint AS top20_pairs_prev,
        coalesce(sum(query_count), 0)::bigint AS visibility_weight,
        coalesce(sum(query_count_prev), 0)::bigint AS visibility_prev_weight,
        round(sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
            / nullif(sum(coalesce(query_count, 0)), 0), 2) AS visibility_pct,
        round(sum(coalesce(visibility_pct_prev, 0) * coalesce(query_count_prev, 0))
            / nullif(sum(coalesce(query_count_prev, 0)), 0), 2) AS visibility_pct_prev,
        coalesce(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0)::bigint AS average_position_weight,
        coalesce(sum(query_count_prev) FILTER (WHERE average_position_prev IS NOT NULL), 0)::bigint AS average_position_prev_weight,
        round(sum(average_position * coalesce(query_count, 0))
            / nullif(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
        round(sum(average_position_prev * coalesce(query_count_prev, 0))
            / nullif(sum(query_count_prev) FILTER (WHERE average_position_prev IS NOT NULL), 0), 2) AS average_position_prev,
        coalesce(sum(query_count) FILTER (WHERE median_position > 0), 0)::bigint AS median_position_weight,
        coalesce(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0)::bigint AS median_position_prev_weight,
        round(sum(median_position * coalesce(query_count, 0)) FILTER (WHERE median_position > 0)
            / nullif(sum(query_count) FILTER (WHERE median_position > 0), 0), 2) AS median_position,
        round(sum(median_position_prev * coalesce(query_count_prev, 0)) FILTER (WHERE median_position_prev > 0)
            / nullif(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0), 2) AS median_position_prev,
        min(average_position) AS best_position,
        coalesce(sum(card_visits), 0)::bigint AS card_visits,
        coalesce(sum(card_visits_prev), 0)::bigint AS card_visits_prev,
        coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
        coalesce(sum(cart_adds_prev), 0)::bigint AS cart_adds_prev,
        coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
        coalesce(sum(ordered_units_prev), 0)::bigint AS ordered_units_prev,
        coalesce(sum(query_count), 0)::bigint AS competitor_weight,
        round(sum(card_visits_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_visits_competitor_percentile,
        round(sum(cart_adds_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_adds_competitor_percentile,
        round(sum(card_to_cart_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_to_cart_competitor_percentile,
        round(sum(ordered_units_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS ordered_units_competitor_percentile,
        round(sum(cart_to_order_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_to_order_competitor_percentile
    FROM base GROUP BY report_date
"""


def source_ctes(filtered: bool, where: str) -> tuple[str, str, str]:
    if not filtered:
        return (
            "",
            "SELECT * FROM public.mv_wb_search_query_daily WHERE report_date BETWEEN %s AND %s",
            "SELECT * FROM public.mv_wb_search_product_daily WHERE report_date BETWEEN %s AND %s",
        )
    base = f"base AS MATERIALIZED (SELECT * FROM public.wb_search_queries_daily r WHERE {where})"
    return base, QUERY_DAY_RAW_SQL, PRODUCT_DAY_RAW_SQL


def selected_frequency_tiers(params: dict[str, list[str]]) -> list[str]:
    return [
        value for value in repeated_values(params, "frequency_tier")
        if value in FREQUENCY_TIER_VALUES
    ]


def frequency_selection_cte(params: dict[str, list[str]]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    tiers = selected_frequency_tiers(params)
    if tiers:
        clauses.append("frequency_tier = ANY(%s)")
        values.append(tiers)
    search_query = params.get("q", [""])[0].strip()
    if search_query:
        clauses.append("search_query ILIKE %s")
        values.append(f"%{search_query}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return (
        "selected_frequency_queries AS MATERIALIZED ("
        f"SELECT * FROM category_frequency_labeled{where}"
        ")",
        values,
    )


def category_rank_source(
    params: dict[str, list[str]],
    date_from: str,
    date_to: str,
    materialized_view_available: bool,
    client_key: str = "gloria_jeans",
) -> tuple[str, list[Any]]:
    ranking_params = {
        key: list(values)
        for key, values in params.items()
        if key not in {"q", "frequency_tier"}
    }
    requires_raw = bool(
        ranking_params.get("product", [""])[0].strip()
        or ranking_params.get("article", [""])[0].strip()
        or has_assortment_filters(ranking_params, client_key)
        or not materialized_view_available
    )
    if requires_raw:
        where, values = raw_filters(ranking_params, date_from, date_to, client_key)
        return (
            "category_rank_base AS MATERIALIZED ("
            f"SELECT * FROM public.wb_search_queries_daily r WHERE {where}"
            "), category_query_day AS MATERIALIZED ("
            f"{CATEGORY_QUERY_DAY_RAW_SQL.replace('FROM base', 'FROM category_rank_base')}"
            ")",
            values,
        )

    clauses = ["r.report_date BETWEEN %s AND %s"]
    values: list[Any] = [date_from, date_to]
    categories = repeated_values(ranking_params, "categories")
    if categories:
        clauses.append("r.subject_name = ANY(%s)")
        values.append(categories)
    append_query_classification_filters(clauses, values, ranking_params, alias="r")
    return (
        "category_query_day AS MATERIALIZED ("
        "SELECT * FROM public.mv_wb_search_category_query_daily r "
        f"WHERE {' AND '.join(clauses)}"
        ")",
        values,
    )



def column_detail_filter(params):
    from table_query import sql_filters
    quote=lambda value:"'"+value.replace("'","''")+"'"
    def labels(field,mapping,fallback):
        return 'CASE '+field+' '+ ' '.join('WHEN '+quote(key)+' THEN '+quote(value) for key,value in mapping.items())+' ELSE '+quote(fallback)+' END'
    expressions={
        'brand_class_label':labels('brand_class',BRAND_CLASS_LABELS,'Не размечено'),
        'brand_name_label':'brand_name',
        'query_type_label':labels('query_type',QUERY_TYPE_LABELS,'Не размечено'),
        'audience_label':labels('audience',AUDIENCE_LABELS,'Не указана'),
        'specificity_label':labels('specificity',SPECIFICITY_LABELS,''),
        'scenario_labels':"(SELECT string_agg("+labels('tag',SCENARIO_LABELS,'')+", ', ' ORDER BY ord) FROM unnest(scenario_tags) WITH ORDINALITY AS scenario(tag,ord))",
    }
    get=lambda key,default='':params.get(key,[default])[0]
    return sql_filters(get,DETAIL_COLUMNS,'category_rank',expressions)

def handle_dashboard(parsed: Any, get_conn: Callable[[], Any], client_key: str) -> dict[str, Any]:
    if client_key not in SUPPORTED_CLIENTS:
        return empty_dashboard("Отчет недоступен для выбранного клиента")
    params = parse_qs(parsed.query)
    page_size = int_param(params, "limit", 50, 10, 200)
    page = int_param(params, "page", 1, 1, 1_000_000)
    sort_col = params.get("sort_col", ["category_rank"])[0]
    if sort_col not in ALLOWED_SORT_COLUMNS:
        sort_col = "category_rank"
    sort_expression = SORT_COLUMN_EXPRESSIONS.get(sort_col, sort_col)
    default_sort_dir = "asc" if sort_col in {"subject_name", "frequency_tier", "category_rank"} else "desc"
    sort_dir = "asc" if params.get("sort_dir", [default_sort_dir])[0].lower() == "asc" else "desc"
    direction = "ASC" if sort_dir == "asc" else "DESC"
    nulls = "NULLS LAST"
    column_where,column_args=column_detail_filter(params)
    if sort_col in {"frequency_tier", "category_rank"}:
        detail_order = (
            f"subject_name ASC, {sort_expression} {direction} {nulls}, "
            "search_demand DESC, search_query"
        )
    elif sort_col == "subject_name":
        detail_order = (
            f"subject_name {direction} {nulls}, category_rank ASC, "
            "search_demand DESC, search_query"
        )
    else:
        detail_order = (
            f"{sort_expression} {direction} {nulls}, subject_name, "
            "category_rank, search_query"
        )

    with get_conn() as conn, conn.cursor() as cur:
        if not relation_exists(cur, "mv_wb_search_queries_daily_summary"):
            return empty_dashboard()
        cur.execute(
            "SELECT min(report_date) AS date_from, max(report_date) AS date_to FROM public.mv_wb_search_queries_daily_summary"
        )
        bounds = cur.fetchone()
        date_from = params.get("date_from", [""])[0].strip() or bounds["date_from"].isoformat()
        date_to = params.get("date_to", [""])[0].strip() or bounds["date_to"].isoformat()
        if date_from > date_to:
            date_from, date_to = date_to, date_from

        where, raw_values = raw_filters(params, date_from, date_to, client_key)
        frequency_tiers = selected_frequency_tiers(params)
        category_source_ctes, category_values = category_rank_source(
            params,
            date_from,
            date_to,
            relation_exists(cur, "mv_wb_search_category_query_daily"),
            client_key,
        )
        frequency_selection_sql, frequency_selection_values = frequency_selection_cte(params)
        filtered = bool(
            [value for value in params.get("categories", []) if value.strip()]
            or params.get("product", [""])[0].strip()
            or params.get("q", [""])[0].strip()
            or params.get("article", [""])[0].strip()
            or has_assortment_filters(params, client_key)
            or has_query_classification_filters(params)
            or frequency_tiers
        )
        if frequency_tiers:
            tier_membership = (
                "EXISTS (SELECT 1 FROM selected_frequency_queries f "
                "WHERE f.subject_name = coalesce(nullif(r.subject_name, ''), 'Без категории') "
                "AND f.search_query = r.search_query)"
            )
            base_cte = (
                f"{category_source_ctes}, {CATEGORY_FREQUENCY_RANK_CTES}, "
                f"{frequency_selection_sql}, base AS MATERIALIZED ("
                "SELECT * FROM public.wb_search_queries_daily r "
                f"WHERE {where} AND {tier_membership})"
            )
            query_day_sql = QUERY_DAY_RAW_SQL
            product_day_sql = PRODUCT_DAY_RAW_SQL
            scoped_values = category_values + frequency_selection_values + raw_values
        else:
            base_cte, query_day_sql, product_day_sql = source_ctes(filtered, where)
            scoped_values = raw_values
        query_values = scoped_values if filtered else [date_from, date_to]
        product_values = scoped_values if filtered else [date_from, date_to]
        with_prefix = f"WITH {base_cte}," if base_cte else "WITH"

        if filtered:
            cur.execute(
                f"""
                {with_prefix}
                query_day AS ({query_day_sql}),
                product_day AS ({product_day_sql}),
                pair_stats AS ({PAIR_DAY_RAW_SQL}),
                demand AS (
                    SELECT report_date,
                        coalesce(sum(query_count), 0)::bigint AS search_demand,
                        coalesce(sum(query_count_prev), 0)::bigint AS search_demand_prev
                    FROM query_day GROUP BY report_date
                ),
                product_stats AS (
                    SELECT report_date,
                        round(avg(card_rating), 2) AS card_rating,
                        round(avg(review_rating), 2) AS review_rating,
                        min(min_price_rub) AS min_price_rub,
                        max(max_price_rub) AS max_price_rub
                    FROM product_day GROUP BY report_date
                )
                SELECT
                    p.*,
                    d.search_demand,
                    d.search_demand_prev,
                    s.card_rating, s.review_rating, s.min_price_rub, s.max_price_rub,
                    round(p.visible_pairs::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS visible_pairs_pct,
                    round(p.visible_pairs_prev::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS visible_pairs_pct_prev,
                    round(p.top20_pairs::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS top20_pairs_pct,
                    round(p.top20_pairs_prev::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS top20_pairs_pct_prev,
                    round(p.cart_adds::numeric / nullif(p.card_visits, 0) * 100, 2) AS card_to_cart_pct,
                    round(p.cart_adds_prev::numeric / nullif(p.card_visits_prev, 0) * 100, 2) AS card_to_cart_pct_prev,
                    round(p.ordered_units::numeric / nullif(p.cart_adds, 0) * 100, 2) AS cart_to_order_pct,
                    round(p.ordered_units_prev::numeric / nullif(p.cart_adds_prev, 0) * 100, 2) AS cart_to_order_pct_prev
                FROM pair_stats p
                JOIN demand d USING (report_date)
                JOIN product_stats s USING (report_date)
                ORDER BY p.report_date
                """,
                query_values,
            )
        else:
            cur.execute(
                """
                SELECT * FROM public.mv_wb_search_queries_daily_summary
                WHERE report_date BETWEEN %s AND %s
                ORDER BY report_date
                """,
                [date_from, date_to],
            )
        daily = normalize_rows(cur.fetchall())
        for row in daily:
            sku_count = float(row.get("sku_count") or 0)
            row["avg_queries_per_sku"] = (
                round(float(row.get("product_query_pairs") or 0) / sku_count, 2)
                if sku_count
                else 0.0
            )

        cur.execute(
            f"""
            {with_prefix}
            query_day AS ({query_day_sql}),
            query_period AS MATERIALIZED (
                SELECT
                    search_query,
                    c.brand_class,
                    c.brand_name,
                    c.query_type,
                    c.audience,
                    c.scenario_tags,
                    c.specificity,
                    coalesce(sum(query_count), 0)::bigint AS search_demand,
                    max(sku_count)::bigint AS sku_count,
                    round(sum(coalesce(visibility_pct, 0) * product_query_pairs)
                        / nullif(sum(product_query_pairs), 0), 2) AS visibility_pct,
                    round(sum(average_position * product_query_pairs)
                        / nullif(sum(product_query_pairs) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
                    min(best_position) AS best_position,
                    coalesce(sum(card_visits), 0)::bigint AS card_visits,
                    coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                    coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                    round(coalesce(sum(cart_adds), 0)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                    round(coalesce(sum(ordered_units), 0)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct
                FROM query_day q
                LEFT JOIN public.wb_search_query_classification c USING (search_query)
                GROUP BY search_query, c.brand_class, c.brand_name, c.query_type, c.audience, c.scenario_tags, c.specificity
            )
            SELECT
                (SELECT count(*) FROM query_period) AS search_query_total,
                (SELECT json_agg(top_row) FROM (
                    SELECT * FROM query_period ORDER BY search_demand DESC, ordered_units DESC, search_query LIMIT 10
                ) top_row) AS top_queries
            """,
            query_values,
        )
        query_payload = cur.fetchone()
        query_payload["top_queries"] = [enrich_classification_row(row) for row in (query_payload["top_queries"] or [])]
        search_query_total = int(query_payload["search_query_total"] or 0)

        detail_values = list(category_values) + list(frequency_selection_values)
        cur.execute(
            f"""
            WITH
            {category_source_ctes},
            {CATEGORY_FREQUENCY_RANK_CTES},
            {frequency_selection_sql},
            category_query_period AS MATERIALIZED (
                SELECT
                    q.subject_name,
                    q.search_query,
                    c.brand_class,
                    c.brand_name,
                    c.query_type,
                    c.audience,
                    c.scenario_tags,
                    c.specificity,
                    coalesce(sum(q.query_count), 0)::bigint AS search_demand,
                    max(q.sku_count)::bigint AS sku_count,
                    round(sum(coalesce(q.visibility_pct, 0) * q.product_query_pairs)
                        / nullif(sum(q.product_query_pairs), 0), 2) AS visibility_pct,
                    round(sum(q.average_position * q.product_query_pairs)
                        / nullif(sum(q.product_query_pairs) FILTER (WHERE q.average_position IS NOT NULL), 0), 2) AS average_position,
                    min(q.best_position) AS best_position,
                    coalesce(sum(q.card_visits), 0)::bigint AS card_visits,
                    coalesce(sum(q.cart_adds), 0)::bigint AS cart_adds,
                    coalesce(sum(q.ordered_units), 0)::bigint AS ordered_units,
                    round(coalesce(sum(q.cart_adds), 0)::numeric / nullif(sum(q.card_visits), 0) * 100, 2) AS card_to_cart_pct,
                    round(coalesce(sum(q.ordered_units), 0)::numeric / nullif(sum(q.cart_adds), 0) * 100, 2) AS cart_to_order_pct
                FROM category_query_day q
                LEFT JOIN public.wb_search_query_classification c USING (search_query)
                GROUP BY q.subject_name, q.search_query, c.brand_class, c.brand_name,
                    c.query_type, c.audience, c.scenario_tags, c.specificity
            ),
            category_labeled AS MATERIALIZED (
                SELECT
                    p.*,
                    f.category_rank,
                    f.category_query_count,
                    f.cumulative_demand_share_before,
                    f.frequency_tier,
                    f.frequency_tier_order
                FROM category_query_period p
                JOIN selected_frequency_queries f USING (subject_name, search_query)
            ),
            detail_rows AS MATERIALIZED (
                SELECT * FROM category_labeled WHERE {column_where}
            ),
            frequency_tier_daily AS MATERIALIZED (
                SELECT
                    q.report_date,
                    count(DISTINCT q.search_query) FILTER (WHERE f.frequency_tier = 'ВЧ')::bigint AS high_frequency_query_count,
                    count(DISTINCT q.search_query) FILTER (WHERE f.frequency_tier = 'СЧ')::bigint AS medium_frequency_query_count,
                    count(DISTINCT q.search_query) FILTER (WHERE f.frequency_tier = 'НЧ')::bigint AS low_frequency_query_count
                FROM category_query_day q
                JOIN selected_frequency_queries f USING (subject_name, search_query)
                GROUP BY q.report_date
            )
            SELECT
                (SELECT count(*) FROM detail_rows) AS total,
                (SELECT json_agg(detail_row) FROM (
                    SELECT * FROM detail_rows
                    ORDER BY {detail_order}
                    LIMIT %s OFFSET %s
                ) detail_row) AS rows,
                (SELECT json_agg(tier_day) FROM (
                    SELECT * FROM frequency_tier_daily ORDER BY report_date
                ) tier_day) AS frequency_tier_daily
            """,
            detail_values + column_args + [page_size, (page - 1) * page_size],
        )
        detail_payload = cur.fetchone()
        detail_rows = [enrich_classification_row(row) for row in (detail_payload["rows"] or [])]
        tier_daily_by_date = {
            row["report_date"]: row
            for row in normalize_rows(detail_payload.get("frequency_tier_daily") or [])
        }
        for row in daily:
            tier_row = tier_daily_by_date.get(row.get("report_date"), {})
            row["high_frequency_query_count"] = int(tier_row.get("high_frequency_query_count") or 0)
            row["medium_frequency_query_count"] = int(tier_row.get("medium_frequency_query_count") or 0)
            row["low_frequency_query_count"] = int(tier_row.get("low_frequency_query_count") or 0)
        total = int(detail_payload["total"] or 0)
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages:
            page = total_pages

        cur.execute(
            f"""
            {with_prefix}
            product_day AS ({product_day_sql})
            SELECT
                wb_sku, max(seller_article) AS seller_article, max(product_name) AS product_name,
                max(subject_name) AS subject_name, max(brand) AS brand,
                max(query_count)::bigint AS query_count,
                coalesce(sum(search_demand), 0)::bigint AS search_demand,
                round(sum(coalesce(visibility_pct, 0) * query_count) / nullif(sum(query_count), 0), 2) AS visibility_pct,
                round(sum(average_position * query_count)
                    / nullif(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0), 2) AS average_position,
                min(best_position) AS best_position,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units
            FROM product_day GROUP BY wb_sku
            ORDER BY ordered_units DESC, card_visits DESC, search_demand DESC
            LIMIT 10
            """,
            product_values,
        )
        top_products = normalize_rows(cur.fetchall())

        cur.execute(
            f"""
            {with_prefix}
            query_day AS ({query_day_sql}), bounds AS (
                SELECT min(report_date) AS first_date, max(report_date) AS last_date FROM query_day
            ), movement AS (
                SELECT
                    last.search_query,
                    first.average_position AS first_position,
                    last.average_position AS last_position,
                    round(first.average_position - last.average_position, 2) AS position_change,
                    last.query_count AS latest_demand,
                    last.ordered_units AS latest_orders
                FROM bounds b
                JOIN query_day first ON first.report_date = b.first_date
                JOIN query_day last ON last.report_date = b.last_date AND last.search_query = first.search_query
                WHERE b.first_date < b.last_date
                  AND first.average_position IS NOT NULL AND last.average_position IS NOT NULL
                  AND coalesce(last.query_count, 0) > 0
            )
            SELECT * FROM movement
            ORDER BY position_change DESC, latest_demand DESC
            LIMIT 8
            """,
            query_values,
        )
        position_growth = normalize_rows(cur.fetchall())
        cur.execute(
            f"""
            {with_prefix}
            query_day AS ({query_day_sql}), bounds AS (
                SELECT min(report_date) AS first_date, max(report_date) AS last_date FROM query_day
            ), movement AS (
                SELECT
                    last.search_query,
                    first.average_position AS first_position,
                    last.average_position AS last_position,
                    round(first.average_position - last.average_position, 2) AS position_change,
                    last.query_count AS latest_demand,
                    last.ordered_units AS latest_orders
                FROM bounds b
                JOIN query_day first ON first.report_date = b.first_date
                JOIN query_day last ON last.report_date = b.last_date AND last.search_query = first.search_query
                WHERE b.first_date < b.last_date
                  AND first.average_position IS NOT NULL AND last.average_position IS NOT NULL
                  AND coalesce(last.query_count, 0) > 0
            )
            SELECT * FROM movement
            ORDER BY position_change ASC, latest_demand DESC
            LIMIT 8
            """,
            query_values,
        )
        position_decline = normalize_rows(cur.fetchall())
        from wb_market_search_comparison import (
            build_market_metric_group,
            build_market_search_comparison,
        )

        market_comparison = build_market_search_comparison(
            cur,
            params,
            date_from,
            date_to,
            client_key,
            relation_exists=relation_exists,
            category_rank_source=category_rank_source,
            frequency_selection_cte=frequency_selection_cte,
            selected_frequency_tiers=selected_frequency_tiers,
            repeated_values=repeated_values,
            category_frequency_rank_ctes=CATEGORY_FREQUENCY_RANK_CTES,
        )

    latest = daily[-1] if daily else {}
    summary = {
        "search_query_count": search_query_total,
        "sku_count": max((int(row.get("sku_count") or 0) for row in daily), default=0),
        "search_demand": sum(float(row.get("search_demand") or 0) for row in daily),
        "search_demand_prev": sum(float(row.get("search_demand_prev") or 0) for row in daily),
        "product_query_pairs": sum(float(row.get("product_query_pairs") or 0) for row in daily),
        "avg_queries_per_sku": (
            round(
                sum(float(row.get("product_query_pairs") or 0) for row in daily)
                / sum(float(row.get("sku_count") or 0) for row in daily),
                2,
            )
            if sum(float(row.get("sku_count") or 0) for row in daily)
            else 0.0
        ),
        "high_frequency_query_count": int(latest.get("high_frequency_query_count") or 0),
        "medium_frequency_query_count": int(latest.get("medium_frequency_query_count") or 0),
        "low_frequency_query_count": int(latest.get("low_frequency_query_count") or 0),
        "visible_pairs": sum(float(row.get("visible_pairs") or 0) for row in daily),
        "visible_pairs_prev": sum(float(row.get("visible_pairs_prev") or 0) for row in daily),
        "top20_pairs": sum(float(row.get("top20_pairs") or 0) for row in daily),
        "top20_pairs_prev": sum(float(row.get("top20_pairs_prev") or 0) for row in daily),
        "visibility_pct": weighted_average(daily, "visibility_pct", "visibility_weight"),
        "visibility_pct_prev": weighted_average(daily, "visibility_pct_prev", "visibility_prev_weight"),
        "average_position": weighted_average(daily, "average_position", "average_position_weight"),
        "average_position_prev": weighted_average(daily, "average_position_prev", "average_position_prev_weight"),
        "median_position": weighted_average(daily, "median_position", "median_position_weight"),
        "median_position_prev": weighted_average(daily, "median_position_prev", "median_position_prev_weight"),
        "best_position": min((float(row["best_position"]) for row in daily if row.get("best_position") is not None), default=0),
        "card_visits": sum(float(row.get("card_visits") or 0) for row in daily),
        "card_visits_prev": sum(float(row.get("card_visits_prev") or 0) for row in daily),
        "cart_adds": sum(float(row.get("cart_adds") or 0) for row in daily),
        "cart_adds_prev": sum(float(row.get("cart_adds_prev") or 0) for row in daily),
        "ordered_units": sum(float(row.get("ordered_units") or 0) for row in daily),
        "ordered_units_prev": sum(float(row.get("ordered_units_prev") or 0) for row in daily),
        "card_rating": float(latest.get("card_rating") or 0),
        "review_rating": float(latest.get("review_rating") or 0),
        "min_price_rub": float(latest.get("min_price_rub") or 0),
        "max_price_rub": float(latest.get("max_price_rub") or 0),
    }
    summary["visible_pairs_pct"] = ratio(summary["visible_pairs"], summary["product_query_pairs"])
    summary["top20_pairs_pct"] = ratio(summary["top20_pairs"], summary["product_query_pairs"])
    summary["card_to_cart_pct"] = ratio(summary["cart_adds"], summary["card_visits"])
    summary["card_to_cart_pct_prev"] = ratio(summary["cart_adds_prev"], summary["card_visits_prev"])
    summary["cart_to_order_pct"] = ratio(summary["ordered_units"], summary["cart_adds"])
    summary["cart_to_order_pct_prev"] = ratio(summary["ordered_units_prev"], summary["cart_adds_prev"])
    for key in (
        "card_visits_competitor_percentile", "cart_adds_competitor_percentile",
        "card_to_cart_competitor_percentile", "ordered_units_competitor_percentile",
        "cart_to_order_competitor_percentile",
    ):
        summary[key] = weighted_average(daily, key, "competitor_weight")
    metric_groups = build_metric_groups(summary)
    market_group = build_market_metric_group(market_comparison)
    if market_group:
        metric_groups.append(market_group)
    return {
        "summary": summary,
        "metric_groups": metric_groups,
        "market_comparison": market_comparison,
        "daily": daily,
        "top_queries": query_payload["top_queries"] or [],
        "top_products": top_products,
        "position_growth": position_growth,
        "position_decline": position_decline,
        "rows": detail_rows,
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
            "Спрос дедуплицирован на уровне дата × поисковый запрос; "
            "переходы, корзины и заказы суммируются по товаро-запросным строкам. "
            "«Ключей на SKU» — среднее число уникальных поисковых запросов на SKU за день. "
            "В детализации один ряд — категория × запрос; ВЧ формируют первые 80% "
            "частотности категории, СЧ — следующие 15%, НЧ — оставшийся хвост. "
            "Поиск по тексту и фильтр ранга применяются после расчёта и не меняют категорийный ранг. "
            "Счётчики ВЧ/СЧ/НЧ показывают уникальные запросы каждого ранга по дням. "
            "«Пред.» — поля предыдущего периода внутри каждой дневной выгрузки WB."
            " Сравнение с площадкой использует последний Serp-период, пересекающий выбранные даты; "
            "категория сопоставляется по точному нормализованному предмету WB, а охват — это "
            "пересечение ключей площадки с запросами, в которых присутствовали выбранные товары."
        ),
    }


def weighted_average(rows: list[dict[str, Any]], value_key: str, weight_key: str) -> float:
    weighted = 0.0
    total_weight = 0.0
    for row in rows:
        value = row.get(value_key)
        weight = float(row.get(weight_key) or 0)
        if value is None or weight <= 0:
            continue
        weighted += float(value) * weight
        total_weight += weight
    return round(weighted / total_weight, 2) if total_weight else 0.0


def ratio(value: Any, base: Any) -> float:
    denominator = float(base or 0)
    return round(float(value or 0) / denominator * 100, 2) if denominator else 0.0


def build_metric_groups(summary: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for group in METRIC_GROUPS:
        items = []
        for config in group["items"]:
            item = dict(config)
            current = float(summary.get(config["key"]) or 0)
            item["value"] = current
            previous_key = config.get("previous_key")
            if previous_key:
                previous = float(summary.get(previous_key) or 0)
                item["previous"] = previous
                change_kind = config.get("change")
                if change_kind == "pct":
                    item["change_value"] = round((current / previous - 1) * 100, 1) if previous else None
                    item["change_suffix"] = "%"
                elif change_kind == "pp":
                    item["change_value"] = round(current - previous, 1)
                    item["change_suffix"] = " п.п."
                elif change_kind == "position":
                    item["change_value"] = round(previous - current, 1)
                    item["change_suffix"] = " поз."
            items.append(item)
        groups.append({"key": group["key"], "title": group["title"], "items": items})
    return groups

