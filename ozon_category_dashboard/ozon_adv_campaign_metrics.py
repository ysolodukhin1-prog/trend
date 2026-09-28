"""Campaign-level Ozon advertising metrics for dashboard overlays."""

from __future__ import annotations

from urllib.parse import parse_qs


PRODUCT_SCOPE_PARAMS = {
    "categories", "inventory_skus", "article", "product",
    "seo_status", "collection_status",
    "gj_model", "assortment_bia", "tg", "tg_plus", "cg", "season",
    "ozon_collection", "ozon_gender", "ozon_season", "ozon_style",
    "ozon_color", "ozon_material", "ozon_material_composition",
    "ozon_russian_size", "ozon_manufacturer_size", "ozon_target_audience",
}


def _values(params, key):
    result = []
    for raw in params.get(key, []):
        result.extend(value.strip() for value in str(raw).split(","))
    return [value for value in dict.fromkeys(result) if value]


def has_product_scope(query):
    params = parse_qs(query)
    return any(_values(params, key) for key in PRODUCT_SCOPE_PARAMS)


def fetch_campaign_metrics(connection_factory, query, group_by='summary'):
    """Return native campaign metrics, or None when SKU filters make them invalid."""
    if has_product_scope(query):
        return None

    params = parse_qs(query)
    marketplace = (params.get("marketplace") or ["ozon"])[0].strip().lower()
    if marketplace != "ozon":
        return None

    filters = []
    values = []
    date_from = (params.get("date_from") or [""])[0].strip()
    date_to = (params.get("date_to") or [""])[0].strip()
    if date_from:
        filters.append("c.report_date >= %s")
        values.append(date_from)
    if date_to:
        filters.append("c.report_date <= %s")
        values.append(date_to)
    campaign_ids = _values(params, "adv_campaign_id")
    if campaign_ids:
        filters.append("c.campaign_id = ANY(%s)")
        values.append(campaign_ids)
    where = "WHERE " + " AND ".join(filters) if filters else ""

    if group_by == "date":
        select = """
            c.report_date,
            count(DISTINCT c.campaign_id) AS campaign_count,
            coalesce(sum(c.impressions), 0) AS impressions,
            coalesce(sum(c.clicks), 0) AS clicks,
            coalesce(sum(c.expense_rub), 0) AS expense_rub,
            coalesce(sum(c.orders_qty), 0) AS orders_qty,
            coalesce(sum(c.orders_amount_rub), 0) AS orders_amount_rub
        """
        suffix = "GROUP BY c.report_date ORDER BY c.report_date"
    elif group_by == "campaign":
        select = """
            c.campaign_id,
            max(c.campaign_title) AS campaign_title,
            min(c.report_date) AS date_from,
            max(c.report_date) AS date_to,
            count(DISTINCT c.report_date) AS campaign_days,
            coalesce(sum(c.impressions), 0) AS impressions,
            coalesce(sum(c.clicks), 0) AS clicks,
            coalesce(sum(c.expense_rub), 0) AS campaign_expense_rub,
            coalesce(sum(c.orders_qty), 0) AS campaign_orders_qty,
            coalesce(sum(c.orders_amount_rub), 0) AS campaign_orders_amount_rub
        """
        suffix = "GROUP BY c.campaign_id ORDER BY campaign_expense_rub DESC, c.campaign_id"
    else:
        select = """
            count(DISTINCT c.campaign_id) AS campaign_count,
            min(c.report_date) AS date_from,
            max(c.report_date) AS date_to,
            count(DISTINCT c.report_date) AS campaign_days,
            coalesce(sum(c.impressions), 0) AS impressions,
            coalesce(sum(c.clicks), 0) AS clicks,
            coalesce(sum(c.expense_rub), 0) AS expense_rub,
            coalesce(sum(c.orders_qty), 0) AS orders_qty,
            coalesce(sum(c.orders_amount_rub), 0) AS orders_amount_rub
        """
        suffix = ""

    with connection_factory() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS relation_name", ("public.ozon_adv_campaign_daily_raw",))
        if not cur.fetchone()["relation_name"]:
            return None
        cur.execute(
            f"SELECT {select} FROM public.ozon_adv_campaign_daily_raw c {where} {suffix}",
            values,
        )
        rows = []
        for raw_row in cur.fetchall():
            row = dict(raw_row)
            for key in ("report_date", "date_from", "date_to"):
                value = row.get(key)
                if value is not None and hasattr(value, "isoformat"):
                    row[key] = value.isoformat()
            rows.append(row)
    if group_by == "summary":
        return rows[0] if rows else None
    return rows
