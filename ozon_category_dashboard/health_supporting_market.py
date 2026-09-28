"""Attach existing external category history; never substitute own sales."""
from datetime import date, timedelta

def attach_market_history(payload, config):
    from km_trade_finance import connect_km
    end = date.fromisoformat(str(payload['analysis_date'])[:10])
    start = end - timedelta(days=174)
    try:
        with connect_km(config) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.km_category_market_daily') AS name")
                if not cur.fetchone()['name']:
                    return {**payload, 'market_dynamics': {'status': 'missing', 'rows': []}}
                cur.execute('''SELECT category_name, report_date::text, revenue_rub, sales_qty
                    FROM public.km_category_market_daily
                    WHERE marketplace=%s AND report_date BETWEEN %s AND %s
                    ORDER BY category_name,report_date''', (payload['marketplace'], start, end))
                rows = [{**dict(r), 'revenue_rub': float(r['revenue_rub']) if r['revenue_rub'] is not None else None,
                         'sales_qty': float(r['sales_qty']) if r['sales_qty'] is not None else None} for r in cur.fetchall()]
        return {**payload, 'market_dynamics': {'status': 'available' if rows else 'missing', 'rows': rows}}
    except Exception:
        return {**payload, 'market_dynamics': {'status': 'unavailable', 'rows': []}}
