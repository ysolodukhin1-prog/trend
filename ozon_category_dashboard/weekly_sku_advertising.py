"""Read-only WB SKU advertising evidence for the weekly diagnostic."""

from datetime import date, timedelta
from urllib.parse import parse_qs


FIELDS = ("impressions", "clicks", "expense_rub", "added_to_cart", "orders_qty", "orders_amount_rub")


def options(parsed):
    params = parse_qs(parsed.query)
    if (params.get("marketplace") or [""])[0].lower() != "wb":
        raise ValueError("Динамика рекламы SKU доступна для WB")
    sku = (params.get("inventory_skus") or [""])[0].strip()
    if not sku or len(sku) > 100 or len(params.get("inventory_skus", [])) != 1:
        raise ValueError("Укажите один SKU WB")
    try:
        start = date.fromisoformat((params.get("date_from") or [""])[0])
        end = date.fromisoformat((params.get("date_to") or [""])[0])
    except ValueError as exc:
        raise ValueError("Укажите корректный период") from exc
    if start > end or (end - start).days > 365:
        raise ValueError("Выберите период не длиннее года")
    return sku, start, end


def matrix(sku, start, end, rows, account_days, last_sku_date=None):
    by_date = {str(row["report_date"])[:10]: row for row in rows}
    result = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        source = by_date.get(day)
        metrics = {field: float(source[field]) if source and source.get(field) is not None else None for field in FIELDS}
        impressions, clicks, expense = metrics["impressions"], metrics["clicks"], metrics["expense_rub"]
        sales = metrics["orders_amount_rub"]
        result.append({"date": day, "observed": source is not None, "source": str(source.get("source_sheet") or "") if source else "",
                       **metrics,
                       "ctr_pct": clicks / impressions * 100 if clicks is not None and impressions and impressions > 0 else None,
                       "cpc_rub": expense / clicks if expense is not None and clicks and clicks > 0 else None,
                       "drr_pct": expense / sales * 100 if expense is not None and sales and sales > 0 else None})
    return {"sku": sku, "date_from": start.isoformat(), "date_to": end.isoformat(), "days": result,
            "observed_days": sum(row["observed"] for row in result), "expected_days": len(result),
            "account_source_days": len(account_days), "last_sku_date": str(last_sku_date) if last_sku_date else None,
            "source_status": "partial"}


def load(parsed, get_conn):
    sku, start, end = options(parsed)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT report_date,
              CASE WHEN count(*) FILTER (WHERE impressions IS NULL)=0 THEN sum(impressions) END impressions,
              CASE WHEN count(*) FILTER (WHERE clicks IS NULL)=0 THEN sum(clicks) END clicks,
              CASE WHEN count(*) FILTER (WHERE expense_rub IS NULL)=0 THEN sum(expense_rub) END expense_rub,
              CASE WHEN count(*) FILTER (WHERE added_to_cart IS NULL)=0 THEN sum(added_to_cart) END added_to_cart,
              CASE WHEN count(*) FILTER (WHERE orders_qty IS NULL)=0 THEN sum(orders_qty) END orders_qty,
              CASE WHEN count(*) FILTER (WHERE orders_amount_rub IS NULL)=0 THEN sum(orders_amount_rub) END orders_amount_rub,
              string_agg(DISTINCT source_sheet, ', ') source_sheet
            FROM public.mv_wb_adv_daily_by_article_category
            WHERE sku = %s AND report_date BETWEEN %s AND %s
            GROUP BY report_date ORDER BY report_date
        """, (sku, start, end))
        rows = cur.fetchall()
        cur.execute("""
            SELECT DISTINCT report_date FROM public.mv_wb_adv_daily_by_article_category
            WHERE report_date BETWEEN %s AND %s
        """, (start, end))
        account_days = {str(row["report_date"])[:10] for row in cur.fetchall()}
        cur.execute("SELECT max(report_date) last_date FROM public.mv_wb_adv_daily_by_article_category WHERE sku = %s AND report_date <= %s", (sku, end))
        last_sku_date = cur.fetchone()["last_date"]
    return matrix(sku, start, end, rows, account_days, last_sku_date)
