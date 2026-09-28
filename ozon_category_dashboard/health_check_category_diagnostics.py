"""Read-only category decline diagnostics for the Health Check report.

The module deliberately separates an observed association from a root cause.
It detects the first sustained decline in the primary outcome, compares equal
windows around that point, and exposes only hypotheses supported by available
warehouse data. Missing price/SPP history remains an explicit evidence gap.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs


VERSION = "2.6.0"
WINDOW_DAYS = 14
HISTORY_DAYS = 49
MIN_DECLINE_PCT = -10.0
DIRECTIONAL_FLOOR_PCT = 1.0


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0.0}:
        return None
    return (current / previous - 1.0) * 100.0


def _sum(rows: Iterable[Mapping[str, Any]], field: str) -> float:
    return sum(_number(row.get(field)) or 0.0 for row in rows)


def _mean(rows: Iterable[Mapping[str, Any]], field: str) -> float | None:
    values = [_number(row.get(field)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _ratio(rows: Iterable[Mapping[str, Any]], numerator: str, denominator: str) -> float | None:
    materialized = list(rows)
    top = _sum(materialized, numerator)
    bottom = _sum(materialized, denominator)
    return top / bottom * 100.0 if bottom else None


def _series_value(row: Mapping[str, Any], metric_id: str) -> float | None:
    if metric_id == "average_order_price_rub":
        units = _number(row.get("ordered_units")) or 0.0
        return (_number(row.get("ordered_revenue_rub")) or 0.0) / units if units else None
    if metric_id == "shown_product_price_rub":
        products = _number(row.get("shown_priced_products")) or 0.0
        return (_number(row.get("shown_product_price_sum")) or 0.0) / products if products else None
    if metric_id == "shown_product_discount_pct":
        products = _number(row.get("shown_discount_products")) or 0.0
        return (_number(row.get("shown_product_discount_sum")) or 0.0) / products if products else None
    if metric_id == "ad_ctr_pct":
        impressions = _number(row.get("ad_impressions")) or 0.0
        return (_number(row.get("ad_clicks")) or 0.0) / impressions * 100.0 if impressions else None
    if metric_id == "ad_click_to_cart_pct":
        clicks = _number(row.get("ad_clicks")) or 0.0
        return (_number(row.get("ad_cart_adds")) or 0.0) / clicks * 100.0 if clicks else None
    if metric_id == "ad_cart_to_order_pct":
        carts = _number(row.get("ad_cart_adds")) or 0.0
        return (_number(row.get("ad_orders")) or 0.0) / carts * 100.0 if carts else None
    if metric_id == "card_to_cart_pct":
        visits = _number(row.get("card_visits")) or 0.0
        return (_number(row.get("cart_adds")) or 0.0) / visits * 100.0 if visits else None
    if metric_id == "cart_to_order_pct":
        carts = _number(row.get("cart_adds")) or 0.0
        return (_number(row.get("ordered_units")) or 0.0) / carts * 100.0 if carts else None
    if metric_id == "ad_impression_share_pct":
        impressions = _number(row.get("impressions")) or 0.0
        return (_number(row.get("ad_impressions")) or 0.0) / impressions * 100.0 if impressions else None
    if metric_id == "organic_impression_share_pct":
        impressions = _number(row.get("impressions")) or 0.0
        return (_number(row.get("organic_impressions")) or 0.0) / impressions * 100.0 if impressions else None
    if metric_id == "organic_ctr_pct":
        impressions = _number(row.get("organic_impressions")) or 0.0
        return (_number(row.get("organic_card_visits")) or 0.0) / impressions * 100.0 if impressions else None
    if metric_id == "organic_card_to_cart_pct":
        visits = _number(row.get("organic_card_visits")) or 0.0
        return (_number(row.get("organic_cart_adds")) or 0.0) / visits * 100.0 if visits else None
    if metric_id == "organic_cart_to_order_pct":
        carts = _number(row.get("organic_cart_adds")) or 0.0
        return (_number(row.get("organic_orders")) or 0.0) / carts * 100.0 if carts else None
    if metric_id == "search_position_coverage_pct":
        listed_skus = _number(row.get("listed_skus")) or 0.0
        return (_number(row.get("positioned_skus")) or 0.0) / listed_skus * 100.0 if listed_skus else None
    if metric_id == "search_impressions_per_listed_sku":
        listed_skus = _number(row.get("listed_skus")) or 0.0
        return (_number(row.get("search_catalog_impressions")) or 0.0) / listed_skus if listed_skus else None
    if metric_id == "search_to_card_visit_pct":
        search_impressions = _number(row.get("search_catalog_impressions")) or 0.0
        return (_number(row.get("card_visits")) or 0.0) / search_impressions * 100.0 if search_impressions else None
    if metric_id == "ad_cpm_rub":
        impressions = _number(row.get("ad_impressions")) or 0.0
        return (_number(row.get("ad_spend_rub")) or 0.0) / impressions * 1000.0 if impressions else None
    if metric_id == "ad_cpc_rub":
        clicks = _number(row.get("ad_clicks")) or 0.0
        return (_number(row.get("ad_spend_rub")) or 0.0) / clicks if clicks else None
    return _number(row.get(metric_id))


def _period_value(rows: list[Mapping[str, Any]], metric_id: str, aggregation: str) -> float | None:
    if metric_id == "average_order_price_rub":
        units = _sum(rows, "ordered_units")
        return _sum(rows, "ordered_revenue_rub") / units if units else None
    if metric_id == "shown_product_price_rub":
        products = _sum(rows, "shown_priced_products")
        return _sum(rows, "shown_product_price_sum") / products if products else None
    if metric_id == "shown_product_discount_pct":
        products = _sum(rows, "shown_discount_products")
        return _sum(rows, "shown_product_discount_sum") / products if products else None
    if metric_id == "search_catalog_position":
        weight = _sum(rows, "search_position_impressions")
        return _sum(rows, "search_position_weighted_sum") / weight if weight else None
    if metric_id == "search_impressions_per_listed_sku":
        listed_skus = _sum(rows, "listed_skus")
        return _sum(rows, "search_catalog_impressions") / listed_skus if listed_skus else None
    ratios = {
        "ad_ctr_pct": ("ad_clicks", "ad_impressions"),
        "ad_click_to_cart_pct": ("ad_cart_adds", "ad_clicks"),
        "ad_cart_to_order_pct": ("ad_orders", "ad_cart_adds"),
        "card_to_cart_pct": ("cart_adds", "card_visits"),
        "cart_to_order_pct": ("ordered_units", "cart_adds"),
        "ad_impression_share_pct": ("ad_impressions", "impressions"),
        "organic_impression_share_pct": ("organic_impressions", "impressions"),
        "organic_ctr_pct": ("organic_card_visits", "organic_impressions"),
        "organic_card_to_cart_pct": ("organic_cart_adds", "organic_card_visits"),
        "organic_cart_to_order_pct": ("organic_orders", "organic_cart_adds"),
        "search_position_coverage_pct": ("positioned_skus", "listed_skus"),
        "search_to_card_visit_pct": ("card_visits", "search_catalog_impressions"),
    }
    if metric_id in ratios:
        return _ratio(rows, *ratios[metric_id])
    if metric_id == "ad_cpm_rub":
        impressions = _sum(rows, "ad_impressions")
        return _sum(rows, "ad_spend_rub") / impressions * 1000.0 if impressions else None
    if metric_id == "ad_cpc_rub":
        clicks = _sum(rows, "ad_clicks")
        return _sum(rows, "ad_spend_rub") / clicks if clicks else None
    if aggregation == "mean":
        return _mean(rows, metric_id)
    return _sum(rows, metric_id)


def _status(change_pct: float | None, *, direction: str, threshold: float = 10.0) -> str:
    """Confirm direction separately from materiality.

    `threshold` remains the business-materiality marker. A real directional
    movement of at least one percent is still evidence, even when it is a
    smaller supporting driver than the configured materiality threshold.
    """
    if change_pct is None:
        return "evidence_gap"
    if direction == "up_is_bad":
        return "supported" if change_pct >= DIRECTIONAL_FLOOR_PCT else "refuted"
    return "supported" if change_pct <= -DIRECTIONAL_FLOOR_PCT else "refuted"


def _format_absolute_delta(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    amount = abs(value)
    suffix = ""
    if unit == "pct":
        suffix = " п.п."
    elif unit == "rub":
        suffix = " ₽"
    elif amount >= 1_000_000:
        amount /= 1_000_000
        suffix = " млн"
    elif amount >= 1_000:
        amount /= 1_000
        suffix = " тыс."
    digits = 0 if amount >= 100 or unit == "rub" and amount >= 100 else 1
    rendered = f"{amount:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{sign}{rendered}{suffix}"


def _metric_context_note(rows: list[Mapping[str, Any]], metric_id: str) -> str:
    if metric_id not in {"shown_product_price_rub", "shown_product_discount_pct"}:
        return ""
    snapshot_dates = sorted({
        day for row in rows
        if (day := _date(row.get("catalog_price_snapshot_date"))) is not None
    })
    shown_products = _sum(rows, "shown_products")
    covered_products = _sum(
        rows,
        "shown_discount_products" if metric_id == "shown_product_discount_pct" else "shown_priced_products",
    )
    coverage = covered_products / shown_products * 100.0 if shown_products else None
    date_text = snapshot_dates[-1].strftime("%d.%m.%Y") if snapshot_dates else "неизвестна"
    coverage_text = f" Покрытие цены: {coverage:.1f}%." if coverage is not None else ""
    definition = (
        "Скидка = (цена до скидки − текущая цена) / цена до скидки. "
        if metric_id == "shown_product_discount_pct" else ""
    )
    return (
        f"{definition}Каталожный снимок: {date_text}; сравнение показывает изменение "
        f"состава товаров с показами, а не ежедневное изменение прайса.{coverage_text}"
    )


def _row_dates(rows: Iterable[Mapping[str, Any]]) -> set[date]:
    return {day for row in rows if (day := _date(row.get("report_date"))) is not None}


def _detect_onset(rows: list[Mapping[str, Any]], cutoff: date, metric_id: str = "ordered_revenue_rub") -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: _date(row.get("report_date")) or date.min)
    candidates: list[tuple[int, float, float]] = []
    values = [_series_value(row, metric_id) for row in ordered]
    scan_start = max(9, len(ordered) - 35)
    for index in range(scan_start, len(ordered)):
        current_values = [value for value in values[index - 2:index + 1] if value is not None]
        baseline_values = [value for value in values[index - 9:index - 2] if value is not None]
        if len(current_values) < 2 or len(baseline_values) < 4:
            continue
        current = median(current_values)
        baseline = median(baseline_values)
        if baseline > 0 and current <= baseline * 0.85:
            candidates.append((index, current, baseline))
    onset_index = None
    for index, current, baseline in candidates:
        indexes = {item[0] for item in candidates}
        if index + 1 in indexes and index + 2 in indexes:
            onset_index = index
            break
    method = "sustained_3d_vs_previous_7d_median"
    confidence = "medium"
    if onset_index is None:
        fallback = max(0, len(ordered) - WINDOW_DAYS)
        onset_index = fallback
        method = "fallback_last_14_observed_days"
        confidence = "low"
    onset = _date(ordered[onset_index].get("report_date")) if ordered else cutoff
    after_available = max(1, len(ordered) - onset_index)
    before_available = max(1, onset_index)
    window = min(WINDOW_DAYS, after_available, before_available)
    before_rows = ordered[max(0, onset_index - window):onset_index]
    after_rows = ordered[onset_index:onset_index + window]
    return {
        "date": onset.isoformat() if onset else cutoff.isoformat(),
        "method": method,
        "confidence": confidence,
        "threshold_pct": -15.0,
        "sustained_days": 3,
        "metric_id": metric_id,
        "window_observed_days": window,
        "before_rows": before_rows,
        "after_rows": after_rows,
    }


def _daily_category_rows(app: Any, marketplace: str, start: date, cutoff: date) -> list[dict[str, Any]]:
    if marketplace == "wb":
        return _daily_category_rows_wb(app, start, cutoff)
    funnel_view = "mv_ozon_funnel_daily_by_article_category" if marketplace == "ozon" else "mv_wb_funnel_daily_by_article_category"
    adv_view = "mv_ozon_adv_daily_by_article_category" if marketplace == "ozon" else "mv_wb_adv_daily_by_article_category"
    query = f"""
        WITH catalog_price AS (
            SELECT DISTINCT ON (product_id::text)
                product_id::text AS product_id,
                nullif(regexp_replace(replace(tsena_rub, ',', '.'), '[^0-9.-]', '', 'g'), '')::numeric AS current_price,
                nullif(regexp_replace(replace(tsena_do_skidki_rub, ',', '.'), '[^0-9.-]', '', 'g'), '')::numeric AS old_price,
                imported_at::date AS snapshot_date
            FROM public.ozon_cat_products
            WHERE product_id IS NOT NULL
            ORDER BY product_id::text, imported_at DESC, source_row_num DESC
        ), funnel_rows AS (
            SELECT
                v.*,
                row_number() OVER (
                    PARTITION BY v.report_date, nullif(trim(v.category_name), ''), v.product_id
                    ORDER BY v.source_row_num NULLS LAST, v.sku::text
                ) AS product_row_no,
                sum(coalesce(v.impressions_total, 0)) OVER (
                    PARTITION BY v.report_date, nullif(trim(v.category_name), ''), v.product_id
                ) AS product_impressions
            FROM public.{funnel_view} v
            WHERE v.report_date BETWEEN %s AND %s
              AND nullif(trim(v.category_name), '') IS NOT NULL
        ), funnel AS (
            SELECT
                report_date,
                nullif(trim(category_name), '') AS category_name,
                bool_and(ordered_amount_rub IS NOT NULL AND ordered_units IS NOT NULL
                    AND impressions_total IS NOT NULL AND card_visits IS NOT NULL
                    AND cart_adds IS NOT NULL) AS funnel_complete,
                sum(coalesce(ordered_amount_rub, 0))::numeric AS ordered_revenue_rub,
                sum(coalesce(ordered_units, 0))::numeric AS ordered_units,
                sum(coalesce(impressions_total, 0))::numeric AS impressions,
                sum(coalesce(impressions_search_catalog, 0))::numeric AS search_catalog_impressions,
                sum(CASE
                    WHEN coalesce(search_catalog_position, 0) > 0
                     AND coalesce(impressions_search_catalog, 0) > 0
                    THEN search_catalog_position * impressions_search_catalog
                    ELSE 0
                END)::numeric AS search_position_weighted_sum,
                sum(CASE
                    WHEN coalesce(search_catalog_position, 0) > 0
                     AND coalesce(impressions_search_catalog, 0) > 0
                    THEN impressions_search_catalog
                    ELSE 0
                END)::numeric AS search_position_impressions,
                sum(coalesce(card_visits, 0))::numeric AS card_visits,
                sum(coalesce(cart_adds, 0))::numeric AS cart_adds,
                count(DISTINCT coalesce(product_artikul, seller_article, sku::text))
                    FILTER (WHERE coalesce(ordered_units, 0) > 0)::numeric AS selling_skus,
                count(DISTINCT coalesce(product_artikul, seller_article, sku::text))
                    FILTER (WHERE coalesce(search_catalog_position, 0) > 0)::numeric AS positioned_skus,
                count(DISTINCT coalesce(product_artikul, seller_article, sku::text))::numeric AS listed_skus,
                count(*) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL AND product_impressions > 0
                )::numeric AS shown_products,
                sum(p.current_price) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL
                      AND product_impressions > 0 AND p.current_price > 0
                )::numeric AS shown_product_price_sum,
                count(*) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL
                      AND product_impressions > 0 AND p.current_price > 0
                )::numeric AS shown_priced_products,
                sum((p.old_price - p.current_price) / nullif(p.old_price, 0) * 100) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL
                      AND product_impressions > 0 AND p.current_price > 0 AND p.old_price > 0
                )::numeric AS shown_product_discount_sum,
                count(*) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL
                      AND product_impressions > 0 AND p.current_price > 0 AND p.old_price > 0
                )::numeric AS shown_discount_products,
                max(p.snapshot_date) FILTER (
                    WHERE product_row_no = 1 AND funnel_rows.product_id IS NOT NULL AND product_impressions > 0
                ) AS catalog_price_snapshot_date
            FROM funnel_rows
            LEFT JOIN catalog_price p ON p.product_id = funnel_rows.product_id::text
            GROUP BY report_date, nullif(trim(category_name), '')
        ), adv AS (
            SELECT
                report_date,
                nullif(trim(category_name), '') AS category_name,
                sum(coalesce(impressions, 0))::numeric AS ad_impressions,
                sum(coalesce(clicks, 0))::numeric AS ad_clicks,
                sum(coalesce(added_to_cart, 0))::numeric AS ad_cart_adds,
                sum(coalesce(orders_qty, 0))::numeric AS ad_orders,
                sum(coalesce(expense_rub, 0))::numeric AS ad_spend_rub,
                count(DISTINCT coalesce(product_artikul, seller_article, sku::text))
                    FILTER (WHERE coalesce(impressions, 0) > 0)::numeric AS promoted_skus,
                count(DISTINCT campaign_id)
                    FILTER (WHERE coalesce(impressions, 0) > 0)::numeric AS active_campaigns
            FROM public.{adv_view}
            WHERE report_date BETWEEN %s AND %s
              AND nullif(trim(category_name), '') IS NOT NULL
            GROUP BY report_date, nullif(trim(category_name), '')
        )
        SELECT
            coalesce(f.report_date, a.report_date) AS report_date,
            coalesce(f.category_name, a.category_name) AS category_name,
            coalesce(f.funnel_complete, false) AS has_funnel,
            a.report_date IS NOT NULL AS has_adv,
            f.ordered_revenue_rub, f.ordered_units, f.impressions,
            f.search_catalog_impressions,
            f.search_position_weighted_sum, f.search_position_impressions,
            CASE WHEN coalesce(f.search_position_impressions, 0) > 0
                THEN f.search_position_weighted_sum / f.search_position_impressions
                ELSE NULL END::numeric AS search_catalog_position,
            f.card_visits, f.cart_adds, f.selling_skus, f.positioned_skus, f.listed_skus,
            f.shown_products, f.shown_product_price_sum, f.shown_priced_products,
            f.shown_product_discount_sum, f.shown_discount_products, f.catalog_price_snapshot_date,
            a.ad_impressions, a.ad_clicks, a.ad_cart_adds, a.ad_orders,
            a.ad_spend_rub, a.promoted_skus, a.active_campaigns,
            greatest(coalesce(f.impressions, 0) - coalesce(a.ad_impressions, 0), 0)::numeric AS organic_impressions,
            greatest(coalesce(f.card_visits, 0) - coalesce(a.ad_clicks, 0), 0)::numeric AS organic_card_visits,
            greatest(coalesce(f.cart_adds, 0) - coalesce(a.ad_cart_adds, 0), 0)::numeric AS organic_cart_adds,
            greatest(coalesce(f.ordered_units, 0) - coalesce(a.ad_orders, 0), 0)::numeric AS organic_orders
        FROM funnel f
        FULL JOIN adv a USING (report_date, category_name)
        ORDER BY 1, 2
    """
    with app.get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, [start, cutoff, start, cutoff])
        return [dict(row) for row in cur.fetchall()]



def _daily_category_rows_wb(app: Any, start: date, cutoff: date) -> list[dict[str, Any]]:
    """Aggregate canonical WB history without requiring derivative views."""
    query = """
        WITH funnel_rows AS MATERIALIZED (
            SELECT report_date,
                   nullif(trim(category_name), '') AS category_name,
                   coalesce(nullif(trim(seller_article), ''), wb_nmid::text) AS sku_key,
                   ordered_amount_rub, ordered_units, impressions_total,
                   card_visits, cart_adds, avg_price_rub
            FROM public.wb_funnel_daily
            WHERE report_date BETWEEN %s AND %s
              AND nullif(trim(category_name), '') IS NOT NULL
        ), funnel AS MATERIALIZED (
            SELECT report_date, category_name,
                   bool_and(ordered_amount_rub IS NOT NULL AND ordered_units IS NOT NULL
                       AND impressions_total IS NOT NULL AND card_visits IS NOT NULL
                       AND cart_adds IS NOT NULL) AS funnel_complete,
                   sum(coalesce(ordered_amount_rub, 0))::numeric AS ordered_revenue_rub,
                   sum(coalesce(ordered_units, 0))::numeric AS ordered_units,
                   sum(coalesce(impressions_total, 0))::numeric AS impressions,
                   sum(coalesce(card_visits, 0))::numeric AS card_visits,
                   sum(coalesce(cart_adds, 0))::numeric AS cart_adds,
                   count(DISTINCT sku_key) FILTER (WHERE coalesce(ordered_units, 0) > 0)::numeric AS selling_skus,
                   count(DISTINCT sku_key)::numeric AS listed_skus,
                   count(DISTINCT sku_key) FILTER (WHERE coalesce(impressions_total, 0) > 0)::numeric AS shown_products,
                   sum(avg_price_rub) FILTER (WHERE coalesce(impressions_total, 0) > 0 AND avg_price_rub > 0)::numeric AS shown_product_price_sum,
                   count(*) FILTER (WHERE coalesce(impressions_total, 0) > 0 AND avg_price_rub > 0)::numeric AS shown_priced_products,
                   max(report_date) FILTER (WHERE coalesce(impressions_total, 0) > 0 AND avg_price_rub > 0) AS catalog_price_snapshot_date
            FROM funnel_rows
            GROUP BY report_date, category_name
        ), article_category AS MATERIALIZED (
            SELECT sku_key, max(category_name) AS category_name
            FROM funnel_rows
            WHERE sku_key IS NOT NULL
            GROUP BY sku_key
        ), adv AS MATERIALIZED (
            SELECT a.report_date, c.category_name,
                   sum(coalesce(a.impressions, 0))::numeric AS ad_impressions,
                   sum(coalesce(a.clicks, 0))::numeric AS ad_clicks,
                   sum(coalesce(a.added_to_cart, 0))::numeric AS ad_cart_adds,
                   sum(coalesce(a.orders_qty, 0))::numeric AS ad_orders,
                   sum(coalesce(a.expense_rub, 0))::numeric AS ad_spend_rub,
                   count(DISTINCT a.seller_article) FILTER (WHERE coalesce(a.impressions, 0) > 0)::numeric AS promoted_skus
            FROM public.wb_adv_daily_raw a
            JOIN article_category c ON c.sku_key = nullif(trim(a.seller_article), '')
            WHERE a.report_date BETWEEN %s AND %s
            GROUP BY a.report_date, c.category_name
        )
        SELECT coalesce(f.report_date, a.report_date) AS report_date,
               coalesce(f.category_name, a.category_name) AS category_name,
               coalesce(f.funnel_complete, false) AS has_funnel,
               a.report_date IS NOT NULL AS has_adv,
               f.ordered_revenue_rub, f.ordered_units, f.impressions,
               NULL::numeric AS search_catalog_impressions,
               NULL::numeric AS search_position_weighted_sum,
               NULL::numeric AS search_position_impressions,
               NULL::numeric AS search_catalog_position,
               f.card_visits, f.cart_adds, f.selling_skus,
               NULL::numeric AS positioned_skus, f.listed_skus,
               f.shown_products, f.shown_product_price_sum, f.shown_priced_products,
               NULL::numeric AS shown_product_discount_sum,
               NULL::numeric AS shown_discount_products,
               f.catalog_price_snapshot_date,
               a.ad_impressions, a.ad_clicks, a.ad_cart_adds, a.ad_orders,
               a.ad_spend_rub, a.promoted_skus, NULL::numeric AS active_campaigns,
               greatest(coalesce(f.impressions, 0) - coalesce(a.ad_impressions, 0), 0)::numeric AS organic_impressions,
               greatest(coalesce(f.card_visits, 0) - coalesce(a.ad_clicks, 0), 0)::numeric AS organic_card_visits,
               greatest(coalesce(f.cart_adds, 0) - coalesce(a.ad_cart_adds, 0), 0)::numeric AS organic_cart_adds,
               greatest(coalesce(f.ordered_units, 0) - coalesce(a.ad_orders, 0), 0)::numeric AS organic_orders
        FROM funnel f
        FULL JOIN adv a USING (report_date, category_name)
        ORDER BY 1, 2
    """
    with app.get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, [start, cutoff, start, cutoff])
        return [dict(row) for row in cur.fetchall()]

def _rank_category_movements(rows: list[Mapping[str, Any]], cutoff: date) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("category_name"):
            grouped[str(row["category_name"])].append(row)
    current_start = cutoff - timedelta(days=WINDOW_DAYS - 1)
    previous_start = current_start - timedelta(days=WINDOW_DAYS)
    previous_end = current_start - timedelta(days=1)
    falling: list[dict[str, Any]] = []
    growing: list[dict[str, Any]] = []
    total_previous = 0.0
    total_current = 0.0
    for category, category_rows in grouped.items():
        current = [row for row in category_rows if current_start <= (_date(row.get("report_date")) or date.min) <= cutoff and row.get("has_funnel")]
        previous = [row for row in category_rows if previous_start <= (_date(row.get("report_date")) or date.min) <= previous_end and row.get("has_funnel")]
        current_value = _sum(current, "ordered_revenue_rub")
        previous_value = _sum(previous, "ordered_revenue_rub")
        total_previous += previous_value
        total_current += current_value
        change_pct = _pct_change(current_value, previous_value)
        base = {
            "category": category,
            "previous": previous_value,
            "current": current_value,
            "change_pct": change_pct,
            "previous_observed_days": len(_row_dates(previous)),
            "current_observed_days": len(_row_dates(current)),
            "is_new": previous_value <= 0 < current_value,
        }
        if previous_value > 0 and change_pct is not None and change_pct <= MIN_DECLINE_PCT:
            falling.append({**base, "lost_revenue_rub": max(previous_value - current_value, 0.0)})
        elif current_value > 0 and (base["is_new"] or (change_pct is not None and change_pct >= abs(MIN_DECLINE_PCT))):
            growing.append({**base, "gained_revenue_rub": max(current_value - previous_value, 0.0)})
    falling.sort(key=lambda item: (-item["lost_revenue_rub"], item["change_pct"] or 0.0))
    growing.sort(key=lambda item: (-item["gained_revenue_rub"], -(item["change_pct"] or 0.0)))
    total_loss = sum(item["lost_revenue_rub"] for item in falling)
    total_gain = sum(item["gained_revenue_rub"] for item in growing)
    compensation_pct = total_gain / total_loss * 100.0 if total_loss else None
    if total_loss and total_gain and (compensation_pct or 0.0) >= 35.0:
        shift_status = "possible_seasonal_shift"
        shift_label = "Возможен сезонный сдвиг"
        shift_message = (
            f"Рост других категорий компенсирует {compensation_pct:.1f}% потери падающих. "
            "Это признак перераспределения спроса; для подтверждения сезонности нужен год-к-году или более длинная история."
        )
    elif total_loss and total_gain:
        shift_status = "limited_reallocation"
        shift_label = "Перераспределение ограничено"
        shift_message = (
            f"Рост других категорий компенсирует только {compensation_pct:.1f}% потери. "
            "Сезонный сдвиг возможен, но пока не объясняет основную часть снижения."
        )
    elif total_loss:
        shift_status = "decline_without_offset"
        shift_label = "Компенсирующего роста не найдено"
        shift_message = "В сопоставимом окне нет категорий с ростом не менее 10%; сезонный переток не подтверждается."
    else:
        shift_status = "no_decline_signal"
        shift_label = "Выраженного падения нет"
        shift_message = "На выбранных окнах нет достаточного сигнала для оценки перераспределения категорий."
    return {
        "falling": falling[:8],
        "growing": growing[:8],
        "shift": {
            "status": shift_status,
            "label": shift_label,
            "message": shift_message,
            "total_loss_rub": total_loss,
            "total_gain_rub": total_gain,
            "compensation_pct": compensation_pct,
            "net_gmv_change_rub": total_current - total_previous,
            "previous_gmv_rub": total_previous,
            "current_gmv_rub": total_current,
            "leading_growth_category": growing[0]["category"] if growing else None,
            "comparison": "Последние 14 дней против предыдущих 14; порог движения ±10%.",
            "causal_limit": "Краткосрочное перераспределение категорий является гипотезой сезонности, а не её доказательством.",
        },
    }


def _rank_categories(rows: list[Mapping[str, Any]], cutoff: date) -> list[dict[str, Any]]:
    """Backward-compatible falling-category ranking."""
    return list(_rank_category_movements(rows, cutoff)["falling"])

def _oos_evidence(app: Any, category: str, before_rows: list[Mapping[str, Any]], after_rows: list[Mapping[str, Any]], cutoff: date) -> dict[str, Any]:
    """Find SKU that sold during the last seven days but are out of stock now.

    The category belongs to the sales funnel. Inventory category_name is not used:
    Gloria Jeans inventory snapshots do not populate it. Missing inventory matches
    remain an evidence gap and are never coerced to zero stock.
    """
    del before_rows, after_rows
    sales_from = cutoff - timedelta(days=6)
    sales_to = cutoff
    query = """
        WITH latest_stock_date AS MATERIALIZED (
            SELECT max(snapshot_date) AS snapshot_date
            FROM public.inventory_history_daily
            WHERE marketplace = 'ozon'
        ), recent_daily AS MATERIALIZED (
            SELECT report_date,
                   sku::text AS sku_key,
                   max(product_name) AS product_name,
                   sum(coalesce(ordered_units, 0))::numeric AS units,
                   sum(coalesce(ordered_amount_rub, 0))::numeric AS revenue_rub
            FROM public.mv_ozon_funnel_daily_by_article_category
            WHERE category_name = %s
              AND report_date BETWEEN %s AND %s
            GROUP BY 1, 2
        ), recent_sales AS MATERIALIZED (
            SELECT sku_key,
                   max(product_name) AS product_name,
                   sum(units)::numeric AS recent_units,
                   sum(revenue_rub)::numeric AS recent_revenue_rub
            FROM recent_daily
            GROUP BY 1
            HAVING sum(units) > 0
        ), stock AS MATERIALIZED (
            SELECT i.sku::text AS sku_key,
                   sum(coalesce(stock_available_qty, 0))::numeric AS stock_qty
            FROM public.inventory_history_daily i
            CROSS JOIN latest_stock_date d
            JOIN recent_sales r ON r.sku_key = i.sku::text
            WHERE i.marketplace = 'ozon'
              AND i.snapshot_date = d.snapshot_date
            GROUP BY 1
        ), oos_candidates AS MATERIALIZED (
            SELECT r.sku_key, r.product_name, r.recent_units, r.recent_revenue_rub, s.stock_qty
            FROM recent_sales r
            JOIN stock s USING (sku_key)
            WHERE s.stock_qty = 0
        ), oos_daily AS (
            SELECT d.report_date, sum(d.units)::numeric AS units
            FROM recent_daily d
            JOIN oos_candidates c USING (sku_key)
            GROUP BY 1
            ORDER BY 1
        )
        SELECT d.snapshot_date,
               (SELECT count(*) FROM recent_sales) AS recent_selling_sku_count,
               (SELECT count(*) FROM recent_sales r JOIN stock s USING (sku_key)) AS matched_stock_sku_count,
               (SELECT count(*) FROM oos_candidates) AS affected_sku_count,
               coalesce((SELECT sum(recent_units) FROM oos_candidates), 0) AS affected_recent_units,
               coalesce((SELECT sum(recent_revenue_rub) FROM oos_candidates), 0) AS affected_recent_revenue_rub,
               coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'sku_key', sku_key,
                    'product_name', product_name,
                    'previous_units', recent_units,
                    'recent_revenue_rub', recent_revenue_rub,
                    'current_units', 0,
                    'stock_qty', stock_qty
               ) ORDER BY recent_revenue_rub DESC, recent_units DESC, sku_key) FROM oos_candidates), '[]'::jsonb) AS affected_skus,
               coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'date', report_date,
                    'value', units
               ) ORDER BY report_date) FROM oos_daily), '[]'::jsonb) AS daily_series
        FROM latest_stock_date d
    """
    try:
        with app.get_conn() as conn, conn.cursor() as cur:
            cur.execute(query, [category, sales_from, sales_to])
            summary = dict(cur.fetchone() or {})
    except Exception:
        return {
            "status": "evidence_gap", "affected_sku_count": 0, "affected_skus": [], "series": [],
            "reason": "Не удалось сопоставить продажи с последним снимком остатков по точному SKU.",
        }
    snapshot_date = _date(summary.get("snapshot_date"))
    recent_sku_count = int(_number(summary.get("recent_selling_sku_count")) or 0)
    matched_sku_count = int(_number(summary.get("matched_stock_sku_count")) or 0)
    affected_count = int(_number(summary.get("affected_sku_count")) or 0)
    affected = list(summary.get("affected_skus") or [])
    series = list(summary.get("daily_series") or [])
    if snapshot_date is None or (recent_sku_count > 0 and matched_sku_count == 0):
        status = "evidence_gap"
        reason = "У продававшихся SKU нет точного соответствия в последнем снимке остатков; отсутствующий остаток не считается нулём."
    elif affected_count:
        status = "supported"
        reason = (
            f"Найдено {affected_count} SKU: были продажи за последние 7 дней, "
            f"а на снимке {snapshot_date.strftime('%d.%m.%Y')} доступный остаток равен нулю."
        )
    else:
        status = "refuted"
        reason = (
            f"Среди {matched_sku_count} продававшихся SKU нет позиций с нулевым остатком "
            f"на снимке {snapshot_date.strftime('%d.%m.%Y')}."
        )
    return {
        "status": status,
        "affected_sku_count": affected_count,
        "affected_skus": affected,
        "previous_units": _number(summary.get("affected_recent_units")) if affected_count else None,
        "previous_revenue_rub": _number(summary.get("affected_recent_revenue_rub")) if affected_count else None,
        "current_units": 0 if affected_count else None,
        "series": [{"date": str(point.get("date") or ""), "value": _number(point.get("value"))} for point in series],
        "sales_period": {"from": sales_from.isoformat(), "to": sales_to.isoformat()},
        "stock_period": {"from": snapshot_date.isoformat(), "to": snapshot_date.isoformat()} if snapshot_date else None,
        "comparison_label": "Правило OOS выполнено" if status == "supported" else "OOS не подтверждён" if status == "refuted" else "Пробел в остатках",
        "reason": reason,
    }


def build_oos_workbook(app: Any, diagnostic: Mapping[str, Any]) -> tuple[bytes, str]:
    """Build the complete revenue-ranked OOS list with the project's XLSX stack."""
    hypotheses = list(diagnostic.get("hypotheses") or [])
    item = next((row for row in hypotheses if row.get("id") == "stockout"), None)
    if not item:
        raise ValueError("OOS-диагностика отсутствует в ответе.")
    affected = sorted(
        list(item.get("affected_skus") or []),
        key=lambda row: (
            -(_number(row.get("recent_revenue_rub")) or 0.0),
            -(_number(row.get("previous_units")) or 0.0),
            str(row.get("sku_key") or ""),
        ),
    )
    sales_period = item.get("before_period") or {}
    stock_period = item.get("after_period") or {}
    category = str(diagnostic.get("selected_category") or "")

    workbook = app.Workbook()
    sheet = workbook.active
    sheet.title = "OOS"
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells("A1:J1")
    sheet["A1"] = "Health Check · товары с продажами за 7 дней и текущим остатком 0"
    sheet["A1"].font = app.Font(bold=True, color="FFFFFF", size=14)
    sheet["A1"].fill = app.PatternFill("solid", fgColor="111827")
    sheet["A1"].alignment = app.Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet["A2"] = "Категория"
    sheet["B2"] = category
    sheet["D2"] = "OOS SKU"
    sheet["E2"] = len(affected)
    sheet["G2"] = "Сортировка"
    sheet["H2"] = "Продажи, ₽ по убыванию"
    sheet["A3"] = "Период продаж"
    sheet["B3"] = f"{sales_period.get('from') or '—'} — {sales_period.get('to') or '—'}"
    sheet["D3"] = "Снимок остатков"
    sheet["E3"] = stock_period.get("to") or stock_period.get("from") or "—"
    sheet["G3"] = "Продажи OOS, ₽"
    sheet["H3"] = sum(_number(row.get("recent_revenue_rub")) or 0.0 for row in affected)
    sheet["H3"].number_format = '#,##0.00 [$₽-ru-RU]'
    for cell in (sheet["A2"], sheet["D2"], sheet["G2"], sheet["A3"], sheet["D3"], sheet["G3"]):
        cell.font = app.Font(bold=True, color="344054")

    headers = [
        "№", "SKU Ozon", "Товар", "Продажи за 7 дней, ₽", "Продажи за 7 дней, шт.",
        "Остаток сейчас, шт.", "Период продаж с", "Период продаж по", "Дата снимка остатков", "Категория",
    ]
    sheet.append([])
    sheet.append(headers)
    header_row = 5
    sales_from = _date(sales_period.get("from"))
    sales_to = _date(sales_period.get("to"))
    stock_date = _date(stock_period.get("to") or stock_period.get("from"))
    for index, row in enumerate(affected, start=1):
        sheet.append([
            index,
            str(row.get("sku_key") or ""),
            str(row.get("product_name") or ""),
            _number(row.get("recent_revenue_rub")) or 0.0,
            _number(row.get("previous_units")) or 0.0,
            _number(row.get("stock_qty")) or 0.0,
            sales_from,
            sales_to,
            stock_date,
            category,
        ])

    header_fill = app.PatternFill("solid", fgColor="000000")
    header_font = app.Font(bold=True, color="FFFFFF")
    border_color = app.Side(style="thin", color="D0D5DD")
    for cell in sheet[header_row]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = app.Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = app.Border(bottom=border_color)
    for row in sheet.iter_rows(min_row=header_row + 1, max_row=sheet.max_row):
        for cell in row:
            cell.border = app.Border(bottom=app.Side(style="hair", color="EAECF0"))
            cell.alignment = app.Alignment(vertical="top", wrap_text=cell.column in {3, 10})
    for row_index in range(header_row + 1, sheet.max_row + 1):
        sheet.cell(row_index, 2).number_format = "@"
        sheet.cell(row_index, 4).number_format = '#,##0.00 [$₽-ru-RU]'
        sheet.cell(row_index, 5).number_format = "#,##0"
        sheet.cell(row_index, 6).number_format = "#,##0"
        for column in (7, 8, 9):
            sheet.cell(row_index, column).number_format = "yyyy-mm-dd"
    widths = {1: 14, 2: 16, 3: 48, 4: 23, 5: 22, 6: 22, 7: 18, 8: 18, 9: 23, 10: 22}
    for column, width in widths.items():
        sheet.column_dimensions[app.get_column_letter(column)].width = width
    sheet.freeze_panes = "A6"
    sheet.auto_filter.ref = f"A{header_row}:J{max(header_row, sheet.max_row)}"

    output = app.BytesIO()
    workbook.save(output)
    filename_date = stock_date.isoformat() if stock_date else date.today().isoformat()
    return output.getvalue(), f"health_check_oos_{filename_date}.xlsx"


def _hypothesis(
    *,
    hypothesis_id: str,
    group: str,
    title: str,
    metric_id: str | None,
    unit: str,
    aggregation: str,
    before_rows: list[Mapping[str, Any]],
    after_rows: list[Mapping[str, Any]],
    direction: str = "down_is_bad",
    threshold: float = 10.0,
    drill_metric_id: str | None = None,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    if unavailable_reason:
        return {
            "id": hypothesis_id, "group": group, "title": title,
            "status": "evidence_gap", "status_label": "Недостаточно данных",
            "metric_id": metric_id, "drill_metric_id": drill_metric_id,
            "unit": unit, "before": None, "after": None, "change_pct": None,
            "evidence": unavailable_reason, "series": [],
        }
    before = _period_value(before_rows, metric_id or "", aggregation)
    after = _period_value(after_rows, metric_id or "", aggregation)
    change_pct = _pct_change(after, before)
    absolute_change = after - before if after is not None and before is not None else None
    status = _status(change_pct, direction=direction, threshold=threshold)
    is_material = change_pct is not None and abs(change_pct) >= threshold
    effect_strength = (
        "material" if is_material else
        "supporting" if change_pct is not None and abs(change_pct) >= DIRECTIONAL_FLOOR_PCT else
        "neutral"
    )
    status_labels = {"supported": "Подтверждается", "refuted": "Не подтверждается", "evidence_gap": "Недостаточно данных"}
    verb = "выросла" if direction == "up_is_bad" else "снизилась"
    if status == "supported" and change_pct is not None:
        scale_note = (
            "Направление и масштаб подтверждают гипотезу."
            if is_material else
            "Направление подтверждает гипотезу; влияние небольшое и считается вспомогательным."
        )
        evidence = (
            f"Метрика {verb} на {abs(change_pct):.1f}% "
            f"({_format_absolute_delta(absolute_change, unit)}). {scale_note}"
        )
    elif status == "refuted" and change_pct is not None and abs(change_pct) < DIRECTIONAL_FLOOR_PCT:
        evidence = (
            f"Изменение {change_pct:+.1f}% ({_format_absolute_delta(absolute_change, unit)}) "
            f"находится в нейтральной зоне ±{DIRECTIONAL_FLOOR_PCT:.1f}%."
        )
    elif status == "refuted" and change_pct is not None:
        actual_verb = "выросла" if change_pct > 0 else "снизилась"
        evidence = (
            f"Метрика {actual_verb} на {abs(change_pct):.1f}% "
            f"({_format_absolute_delta(absolute_change, unit)}): направление противоположно гипотезе."
        )
    else:
        evidence = "Нет двух сопоставимых значений для проверки."
    context_note = _metric_context_note(before_rows + after_rows, metric_id or "")
    if context_note:
        evidence = f"{evidence} {context_note}"
    series_rows = (before_rows + after_rows)[-28:]
    return {
        "id": hypothesis_id, "group": group, "title": title,
        "status": status, "status_label": status_labels[status],
        "metric_id": metric_id, "drill_metric_id": drill_metric_id,
        "unit": unit, "before": before, "after": after, "change_pct": change_pct,
        "absolute_change": absolute_change, "directional_floor_pct": DIRECTIONAL_FLOOR_PCT,
        "materiality_threshold_pct": threshold, "is_material": is_material,
        "effect_strength": effect_strength, "evidence": evidence,
        "series": [{"date": (_date(row.get("report_date")) or date.min).isoformat(), "value": _series_value(row, metric_id or "")} for row in series_rows],
    }


def _metric_drill_specs(metric_id: str) -> tuple[bool, list[tuple[Any, ...]]]:
    """Return only measurable drivers for the selected metric.

    The first flag enables the SKU-level out-of-stock check. Specs intentionally
    contain no self-edge; deeper recursion is controlled by the frontend path.
    """
    specs: dict[str, tuple[bool, list[tuple[Any, ...]]]] = {
        "ordered_units": (True, [
            ("selling_skus_driver", "Доступность", "Сократилось число продающих SKU", "selling_skus", "count", "mean", "down_is_bad", 10, "selling_skus", None),
            ("order_price_driver", "Цена и скидки", "Средняя цена товаров с показами выросла (сдвиг микса)", "shown_product_price_rub", "rub", "ratio", "up_is_bad", 5, "shown_product_price_rub", None),
            ("card_to_cart_driver", "Воронка", "Упала конверсия карточка → корзина", "card_to_cart_pct", "pct", "ratio", "down_is_bad", 8, "card_to_cart_pct", None),
            ("cart_to_order_driver", "Воронка", "Упала конверсия корзина → заказ", "cart_to_order_pct", "pct", "ratio", "down_is_bad", 8, "cart_to_order_pct", None),
        ]),
        "organic_impressions": (False, [
            ("search_impressions_driver", "Поиск и каталог", "Упали показы в поиске и каталоге", "search_catalog_impressions", "count", "sum", "down_is_bad", 10, None, None),
            ("search_position_driver", "Поиск и каталог", "Ухудшилась средневзвешенная позиция в поиске и каталоге", "search_catalog_position", "count", "ratio", "up_is_bad", 8, None, None),
            ("search_position_coverage_driver", "Поиск и каталог", "Сократилась доля SKU с позицией в поиске и каталоге", "search_position_coverage_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("search_intensity_driver", "Поиск и каталог", "Упали показы поиска и каталога на доступный SKU", "search_impressions_per_listed_sku", "count", "ratio", "down_is_bad", 10, None, None),
            ("organic_share_driver", "Продвижение", "Снизилась доля органических показов", "organic_impression_share_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("search_to_card_driver", "Воронка", "Упала конверсия поиска и каталога → карточка", "search_to_card_visit_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("listed_skus_driver", "Доступность", "Сократился доступный ассортимент", "listed_skus", "count", "mean", "down_is_bad", 10, "listed_skus", None),
            ("search_query_gap", "Ограничения данных", "По каким поисковым запросам произошло изменение", None, "count", "mean", "down_is_bad", 10, None, "Позиции и показы доступны на уровне SKU, но история конкретных запросов Ozon, их частотности и конкурентной выдачи не подключена."),
        ]),
        "impressions": (False, [
            ("organic_visibility_driver", "Продвижение", "Упали органические показы", "organic_impressions", "count", "sum", "down_is_bad", 10, "organic_impressions", None),
            ("ad_visibility_driver", "Продвижение", "Упали рекламные показы", "ad_impressions", "count", "sum", "down_is_bad", 10, "ad_impressions", None),
            ("listed_skus_visibility", "Доступность", "Сократился доступный ассортимент", "listed_skus", "count", "mean", "down_is_bad", 10, "listed_skus", None),
        ]),
        "ad_impressions": (False, [
            ("ad_spend_driver", "Продвижение", "Снизился рекламный расход", "ad_spend_rub", "rub", "sum", "down_is_bad", 10, "ad_spend_rub", None),
            ("promoted_skus_driver", "Продвижение", "Снизилось число товаров в продвижении", "promoted_skus", "count", "mean", "down_is_bad", 10, "promoted_skus", None),
            ("active_campaigns_driver", "Продвижение", "Снизилось число активных кампаний", "active_campaigns", "count", "mean", "down_is_bad", 10, None, None),
            ("ad_cpm_driver", "Продвижение", "Вырос CPM и тот же бюджет купил меньше показов", "ad_cpm_rub", "rub", "ratio", "up_is_bad", 8, None, None),
        ]),
        "ad_spend_rub": (False, [
            ("spend_campaigns_driver", "Продвижение", "Снизилось число активных кампаний", "active_campaigns", "count", "mean", "down_is_bad", 10, None, None),
            ("spend_skus_driver", "Продвижение", "Снизилось число товаров в продвижении", "promoted_skus", "count", "mean", "down_is_bad", 10, "promoted_skus", None),
            ("spend_cpc_driver", "Продвижение", "Снизилась цена клика", "ad_cpc_rub", "rub", "ratio", "down_is_bad", 8, None, None),
            ("budget_gap", "Продвижение", "Снизились дневные бюджеты или лимиты", None, "rub", "sum", "down_is_bad", 10, None, "История настроек бюджетов и лимитов кампаний пока не подключена."),
        ]),
        "promoted_skus": (False, [
            ("promoted_campaigns_driver", "Продвижение", "Снизилось число активных кампаний", "active_campaigns", "count", "mean", "down_is_bad", 10, None, None),
            ("promoted_listed_driver", "Доступность", "Сократился доступный ассортимент", "listed_skus", "count", "mean", "down_is_bad", 10, "listed_skus", None),
        ]),
        "ad_ctr_pct": (False, [
            ("ad_clicks_driver", "Воронка", "Снизилось число рекламных кликов", "ad_clicks", "count", "sum", "down_is_bad", 10, None, None),
            ("ad_impressions_mix", "Воронка", "Показы выросли быстрее кликов", "ad_impressions", "count", "sum", "up_is_bad", 10, "ad_impressions", None),
            ("creative_gap", "Продвижение", "Ухудшились креативы или релевантность выдачи", None, "pct", "ratio", "down_is_bad", 8, None, "История креативов, ставок и поисковых фраз пока не подключена."),
        ]),
        "card_to_cart_pct": (False, [
            ("cart_adds_driver", "Воронка", "Снизилось число добавлений в корзину", "cart_adds", "count", "sum", "down_is_bad", 10, None, None),
            ("card_visits_mix", "Воронка", "Визиты выросли быстрее корзин", "card_visits", "count", "sum", "up_is_bad", 10, None, None),
            ("price_barrier_driver", "Цена и скидки", "Средняя цена товаров с показами выросла (сдвиг микса)", "shown_product_price_rub", "rub", "ratio", "up_is_bad", 5, "shown_product_price_rub", None),
            ("buyer_price_gap", "Цена и промо", "Цена покупателя выросла или СПП снизилась", None, "rub", "ratio", "up_is_bad", 5, None, "История цены покупателя и СПП пока не подключена."),
        ]),
        "cart_to_order_pct": (True, [
            ("orders_numerator_driver", "Воронка", "Снизилось число заказанных единиц", "ordered_units", "count", "sum", "down_is_bad", 10, "ordered_units", None),
            ("cart_denominator_mix", "Воронка", "Корзины выросли быстрее заказов", "cart_adds", "count", "sum", "up_is_bad", 10, None, None),
            ("checkout_price_gap", "Цена и промо", "Цена покупателя или условия покупки ухудшились", None, "rub", "ratio", "up_is_bad", 5, None, "История цены покупателя, СПП и условий доставки пока не подключена."),
        ]),
        "ad_click_to_cart_pct": (False, [
            ("ad_carts_driver", "Воронка", "Снизились рекламные добавления в корзину", "ad_cart_adds", "count", "sum", "down_is_bad", 10, None, None),
            ("ad_click_mix", "Воронка", "Клики выросли быстрее рекламных корзин", "ad_clicks", "count", "sum", "up_is_bad", 10, None, None),
            ("ad_price_barrier", "Цена и скидки", "Средняя цена товаров с показами выросла (сдвиг микса)", "shown_product_price_rub", "rub", "ratio", "up_is_bad", 5, "shown_product_price_rub", None),
        ]),
        "ad_cart_to_order_pct": (True, [
            ("ad_orders_driver", "Воронка", "Снизилось число рекламных заказов", "ad_orders", "count", "sum", "down_is_bad", 10, None, None),
            ("ad_cart_mix", "Воронка", "Рекламные корзины выросли быстрее заказов", "ad_cart_adds", "count", "sum", "up_is_bad", 10, None, None),
            ("ad_checkout_gap", "Цена и промо", "Цена покупателя или условия покупки ухудшились", None, "rub", "ratio", "up_is_bad", 5, None, "История цены покупателя, СПП и условий доставки пока не подключена."),
        ]),
        "selling_skus": (True, [
            ("listed_skus_driver", "Доступность", "Сократился доступный ассортимент", "listed_skus", "count", "mean", "down_is_bad", 10, "listed_skus", None),
        ]),
        "shown_product_price_rub": (False, [
            ("shown_discount_mix_driver", "Цена и скидки", "Средняя скидка товаров с показами снизилась (сдвиг микса)", "shown_product_discount_pct", "pct", "ratio", "down_is_bad", 5, None, None),
            ("buyer_price_history_gap", "Ограничения данных", "Изменялись ли цены и скидки самих товаров", None, "rub", "ratio", "up_is_bad", 5, None, "Есть один каталожный снимок на 16.07.2026, но нет посуточной истории прайса. Поэтому проверяется только сдвиг состава товаров с показами."),
            ("buyer_price_spp_gap", "Ограничения данных", "Изменилась ли цена покупателя или СПП", None, "rub", "ratio", "up_is_bad", 5, None, "Посуточная история цены покупателя и СПП не подключена."),
        ]),
        "listed_skus": (False, [
            ("catalog_gap", "Доступность", "Карточки скрыты, заблокированы или удалены", None, "count", "mean", "down_is_bad", 10, None, "История статусов и блокировок карточек пока не подключена."),
        ]),
    }
    return specs.get(metric_id, (False, [
        ("driver_contract_gap", "Воронка", "Для метрики ещё не подключено дерево драйверов", None, "count", "sum", "down_is_bad", 10, None, "Нужен отдельный контракт источника и причинных связей для выбранной метрики."),
    ]))


def _build_hypotheses(
    app: Any,
    category: str,
    onset: Mapping[str, Any],
    cutoff: date,
    *,
    marketplace: str = "ozon",
    metric_id: str = "ordered_revenue_rub",
    metric_drill: bool = False,
) -> list[dict[str, Any]]:
    before_rows = list(onset.get("before_rows") or [])
    after_rows = list(onset.get("after_rows") or [])
    if metric_drill:
        include_oos, specs = _metric_drill_specs(metric_id)
    else:
        include_oos = True
        specs = [
            ("selling_assortment", "Доступность", "Сократилось число продающих SKU", "selling_skus", "count", "mean", "down_is_bad", 10, "ordered_units", None),
            ("seller_price_mix", "Цена и скидки", "Средняя цена товаров с показами выросла (сдвиг микса)", "shown_product_price_rub", "rub", "ratio", "up_is_bad", 5, "shown_product_price_rub", None),
            ("seller_discount_mix", "Цена и скидки", "Средняя скидка товаров с показами снизилась (сдвиг микса)", "shown_product_discount_pct", "pct", "ratio", "down_is_bad", 5, None, None),
            ("buyer_price_spp", "Ограничения данных", "Цена покупателя или СПП ухудшились", None, "rub", "ratio", "up_is_bad", 5, None, "Посуточная история цены покупателя и СПП не подключена. Каталожный снимок 16.07.2026 показывает цену продавца и скидку, но не заменяет цену покупателя."),
            ("search_impressions", "Поиск и каталог", "Упали показы в поиске и каталоге", "search_catalog_impressions", "count", "sum", "down_is_bad", 10, None, None),
            ("search_position", "Поиск и каталог", "Ухудшилась средневзвешенная позиция в поиске и каталоге", "search_catalog_position", "count", "ratio", "up_is_bad", 8, None, None),
            ("promoted_skus", "Продвижение", "Снизилось число товаров в продвижении", "promoted_skus", "count", "mean", "down_is_bad", 10, "ad_impressions", None),
            ("active_campaigns", "Продвижение", "Снизилось число активных кампаний", "active_campaigns", "count", "mean", "down_is_bad", 10, "ad_impressions", None),
            ("ad_spend", "Продвижение", "Снизился рекламный расход", "ad_spend_rub", "rub", "sum", "down_is_bad", 10, "ad_spend_rub", None),
            ("ad_impressions", "Продвижение", "Упали рекламные показы", "ad_impressions", "count", "sum", "down_is_bad", 10, "ad_impressions", None),
            ("organic_impressions", "Органическая воронка", "Упали органические показы", "organic_impressions", "count", "sum", "down_is_bad", 10, "organic_impressions", None),
            ("organic_ctr", "Органическая воронка", "Упала органическая конверсия показ → карточка", "organic_ctr_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("organic_card_to_cart", "Органическая воронка", "Упала органическая конверсия карточка → корзина", "organic_card_to_cart_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("organic_cart_to_order", "Органическая воронка", "Упала органическая конверсия корзина → заказ", "organic_cart_to_order_pct", "pct", "ratio", "down_is_bad", 8, None, None),
            ("total_impressions", "Общая воронка", "Упали общие показы", "impressions", "count", "sum", "down_is_bad", 10, "impressions", None),
            ("card_to_cart", "Общая воронка", "Упала конверсия карточка → корзина", "card_to_cart_pct", "pct", "ratio", "down_is_bad", 8, "card_to_cart_pct", None),
            ("cart_to_order", "Общая воронка", "Упала конверсия корзина → заказ", "cart_to_order_pct", "pct", "ratio", "down_is_bad", 8, "cart_to_order_pct", None),
            ("ad_ctr", "Рекламная воронка", "Упал рекламный CTR", "ad_ctr_pct", "pct", "ratio", "down_is_bad", 8, "ad_ctr_pct", None),
            ("ad_click_to_cart", "Рекламная воронка", "Упала рекламная конверсия клик → корзина", "ad_click_to_cart_pct", "pct", "ratio", "down_is_bad", 8, "ad_click_to_cart_pct", None),
            ("ad_cart_to_order", "Рекламная воронка", "Упала рекламная конверсия корзина → заказ", "ad_cart_to_order_pct", "pct", "ratio", "down_is_bad", 8, "ad_cart_to_order_pct", None),
        ]
    result: list[dict[str, Any]] = []
    if marketplace == "wb":
        wb_gap_reasons = {
            "seller_discount_mix": "В WB-воронке нет сопоставимой посуточной скидки продавца; отсутствие значения не считается нулевой скидкой.",
            "buyer_price_spp": "Посуточная история цены покупателя и скидки WB не подключена.",
            "search_impressions": "WB-воронка Gloria Jeans не разделяет общие показы на поиск и каталог.",
            "search_position": "Посуточная позиция в поиске WB отсутствует в текущей категорийной витрине.",
            "active_campaigns": "В исходной WB-рекламе нет устойчивого идентификатора кампании для сопоставимого дневного счёта.",
            "search_impressions_driver": "WB-воронка Gloria Jeans не разделяет общие показы на поиск и каталог.",
            "search_position_driver": "Посуточная позиция в поиске WB отсутствует в текущей категорийной витрине.",
            "search_position_coverage_driver": "Покрытие позиций поиска WB отсутствует в текущей категорийной витрине.",
            "search_intensity_driver": "Показы поиска WB на доступный SKU отсутствуют в текущей категорийной витрине.",
            "search_to_card_driver": "Показы поиска WB отдельно от общих показов не подключены.",
        }
        specs = [(*item[:9], wb_gap_reasons.get(item[0], item[9])) for item in specs]
        wb_oos_requested = include_oos
        include_oos = False
        if wb_oos_requested:
            result.append({
                "id": "stockout", "group": "Доступность", "title": "Товары закончились",
                "status": "evidence_gap", "status_label": "Недостаточно данных",
                "metric_id": "oos_sku_share_pct", "drill_metric_id": None, "unit": "count",
                "before": None, "after": None, "change_pct": None, "series": [],
                "affected_sku_count": 0, "affected_skus": [],
                "evidence": "История остатков WB по дням для Gloria Jeans не загружена; отсутствие снимка не считается нулевым остатком.",
            })
    if include_oos:
        oos = _oos_evidence(app, category, before_rows, after_rows, cutoff)
        status_labels = {"supported": "Подтверждается", "refuted": "Не подтверждается", "evidence_gap": "Недостаточно данных"}
        result.append({
            "id": "stockout", "group": "Доступность", "title": "Товары закончились",
            "status": oos["status"], "status_label": status_labels[oos["status"]],
            "metric_id": "oos_sku_share_pct", "drill_metric_id": None, "unit": "rub",
            "before_unit": "rub", "after_unit": "count",
            "before": oos.get("previous_revenue_rub"), "after": oos.get("current_units"),
            "change_pct": None,
            "before_label": "Продажи за 7 дней, ₽", "after_label": "Остаток сейчас, шт.",
            "before_period": oos.get("sales_period"), "after_period": oos.get("stock_period"),
            "comparison_label": oos.get("comparison_label"),
            "evidence": oos["reason"], "series": oos.get("series") or [],
            "affected_sku_count": oos.get("affected_sku_count") or 0,
            "affected_skus": oos.get("affected_skus") or [],
        })
    for item in specs:
        result.append(_hypothesis(
            hypothesis_id=item[0], group=item[1], title=item[2], metric_id=item[3], unit=item[4],
            aggregation=item[5], direction=item[6], threshold=item[7], drill_metric_id=item[8],
            unavailable_reason=item[9], before_rows=before_rows, after_rows=after_rows,
        ))
    group_names = (
        ("Поиск и каталог", "Продвижение", "Воронка", "Доступность", "Цена и скидки", "Ограничения данных")
        if metric_drill and metric_id == "organic_impressions" else
        ("Доступность", "Цена и скидки", "Продвижение", "Воронка", "Ограничения данных")
        if metric_drill else
        ("Доступность", "Цена и скидки", "Поиск и каталог", "Продвижение", "Органическая воронка", "Общая воронка", "Рекламная воронка", "Ограничения данных")
    )
    group_order = {name: index for index, name in enumerate(group_names)}
    status_order = {"supported": 0, "evidence_gap": 1, "refuted": 2}
    return sorted(result, key=lambda item: (group_order.get(item["group"], 99), status_order[item["status"]]))

def analyze(app: Any, parsed: Any, context: Mapping[str, Any], metric_id: str) -> dict[str, Any]:
    params = parse_qs(str(getattr(parsed, "query", "") or ""), keep_blank_values=False)
    first = lambda key: str((params.get(key) or [""])[0]).strip()
    client = first("client") or str(context.get("client") or "")
    marketplace = (first("marketplace") or str(context.get("marketplace") or "ozon")).lower()
    requested_category = first("category")
    diagnostic_mode = first("diagnostic_mode")
    diagnostic_depth = min(max(int(first("diagnostic_depth") or 0), 0), 4)
    metric_drill = diagnostic_mode == "metric_drill"
    analysis_date = _date(context.get("analysis_date")) or date.today()
    token = app.CURRENT_CLIENT.set(client)
    try:
        rows = _daily_category_rows(app, marketplace, analysis_date - timedelta(days=HISTORY_DAYS), analysis_date)
        daily_revenue: dict[date, float] = defaultdict(float)
        for row in rows:
            day = _date(row.get("report_date"))
            if day is not None and row.get("has_funnel"):
                daily_revenue[day] += _number(row.get("ordered_revenue_rub")) or 0.0
        funnel_dates = sorted(day for day, revenue in daily_revenue.items() if revenue > 0)
        if not funnel_dates:
            return {"version": VERSION, "status": "evidence_gap", "categories": [], "selected_category": None, "message": "Нет дневных данных по категориям."}
        effective_cutoff = min(analysis_date, funnel_dates[-1])
        movements = _rank_category_movements(rows, effective_cutoff)
        ranked = list(movements["falling"])
        growing = list(movements["growing"])
        selected = requested_category or (ranked[0]["category"] if ranked else "")
        category_rows = [row for row in rows if str(row.get("category_name") or "") == selected and row.get("has_funnel") and (_date(row.get("report_date")) or date.min) <= effective_cutoff]
        onset_metric_id = metric_id if metric_drill else "ordered_revenue_rub"
        onset = _detect_onset(category_rows, effective_cutoff, onset_metric_id) if category_rows else None
        categories = []
        for row in ranked:
            category_onset = _detect_onset(
                [item for item in rows if str(item.get("category_name") or "") == row["category"] and item.get("has_funnel") and (_date(item.get("report_date")) or date.min) <= effective_cutoff],
                effective_cutoff,
            )
            categories.append({**row, "onset_date": category_onset["date"], "selected": row["category"] == selected})
        if not onset:
            return {"version": VERSION, "status": "evidence_gap", "categories": categories, "selected_category": selected or None, "message": "Для выбранной категории нет сопоставимой истории."}
        before_rows = list(onset.pop("before_rows"))
        after_rows = list(onset.pop("after_rows"))
        before_dates = sorted(_row_dates(before_rows))
        after_dates = sorted(_row_dates(after_rows))
        onset["before"] = {"from": before_dates[0].isoformat(), "to": before_dates[-1].isoformat(), "observed_days": len(before_dates)}
        onset["after"] = {"from": after_dates[0].isoformat(), "to": after_dates[-1].isoformat(), "observed_days": len(after_dates)}
        onset["before_rows"] = before_rows
        onset["after_rows"] = after_rows
        hypotheses = _build_hypotheses(
            app, selected, onset, effective_cutoff,
            marketplace=marketplace, metric_id=metric_id, metric_drill=metric_drill,
        )
        onset.pop("before_rows", None)
        onset.pop("after_rows", None)
        return {
            "version": VERSION,
            "status": "available",
            "primary_metric_id": "ordered_revenue_rub",
            "requested_metric_id": metric_id,
            "diagnostic_mode": diagnostic_mode or "root",
            "diagnostic_depth": diagnostic_depth,
            "effective_cutoff": effective_cutoff.isoformat(),
            "categories": categories,
            "growing_categories": growing,
            "category_shift": movements["shift"],
            "selected_category": selected,
            "onset": onset,
            "hypotheses": hypotheses,
            "methodology": {
                "ranking": "Последние 14 наблюдаемых календарных дней против предыдущих 14; категории ранжируются по потере GMV.",
                "onset": "Первая устойчивая серия из 3 дней, где 3-дневная медиана минимум на 15% ниже медианы предыдущих 7 дней.",
                "comparison": "Равные наблюдаемые окна до и после точки падения; пропуски источника не подменяются нулями.",
                "causal_limit": "Совпадение направления подтверждает драйвер, но не доказывает единственную корневую причину без разделяющего теста.",
            },
        }
    except Exception:
        return {
            "version": VERSION, "status": "evidence_gap", "categories": [], "selected_category": requested_category or None,
            "message": "Не удалось прочитать категорийную детализацию. Исходное дерево Health Check остаётся доступно.",
        }
    finally:
        app.CURRENT_CLIENT.reset(token)
