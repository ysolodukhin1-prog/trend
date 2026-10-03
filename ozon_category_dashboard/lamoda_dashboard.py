"""Read-only Lamoda dashboards backed exclusively by materialized views."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import psycopg2
from psycopg2.extras import RealDictCursor


REPORTS = {
    "lamodaSales": ("Продажи Lamoda", "Заказы и оборот по дате создания заказа."),
    "lamodaReturns": ("Возвраты Lamoda", "Возвратные позиции FBS по дате возврата."),
    "lamodaCatalog": ("Ассортимент Lamoda", "Последнее загруженное состояние номенклатуры, цен и модерации."),
    "lamodaOperations": ("Операции Lamoda", "Текущие акции и поставки FBO."),
}


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _period(params):
    today = date.today()
    try:
        date_to = date.fromisoformat((params.get("date_to") or [today.isoformat()])[0])
        date_from = date.fromisoformat((params.get("date_from") or [(date_to - timedelta(days=30)).isoformat()])[0])
    except ValueError as exc:
        raise ValueError("Некорректный период") from exc
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    if (date_to - date_from).days > 732:
        raise ValueError("Период отчёта не может превышать 733 дня")
    return date_from, date_to


def _page(params):
    try:
        page = max(1, int((params.get("page") or ["1"])[0]))
        size = min(200, max(10, int((params.get("page_size") or ["50"])[0])))
    except ValueError as exc:
        raise ValueError("Некорректная страница") from exc
    return page, size


def _fetchall(cur, query, values=()):
    cur.execute(query, values)
    return [dict(row) for row in cur.fetchall()]


def _freshness(cur):
    cur.execute("SELECT max(synced_at) AS synced_at FROM public.lamoda_v2_entities WHERE account_id IS NOT NULL")
    row = cur.fetchone() or {}
    return _plain(row.get("synced_at"))


def _sales(cur, date_from, date_to, page, size):
    values = (date_from, date_to)
    cur.execute("""
        SELECT count(*)::bigint AS orders_count,
               (COALESCE(sum(total_amount), 0) / 100)::numeric AS orders_amount,
               count(*) FILTER (WHERE is_confirmed)::bigint AS confirmed_count,
               count(DISTINCT status)::bigint AS statuses_count
        FROM public.mv_lamoda_orders_flat
        WHERE order_date BETWEEN %s AND %s
    """, values)
    summary = dict(cur.fetchone())
    trend = _fetchall(cur, """
        SELECT order_date AS date, sum(orders_count)::bigint AS count,
               (sum(orders_amount) / 100)::numeric AS amount
        FROM public.mv_lamoda_orders_daily
        WHERE order_date BETWEEN %s AND %s
        GROUP BY order_date ORDER BY order_date
    """, values)
    fulfillment_trend = _fetchall(cur, """
        SELECT order_date AS date, fulfillment, count(*)::bigint AS count,
               (COALESCE(sum(total_amount), 0) / 100)::numeric AS amount
        FROM public.mv_lamoda_orders_flat
        WHERE order_date BETWEEN %s AND %s AND fulfillment IN ('FBO', 'FBS')
        GROUP BY order_date, fulfillment ORDER BY order_date, fulfillment
    """, values)
    breakdown = _fetchall(cur, """
        SELECT COALESCE(NULLIF(status, ''), 'Без статуса') AS label,
               count(*)::bigint AS value
        FROM public.mv_lamoda_orders_flat
        WHERE order_date BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20
    """, values)
    cur.execute("""
        SELECT count(*) AS total FROM public.mv_lamoda_orders_flat
        WHERE order_date BETWEEN %s AND %s
    """, values)
    total = cur.fetchone()["total"]
    rows = _fetchall(cur, """
        SELECT order_date, order_id, status, is_confirmed, payment_method,
               shipping_method_name, total_amount / 100 AS total_amount, currency, updated_at
        FROM public.mv_lamoda_orders_flat
        WHERE order_date BETWEEN %s AND %s
        ORDER BY order_date DESC, updated_at DESC NULLS LAST, order_id
        LIMIT %s OFFSET %s
    """, (*values, size, (page - 1) * size))
    return {
        "kpis": [
            {"label": "Заказы", "value": summary["orders_count"], "format": "integer"},
            {"label": "Сумма заказов", "value": summary["orders_amount"], "format": "money"},
            {"label": "Подтверждены", "value": summary["confirmed_count"], "format": "integer"},
            {"label": "Статусов", "value": summary["statuses_count"], "format": "integer"},
        ],
        "trend": trend,
        "fulfillment_trend": fulfillment_trend,
        "breakdowns": [{"title": "Статусы заказов", "rows": breakdown}],
        "columns": [
            {"key": "order_date", "label": "Дата"}, {"key": "order_id", "label": "Заказ"},
            {"key": "status", "label": "Статус"}, {"key": "is_confirmed", "label": "Подтверждён"},
            {"key": "payment_method", "label": "Оплата"}, {"key": "shipping_method_name", "label": "Доставка"},
            {"key": "total_amount", "label": "Сумма", "format": "money"}, {"key": "currency", "label": "Валюта"},
        ],
        "rows": rows, "total": total,
        "limitations": ["Источник отдаёт заказ целиком без товарных позиций; SKU-разрез в этом отчёте отсутствует."],
    }


def _returns(cur, date_from, date_to, page, size):
    values = (date_from, date_to)
    cur.execute("""
        SELECT count(*)::bigint AS returns_count,
               count(DISTINCT order_id)::bigint AS orders_count,
               (COALESCE(sum(amount), 0) / 100)::numeric AS returns_amount,
               count(DISTINCT seller_sku) FILTER (WHERE seller_sku <> '')::bigint AS sku_count
        FROM public.mv_lamoda_returns_flat
        WHERE return_date BETWEEN %s AND %s
    """, values)
    summary = dict(cur.fetchone())
    trend = _fetchall(cur, """
        SELECT return_date AS date, sum(returns_count)::bigint AS count,
               (sum(returns_amount) / 100)::numeric AS amount
        FROM public.mv_lamoda_returns_daily
        WHERE return_date BETWEEN %s AND %s
        GROUP BY return_date ORDER BY return_date
    """, values)
    by_type = _fetchall(cur, """
        SELECT COALESCE(NULLIF(return_type, ''), 'Не указан') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_returns_flat WHERE return_date BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC, 1
    """, values)
    by_status = _fetchall(cur, """
        SELECT COALESCE(NULLIF(status, ''), 'Без статуса') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_returns_flat WHERE return_date BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 2 DESC, 1
    """, values)
    cur.execute("SELECT count(*) AS total FROM public.mv_lamoda_returns_flat WHERE return_date BETWEEN %s AND %s", values)
    total = cur.fetchone()["total"]
    rows = _fetchall(cur, """
        SELECT return_date, order_id, seller_sku, lamoda_sku, item_name, size,
               return_type, status, amount / 100 AS amount, currency
        FROM public.mv_lamoda_returns_flat
        WHERE return_date BETWEEN %s AND %s
        ORDER BY return_date DESC, order_id, item_id LIMIT %s OFFSET %s
    """, (*values, size, (page - 1) * size))
    return {
        "kpis": [
            {"label": "Возвратные позиции", "value": summary["returns_count"], "format": "integer"},
            {"label": "Заказы с возвратом", "value": summary["orders_count"], "format": "integer"},
            {"label": "Стоимость позиций", "value": summary["returns_amount"], "format": "money"},
            {"label": "SKU", "value": summary["sku_count"], "format": "integer"},
        ],
        "trend": trend,
        "breakdowns": [{"title": "Типы возвратов", "rows": by_type}, {"title": "Статусы", "rows": by_status}],
        "columns": [
            {"key": "return_date", "label": "Дата"}, {"key": "order_id", "label": "Заказ"},
            {"key": "seller_sku", "label": "Артикул продавца"}, {"key": "lamoda_sku", "label": "SKU Lamoda"},
            {"key": "item_name", "label": "Товар"}, {"key": "size", "label": "Размер"},
            {"key": "return_type", "label": "Тип"}, {"key": "status", "label": "Статус"},
            {"key": "amount", "label": "Стоимость", "format": "money"},
        ],
        "rows": rows, "total": total, "limitations": [],
    }


def _catalog(cur, _date_from, _date_to, page, size):
    cur.execute("""
        SELECT count(*)::bigint AS products_count,
               count(*) FILTER (WHERE status = 'ONLINE')::bigint AS online_count,
               count(*) FILTER (WHERE status = 'OUT_OF_STOCK')::bigint AS out_of_stock_count,
               count(*) FILTER (WHERE sale_price IS NOT NULL)::bigint AS discounted_count
        FROM public.mv_lamoda_catalog_flat
    """)
    summary = dict(cur.fetchone())
    statuses = _fetchall(cur, """
        SELECT COALESCE(NULLIF(status, ''), 'Без статуса') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_catalog_flat GROUP BY 1 ORDER BY 2 DESC, 1
    """)
    moderation = _fetchall(cur, """
        SELECT COALESCE(NULLIF(moderation_status, ''), 'Не указан') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_catalog_flat GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20
    """)
    cur.execute("SELECT count(*) AS total FROM public.mv_lamoda_catalog_flat")
    total = cur.fetchone()["total"]
    rows = _fetchall(cur, """
        SELECT seller_sku, lamoda_sku, product_name, brand, category_path, status,
               quantity, price / 100 AS price, sale_price / 100 AS sale_price,
               currency, price_status, moderation_status, snapshot_date
        FROM public.mv_lamoda_catalog_flat
        ORDER BY product_name, seller_sku LIMIT %s OFFSET %s
    """, (size, (page - 1) * size))
    return {
        "kpis": [
            {"label": "Товары", "value": summary["products_count"], "format": "integer"},
            {"label": "Онлайн", "value": summary["online_count"], "format": "integer"},
            {"label": "Нет в наличии", "value": summary["out_of_stock_count"], "format": "integer"},
            {"label": "Со скидкой", "value": summary["discounted_count"], "format": "integer"},
        ],
        "trend": [],
        "breakdowns": [{"title": "Статусы товаров", "rows": statuses}, {"title": "Модерация", "rows": moderation}],
        "columns": [
            {"key": "seller_sku", "label": "Артикул продавца"}, {"key": "lamoda_sku", "label": "SKU Lamoda"},
            {"key": "product_name", "label": "Товар"}, {"key": "brand", "label": "Бренд"},
            {"key": "category_path", "label": "Категория"}, {"key": "status", "label": "Статус"},
            {"key": "quantity", "label": "Количество"}, {"key": "price", "label": "Цена", "format": "money"},
            {"key": "sale_price", "label": "Цена со скидкой", "format": "money"},
            {"key": "moderation_status", "label": "Модерация"},
        ],
        "rows": rows, "total": total,
        "limitations": ["Ассортимент обновляется только вручную из админки; отчёт показывает последний загруженный снимок."],
    }


def _operations(cur, date_from, date_to, page, size):
    cur.execute("""
        SELECT count(*)::bigint AS promotions_count,
               count(*) FILTER (WHERE status ILIKE '%%active%%')::bigint AS active_promotions
        FROM public.mv_lamoda_promotions_flat
    """)
    promo = dict(cur.fetchone())
    cur.execute("""
        SELECT count(*)::bigint AS shipments_count,
               COALESCE(sum(planned_items), 0)::bigint AS planned_items,
               COALESCE(sum(accepted_items), 0)::bigint AS accepted_items
        FROM public.mv_lamoda_fbo_shipments_flat
        WHERE planned_at::date BETWEEN %s AND %s OR planned_at IS NULL
    """, (date_from, date_to))
    shipment = dict(cur.fetchone())
    promo_status = _fetchall(cur, """
        SELECT COALESCE(NULLIF(status, ''), 'Без статуса') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_promotions_flat GROUP BY 1 ORDER BY 2 DESC, 1
    """)
    shipment_status = _fetchall(cur, """
        SELECT COALESCE(NULLIF(status, ''), 'Без статуса') AS label, count(*)::bigint AS value
        FROM public.mv_lamoda_fbo_shipments_flat
        WHERE planned_at::date BETWEEN %s AND %s OR planned_at IS NULL
        GROUP BY 1 ORDER BY 2 DESC, 1
    """, (date_from, date_to))
    rows = _fetchall(cur, """
        SELECT planned_at::date AS planned_date, external_shipment_id, status,
               planned_items, accepted_items, received_items, damaged_items, missing_items
        FROM public.mv_lamoda_fbo_shipments_flat
        WHERE planned_at::date BETWEEN %s AND %s OR planned_at IS NULL
        ORDER BY planned_at DESC NULLS LAST LIMIT %s OFFSET %s
    """, (date_from, date_to, size, (page - 1) * size))
    return {
        "kpis": [
            {"label": "Акции", "value": promo["promotions_count"], "format": "integer"},
            {"label": "Активные акции", "value": promo["active_promotions"], "format": "integer"},
            {"label": "Поставки FBO", "value": shipment["shipments_count"], "format": "integer"},
            {"label": "Принято товаров", "value": shipment["accepted_items"], "format": "integer"},
        ],
        "trend": [],
        "breakdowns": [{"title": "Статусы акций", "rows": promo_status}, {"title": "Статусы поставок", "rows": shipment_status}],
        "columns": [
            {"key": "planned_date", "label": "Дата поставки"}, {"key": "external_shipment_id", "label": "Поставка"},
            {"key": "status", "label": "Статус"}, {"key": "planned_items", "label": "Запланировано"},
            {"key": "accepted_items", "label": "Принято"}, {"key": "received_items", "label": "Получено"},
            {"key": "damaged_items", "label": "Повреждено"}, {"key": "missing_items", "label": "Недостача"},
        ],
        "rows": rows, "total": shipment["shipments_count"],
        "limitations": ["Финансовые документы и расходы Lamoda не входят в Seller API v2 и в отчётах не рассчитываются косвенно."],
    }


LOADERS = {
    "lamodaSales": _sales,
    "lamodaReturns": _returns,
    "lamodaCatalog": _catalog,
    "lamodaOperations": _operations,
}


def dashboard_payload(parsed, db_config):
    params = parse_qs(parsed.query)
    report = (params.get("dashboard") or params.get("report") or ["lamodaSales"])[0]
    if report not in REPORTS:
        raise ValueError("Неизвестный отчёт Lamoda")
    date_from, date_to = _period(params)
    page, size = _page(params)
    with psycopg2.connect(**db_config) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            data = LOADERS[report](cur, date_from, date_to, page, size)
            freshness = _freshness(cur)
    title, subtitle = REPORTS[report]
    return _plain({
        "ok": True,
        "available": True,
        "report": report,
        "title": title,
        "subtitle": subtitle,
        "date_from": date_from,
        "date_to": date_to,
        "freshness": freshness,
        "page": page,
        "page_size": size,
        **data,
    })
