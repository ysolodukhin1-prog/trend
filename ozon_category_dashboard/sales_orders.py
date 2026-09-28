"""Read-only daily order evidence for WB, Ozon and Yandex Market.

Order IDs, timestamp, geography and buyer-price fields are never inferred from
Ozon's product/day funnel aggregate. Missing source days remain unknown.
"""

from datetime import date, timedelta
from urllib.parse import parse_qs

from order_feed import json_ready, numeric

MARKETS = {"wb", "ozon", "yandex_market"}


def options(parsed):
    p = parse_qs(parsed.query)
    market = (p.get("marketplace") or ["wb"])[0]
    if market not in MARKETS:
        raise ValueError("Выберите WB, Ozon или Яндекс")
    try:
        end = date.fromisoformat((p.get("date_to") or [date.today().isoformat()])[0])
        start = date.fromisoformat((p.get("date_from") or [(end - timedelta(days=29)).isoformat()])[0])
    except ValueError as exc:
        raise ValueError("Некорректный период") from exc
    if start > end or (end - start).days > 365:
        raise ValueError("Период должен составлять от 1 до 366 дней")
    status = (p.get("order_status") or ["all"])[0]
    if status not in ("all", "active", "cancelled") or (market == "ozon" and status != "all"):
        raise ValueError("Статус заказа недоступен для этой площадки")
    sku = (p.get("inventory_skus") or [""])[0].strip()
    if len(sku) > 100:
        raise ValueError("Слишком длинный SKU")
    detail = (p.get("detail_date") or [""])[0]
    if detail:
        try:
            selected = date.fromisoformat(detail)
        except ValueError as exc:
            raise ValueError("Некорректная дата детализации") from exc
        if selected < start or selected > end:
            raise ValueError("Дата детализации вне периода")
    region = (p.get("sales_region") or [""])[0].strip()
    if len(region) > 150:
        raise ValueError("Слишком длинное название региона")
    heat_day = (p.get("heat_weekday") or [""])[0]
    heat_hour = (p.get("heat_hour") or [""])[0]
    if (heat_day or heat_hour) and (not heat_day.isdigit() or not heat_hour.isdigit() or
                                   int(heat_day) not in range(7) or int(heat_hour) not in range(24)):
        raise ValueError("Некорректный час спроса")
    return market, start, end, status, sku, detail, region, heat_day, heat_hour


def source_sql(market, start, end, status, sku, client):
    """Return fixed SQL and bound values for a consistent item-level grain."""
    if market == "wb":
        clauses = ["source_key='statistics.orders'", "record_date BETWEEN %s AND %s"]
        args = [start, end]
        if sku:
            clauses.append("payload->>'nmId'=%s"); args.append(sku)
        if status != "all":
            clauses.append("lower(coalesce(payload->>'isCancel','false')) " + ("=" if status == "cancelled" else "<>") + " 'true'")
        return f"""SELECT record_date AS day, nullif(payload->>'srid','') AS order_id,
            entity_key AS item_id, payload->>'nmId' AS sku, payload->>'subject' AS product,
            1::numeric AS units,
            CASE WHEN {numeric('finishedPrice')}>0 THEN {numeric('finishedPrice')} END AS amount,
            CASE WHEN {numeric('priceWithDisc')}>0 THEN {numeric('priceWithDisc')} END AS seller_price,
            {numeric('spp')} AS spp,
            lower(coalesce(payload->>'isCancel','false'))='true' AS cancelled,
            nullif(payload->>'regionName','') AS region, payload->>'date' AS ordered_at
            FROM public.wb_api_entities WHERE {' AND '.join(clauses)}""", args
    if market == "yandex_market":
        clauses = ["i.client_key=%s", "i.order_date BETWEEN %s AND %s", "NOT i.is_test", "i.currency='RUR'"]
        args = [client, start, end]
        if sku:
            clauses.append("i.offer_id=%s"); args.append(sku)
        if status != "all":
            clauses.append("o.status " + ("=" if status == "cancelled" else "<>") + " 'CANCELLED'")
        # Both raw entities use the canonical order ID and campaign from the fact.
        return f"""SELECT i.order_date AS day, concat_ws(':',i.business_id,i.campaign_id,i.order_id) AS order_id,
            concat_ws(':',i.business_id,i.campaign_id,i.order_id,i.item_id) AS item_id,
            i.offer_id AS sku, i.offer_name AS product, i.units,
            i.buyer_payment AS amount, NULL::numeric AS seller_price, NULL::numeric AS spp,
            o.status='CANCELLED' AS cancelled,
            nullif(s.payload#>>'{{deliveryRegion,name}}','') AS region,
            r.payload->>'creationDate' AS ordered_at
            FROM public.yandex_fact_order_items i
            JOIN public.yandex_fact_orders o ON o.client_key=i.client_key AND o.business_id=i.business_id
              AND o.campaign_id=i.campaign_id AND o.order_id=i.order_id
            LEFT JOIN public.yandex_market_entities r ON r.client_key=i.client_key AND r.business_id=i.business_id
              AND r.campaign_id=i.campaign_id AND r.entity_id=i.order_id AND r.source_key='yandex_orders'
            LEFT JOIN public.yandex_market_entities s ON s.client_key=i.client_key AND s.business_id=i.business_id
              AND s.campaign_id=i.campaign_id AND s.entity_id=i.order_id AND s.source_key='yandex_order_stats'
            WHERE {' AND '.join(clauses)}""", args
    clauses = ["report_date BETWEEN %s AND %s"]
    args = [start, end]
    if sku:
        clauses.append("sku=%s"); args.append(sku)
    return f"""SELECT report_date AS day, NULL::text AS order_id, row_key AS item_id,
        sku, product_name AS product, ordered_units AS units, ordered_amount_rub AS amount,
        NULL::numeric AS seller_price, NULL::numeric AS spp, NULL::boolean AS cancelled,
        NULL::text AS region, NULL::text AS ordered_at
        FROM public.ozon_funnel_daily WHERE {' AND '.join(clauses)}""", args


def expand_days(start, end, observed):
    return [observed.get((start + timedelta(days=i)).isoformat(), {
        "date": (start + timedelta(days=i)).isoformat(), "orders": None, "units": None,
        "amount": None, "priced": None, "buyer_mean": None, "seller_mean": None,
        "spp_mean": None, "cancelled": None, "source_rows": 0,
    }) for i in range((end - start).days + 1)]


def load(parsed, get_conn, client):
    market, start, end, status, sku, detail, region, heat_day, heat_hour = options(parsed)
    source, values = source_sql(market, start, end, status, sku, client)
    with get_conn() as conn, conn.cursor() as cur:
        relation = {"wb":"wb_api_entities", "ozon":"ozon_funnel_daily", "yandex_market":"yandex_fact_order_items"}[market]
        cur.execute("SELECT to_regclass(%s) AS relation", ("public." + relation,))
        if not cur.fetchone()["relation"]:
            return {"marketplace":market,"status":"unavailable","days":[],"geography":None,"heatmap":None,"details":[]}
        source_bounds = {"wb":("wb_api_entities","record_date","source_key='statistics.orders'",[]),
                         "ozon":("ozon_funnel_daily","report_date","true",[]),
                         "yandex_market":("yandex_fact_orders","order_date","client_key=%s AND NOT is_test",[client])}[market]
        table, dt, where, bound = source_bounds
        cur.execute(f"SELECT min({dt}) AS first_date,max({dt}) AS last_date FROM public.{table} WHERE {where}", bound)
        bounds = dict(cur.fetchone())
        cur.execute("WITH events AS (" + source + """), daily AS (
          SELECT day,count(*) AS source_rows,
            CASE WHEN count(*) FILTER(WHERE order_id IS NULL)=0 THEN count(DISTINCT order_id) END AS orders,
            CASE WHEN count(*) FILTER(WHERE units IS NULL)=0 THEN sum(units) END AS units,
            CASE WHEN count(*) FILTER(WHERE amount IS NULL)=0 THEN sum(amount) END AS amount,
            count(amount) AS priced,
            CASE WHEN sum(units) FILTER(WHERE amount IS NOT NULL)>0
              THEN sum(amount)/sum(units) FILTER(WHERE amount IS NOT NULL) END AS buyer_mean,
            avg(seller_price) AS seller_mean, avg(spp) AS spp_mean,
            CASE WHEN count(*) FILTER(WHERE cancelled IS NULL)=0
              THEN count(DISTINCT order_id) FILTER(WHERE cancelled) END AS cancelled
          FROM events GROUP BY day)
          SELECT * FROM daily ORDER BY day""", values)
        observed = {}
        for raw in cur.fetchall():
            r = dict(raw); r["date"] = r.pop("day").isoformat()
            if market == "ozon":
                r["priced"] = None; r["buyer_mean"] = None
                r["avg_ordered_unit"] = r["amount"] / r["units"] if r["amount"] is not None and r["units"] and r["units"] > 0 else None
            observed[r["date"]] = r
        days = expand_days(start, end, observed)
        geography = None; heatmap = None
        if market != "ozon":
            cur.execute("WITH events AS (" + source + """), regions AS (
                SELECT coalesce(nullif(trim(region),''),'Не указан') AS region,
                  count(DISTINCT order_id) AS orders,
                  CASE WHEN count(*) FILTER(WHERE units IS NULL)=0 THEN sum(units) END AS units,
                  CASE WHEN count(*) FILTER(WHERE amount IS NULL)=0 THEN sum(amount) END AS amount
                FROM events GROUP BY 1)
                SELECT * FROM regions ORDER BY orders DESC,region""", values)
            regions = [dict(r) for r in cur.fetchall()]
            geography = {"regions":regions,"known_orders":sum(r["orders"] for r in regions if r["region"] != "Не указан"),
                         "unknown_orders":sum(r["orders"] for r in regions if r["region"] == "Не указан")}
            if region:
                cur.execute("WITH events AS (" + source + """ ) SELECT sku,max(product) AS product,
                    count(DISTINCT order_id) AS orders,sum(units) AS units,
                    CASE WHEN count(*) FILTER(WHERE amount IS NULL)=0 THEN sum(amount) END AS amount
                    FROM events WHERE coalesce(nullif(trim(region),''),'Не указан')=%s
                    GROUP BY sku ORDER BY orders DESC,sku LIMIT 30""", values + [region])
                geography["selected_region"] = region
                geography["top_skus"] = [dict(r) for r in cur.fetchall()]
            cur.execute("WITH events AS (" + source + """), timed AS (
                SELECT day,order_id,units,substring(ordered_at from 12 for 2)::int AS hour
                FROM events WHERE ordered_at ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-2][0-9]:' AND order_id IS NOT NULL)
                SELECT extract(isodow from day)::int-1 AS weekday,hour,
                  count(DISTINCT order_id) AS orders,sum(units) AS units
                FROM timed WHERE hour BETWEEN 0 AND 23 GROUP BY 1,2 ORDER BY 1,2""", values)
            cells = [dict(r) for r in cur.fetchall()]
            # Count unique orders with a timestamp, including multi-line orders once.
            cur.execute("WITH events AS (" + source + """ ) SELECT count(DISTINCT order_id) FILTER
                (WHERE ordered_at ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-2][0-9]:') AS known,
                count(DISTINCT order_id) AS total FROM events""", values)
            times = cur.fetchone()
            heatmap = {"cells":cells,"known_orders":times["known"],"unknown_orders":times["total"]-times["known"],
                       "timezone":"МСК (+03:00)" if market == "yandex_market" else "время WB без зоны в источнике"}
        details = []
        if (detail or heat_day) and market != "ozon":
            detail_where = "day=%s" if detail else "ordered_at ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-2][0-9]:' AND extract(isodow from day)::int-1=%s AND substring(ordered_at from 12 for 2)::int=%s"
            detail_args = [detail] if detail else [int(heat_day),int(heat_hour)]
            cur.execute("WITH events AS (" + source + """ ) SELECT order_id,item_id,day,ordered_at,sku,product,
                units,amount,seller_price,spp,cancelled,region FROM events WHERE """ + detail_where + """
                ORDER BY ordered_at DESC NULLS LAST,item_id LIMIT 100""", values + detail_args)
            details = [dict(r) for r in cur.fetchall()]
    return json_ready({"marketplace":market,"status":"partial" if bounds["first_date"] else "unavailable",
        "date_from":start,"date_to":end,"order_status":status,"sku":sku,"days":days,
        "coverage":{"first_date":bounds["first_date"],"last_date":bounds["last_date"],
                    "observed_days":len(observed),"expected_days":len(days),
                    "order_identity_available":market!="ozon","buyer_price_available":market!="ozon"},
        "geography":geography,"heatmap":heatmap,"details":details,"detail_date":detail,
        "heat_selection":{"weekday":int(heat_day),"hour":int(heat_hour)} if heat_day else None,
        "source":{"wb":"WB statistics.orders","ozon":"Ozon дневная товарная воронка",
                  "yandex_market":"Яндекс заказы и товарные позиции"}[market]})
