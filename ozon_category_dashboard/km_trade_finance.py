"""KM Trade-style Ozon Unit Economics and P&L payloads.

Connections are pinned to the database supplied by the dashboard client config.
The legacy KM Trade endpoints stay compatible while registered clients can use
the same finance schema in their own isolated databases.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
from typing import Any, Callable

import psycopg2
from openpyxl import load_workbook
from psycopg2.extras import RealDictCursor, execute_values


TARGET_DB = "km_trade_products"


def decimal_value(value: Any, *, nullable: bool = False) -> Decimal | None:
    if value in (None, ""):
        return None if nullable else Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value!r}") from exc


def number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def row_numbers(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, Decimal):
            result[key] = float(value)
        elif isinstance(value, (date,)):
            result[key] = value.isoformat()
    return result


from ozon_article_labels import article_label

def unit_row_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    missing_dimensions = bool(row.get("dimension_missing_reason"))
    try:
        revenue_key = -float(row.get("actual_revenue") or 0)
    except (TypeError, ValueError):
        revenue_key = 0.0
    return (1 if missing_dimensions else 0, revenue_key, str(row.get("sku") or ""))


def volume_l_from_dimensions(
    depth: Decimal | None,
    width: Decimal | None,
    height: Decimal | None,
    unit: str | None,
) -> Decimal | None:
    """Return package volume in litres from the effective dimensions."""
    if depth is None or width is None or height is None:
        return None
    if depth <= 0 or width <= 0 or height <= 0:
        return None
    normalized = str(unit or "mm").strip().lower()
    divisor = {
        "mm": Decimal("1000000"),
        "мм": Decimal("1000000"),
        "cm": Decimal("1000"),
        "см": Decimal("1000"),
        "m": Decimal("0.001"),
        "м": Decimal("0.001"),
    }.get(normalized)
    if divisor is None:
        return None
    return depth * width * height / divisor


def volume_scaled_forward_logistics(
    *,
    delivery_amount: Decimal,
    logistics_min: Decimal,
    logistics_max: Decimal,
    api_volume_weight: Decimal | None,
    model_volume_weight: Decimal | None,
    observed_per_unit: Decimal | None,
    first_mile_min: Decimal = Decimal("0"),
    first_mile_max: Decimal = Decimal("0"),
) -> tuple[Decimal, str]:
    """Scale the current Ozon tariff band without using factual expenses."""
    lower = max(logistics_min, Decimal("0"))
    upper = max(logistics_max, lower)
    first_mile = (
        max(first_mile_min, Decimal("0")) + max(first_mile_max, Decimal("0"))
    ) / 2
    # Kept in the signature for compatibility. Finance fact is reconciliation-only.
    _ = observed_per_unit
    if lower > 0:
        baseline = lower
        source = "Мин. тариф Ozon × объём"
    else:
        baseline = upper
        source = "Тариф Ozon × объём"
    ratio = Decimal("1")
    if (
        api_volume_weight is not None
        and api_volume_weight > 0
        and model_volume_weight is not None
        and model_volume_weight > 0
    ):
        ratio = model_volume_weight / api_volume_weight
    return delivery_amount + first_mile + baseline * ratio, source


def advertising_pct_per_buyout(
    advertising_pct: Decimal | None,
    non_buyout_pct: Decimal | None = Decimal("0"),
) -> Decimal | None:
    """Normalize order-based DRR to one bought-out unit."""
    if advertising_pct is None or non_buyout_pct is None:
        return None
    if non_buyout_pct < 0 or non_buyout_pct >= 100:
        return None
    buyout_rate = Decimal("1") - non_buyout_pct / Decimal("100")
    return advertising_pct / buyout_rate


def actual_advertising_allocation(
    finance_total_amount: Decimal,
    sku_finance_amount: Decimal,
    performance_spend: Decimal,
    performance_total: Decimal,
) -> tuple[Decimal, str]:
    """Return a positive expense, using Finance as total and Performance as SKU weights."""
    normalized_performance = max(performance_spend, Decimal("0"))
    if finance_total_amount and performance_total > 0:
        expense = -finance_total_amount * normalized_performance / performance_total
        return expense, "Finance Ozon → распределено по доле Performance API"
    if sku_finance_amount:
        return -sku_finance_amount, "Finance Ozon — точная привязка к SKU"
    if normalized_performance:
        return normalized_performance, "Performance API — Finance без рекламной статьи"
    return Decimal("0"), "Рекламных расходов по SKU не найдено"


def unit_scenario(
    *,
    price: Decimal | None,
    commission_pct: Decimal | None,
    acquiring_pct: Decimal | None,
    tax_pct: Decimal | None,
    advertising_pct: Decimal | None,
    forward_logistics: Decimal | None,
    reverse_logistics: Decimal | None,
    cogs: Decimal | None,
    seller_costs: Decimal | None,
    vat_pct: Decimal | None = Decimal("0"),
    capital_cost: Decimal | None = Decimal("0"),
    non_buyout_pct: Decimal | None = Decimal("0"),
) -> dict[str, float | None]:
    """Calculate one transparent per-unit pricing scenario."""
    effective_advertising_pct = advertising_pct_per_buyout(
        advertising_pct, non_buyout_pct
    )
    if price is None:
        return {
            "price": None,
            "commission": None,
            "acquiring": None,
            "tax": None,
            "vat": None,
            "advertising": None,
            "advertising_pct_effective": number(effective_advertising_pct),
            "forward_logistics": number(forward_logistics),
            "reverse_logistics": number(reverse_logistics),
            "capital": number(capital_cost),
            "cogs": number(cogs),
            "seller_costs": number(seller_costs),
            "total_costs": None,
            "profit": None,
            "margin_pct": None,
        }
    commission = price * commission_pct / 100 if commission_pct is not None else None
    acquiring = price * acquiring_pct / 100 if acquiring_pct is not None else None
    tax = price * tax_pct / 100 if tax_pct is not None else None
    vat = price * (vat_pct or Decimal("0")) / 100
    advertising = (
        price * effective_advertising_pct / 100
        if effective_advertising_pct is not None
        else None
    )
    total_costs = None
    profit = None
    if all(
        value is not None
        for value in (
            commission,
            acquiring,
            tax,
            advertising,
            forward_logistics,
            reverse_logistics,
            cogs,
            seller_costs,
            capital_cost,
        )
    ):
        total_costs = (
            commission
            + acquiring
            + tax
            + vat
            + advertising
            + forward_logistics
            + reverse_logistics
            + seller_costs
            + capital_cost
            + cogs
        )
        profit = price - total_costs
    return {
        "price": number(price),
        "commission": number(commission),
        "acquiring": number(acquiring),
        "tax": number(tax),
        "vat": number(vat),
        "advertising": number(advertising),
        "advertising_pct_effective": number(effective_advertising_pct),
        "forward_logistics": number(forward_logistics),
        "reverse_logistics": number(reverse_logistics),
        "capital": number(capital_cost),
        "cogs": number(cogs),
        "seller_costs": number(seller_costs),
        "total_costs": number(total_costs),
        "profit": number(profit),
        "margin_pct": number(profit / price * 100 if profit is not None and price else None),
    }


def price_corridor(
    *,
    commission_pct: Decimal | None,
    acquiring_pct: Decimal | None,
    tax_pct: Decimal | None,
    advertising_pct: Decimal | None,
    mrc_margin_pct: Decimal,
    rrc_margin_pct: Decimal,
    forward_logistics: Decimal | None,
    reverse_logistics: Decimal | None,
    cogs: Decimal | None,
    seller_costs: Decimal | None,
    vat_pct: Decimal | None = Decimal("0"),
    capital_cost: Decimal | None = Decimal("0"),
    non_buyout_pct: Decimal | None = Decimal("0"),
) -> tuple[Decimal | None, Decimal | None]:
    """Return prices for the configured MRC and RRC margin targets."""
    effective_advertising_pct = advertising_pct_per_buyout(
        advertising_pct, non_buyout_pct
    )
    if any(
        value is None
        for value in (
            commission_pct,
            acquiring_pct,
            tax_pct,
            effective_advertising_pct,
            forward_logistics,
            reverse_logistics,
            cogs,
            seller_costs,
            capital_cost,
        )
    ):
        return None, None
    variable_pct = (
        commission_pct
        + acquiring_pct
        + tax_pct
        + (vat_pct or Decimal("0"))
        + effective_advertising_pct
    )
    fixed_costs = (
        cogs
        + forward_logistics
        + reverse_logistics
        + seller_costs
        + capital_cost
    )

    def target_price(margin_pct: Decimal) -> Decimal | None:
        denominator = Decimal("1") - (variable_pct + margin_pct) / 100
        return fixed_costs / denominator if denominator > 0 else None

    return target_price(mrc_margin_pct), target_price(rrc_margin_pct)


UNIT_COST_KIND_META = {
    "commission": ("Комиссия Ozon", "direct", "Точно по SKU из финансовых начислений"),
    "acquiring": ("Эквайринг", "direct", "Точно по SKU; ставка зависит от способа оплаты"),
    "logistics": ("Прямая логистика и последняя миля", "direct", "Точно по SKU и отправлению"),
    "reverse_logistics": ("Возвраты и обратная логистика", "direct", "Точно по SKU и возврату"),
    "advertising": ("Реклама Ozon", "driver", "Финансовый факт — P&L; на SKU распределяется только по атрибуции Performance API"),
    "storage": (
        "Хранение Ozon — факт P&L",
        "mixed",
        "В плановую юнитку не входит; точный SKU — напрямую, общий счёт остаётся в P&L",
    ),
    "fulfillment_ozon": ("Кросс-докинг, приёмка и упаковка Ozon", "mixed", "Точный SKU — напрямую; поставка — по единицам или объёму поставки, иначе P&L"),
    "other_service": ("Прочие услуги Ozon", "mixed", "Точный SKU — напрямую; общий счёт без драйвера остаётся в P&L"),
}


def unit_cost_structure(cur, date_from: date, date_to: date) -> dict[str, Any]:
    """Return a transparent fact cost registry without hidden account-level allocation."""
    cur.execute(
        """
        SELECT line_kind, coalesce(sum(amount), 0) AS amount,
               coalesce(sum(amount) FILTER (WHERE nullif(sku, '') IS NOT NULL), 0) AS sku_amount,
               count(*) AS lines,
               count(*) FILTER (WHERE nullif(sku, '') IS NOT NULL) AS sku_lines,
               count(DISTINCT sku) FILTER (WHERE nullif(sku, '') IS NOT NULL) AS skus
        FROM public.ozon_finance_lines
        WHERE operation_date BETWEEN %s AND %s
        GROUP BY line_kind
        ORDER BY abs(sum(amount)) DESC
        """,
        (date_from, date_to),
    )
    raw_kinds = cur.fetchall()
    revenue = sum((decimal_value(row["amount"]) for row in raw_kinds if row["line_kind"] == "revenue"), Decimal("0"))
    expenses = sum((decimal_value(row["amount"]) for row in raw_kinds if decimal_value(row["amount"]) < 0), Decimal("0"))
    items = []
    for row in raw_kinds:
        kind = str(row["line_kind"])
        if kind == "revenue":
            continue
        amount = decimal_value(row["amount"])
        sku_amount = decimal_value(row["sku_amount"])
        label, allocation_mode, allocation_rule = UNIT_COST_KIND_META.get(
            kind, (kind, "review", "Статья не классифицирована; до проверки остаётся в P&L")
        )
        items.append({
            "key": kind,
            "label": label,
            "amount": number(amount),
            "share_of_revenue_pct": number(abs(amount) / revenue * 100 if revenue else None),
            "sku_amount_coverage_pct": number(abs(sku_amount) / abs(amount) * 100 if amount else None),
            "lines": int(row["lines"] or 0),
            "sku_lines": int(row["sku_lines"] or 0),
            "skus": int(row["skus"] or 0),
            "allocation_mode": allocation_mode,
            "allocation_rule": allocation_rule,
            "pl_amount": number(amount - sku_amount),
        })
    cur.execute(
        """
        SELECT line_kind, coalesce(nullif(type_name, ''), 'Без наименования') AS type_name,
               coalesce(sum(amount), 0) AS amount,
               coalesce(sum(amount) FILTER (WHERE nullif(sku, '') IS NOT NULL), 0) AS sku_amount,
               count(*) AS lines
        FROM public.ozon_finance_lines
        WHERE operation_date BETWEEN %s AND %s
        GROUP BY line_kind, coalesce(nullif(type_name, ''), 'Без наименования')
        ORDER BY abs(sum(amount)) DESC
        """,
        (date_from, date_to),
    )
    details = []
    for row in cur.fetchall():
        if row["line_kind"] == "revenue":
            continue
        amount = decimal_value(row["amount"])
        sku_amount = decimal_value(row["sku_amount"])
        details.append({
            "line_kind": row["line_kind"],
            "type_name": article_label(row["type_name"], row["line_kind"]),
            "source_type_name": row["type_name"],
            "amount": number(amount),
            "share_of_revenue_pct": number(abs(amount) / revenue * 100 if revenue else None),
            "sku_amount_coverage_pct": number(abs(sku_amount) / abs(amount) * 100 if amount else None),
            "lines": int(row["lines"] or 0),
        })
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "revenue": number(revenue),
        "expenses": number(expenses),
        "items": items,
        "details": details,
    }


def connect_km(config: dict[str, Any]):
    safe = dict(config)
    expected_database = str(safe.get("database") or TARGET_DB)
    safe["database"] = expected_database
    conn = psycopg2.connect(**safe, cursor_factory=RealDictCursor)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database() AS name")
        actual = cur.fetchone()["name"]
    if actual != expected_database:
        conn.close()
        raise RuntimeError(f"Защита клиента: ожидалась {expected_database}, открылась {actual}")
    return conn


def available_range(cur) -> tuple[date, date]:
    cur.execute(
        """
        SELECT min(operation_date) AS date_from, max(operation_date) AS date_to
        FROM public.ozon_finance_events
        """
    )
    row = cur.fetchone()
    today = date.today()
    return row["date_from"] or today, row["date_to"] or today


def unavailable_unit_payload(
    raw_from: str | None,
    raw_to: str | None,
    client_key: str,
    missing_relations: list[str],
) -> dict[str, Any]:
    """Return an honest read-only state when the client has no finance model."""
    today = date.today()
    date_from = date.fromisoformat(raw_from) if raw_from else today
    date_to = date.fromisoformat(raw_to) if raw_to else today
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    message = (
        "Юнит-экономика Ozon недоступна: для кабинета не загружена "
        "нормализованная финансовая схема. Пропуски не заменены нулями."
    )
    return {
        "ok": True,
        "available": False,
        "data_status": "unavailable",
        "reason_code": "ozon_finance_schema_missing",
        "client": client_key,
        "marketplace": "ozon",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "available_date_from": today.isoformat(),
        "available_date_to": today.isoformat(),
        "global_settings": {},
        "totals": {},
        "rows": [],
        "model_read_only": True,
        "model_notice": message,
        "message": message,
        "missing_relations": missing_relations,
    }


def wb_pl_payload(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
) -> dict[str, Any]:
    """Build WB P&L from detailed realization rows without Ozon substitution."""
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_finance_lines') AS relation")
        relation_exists = bool((cur.fetchone() or {}).get("relation"))
        if not relation_exists:
            available_from = available_to = date.today()
            rows_count = 0
        else:
            cur.execute(
                """
                SELECT min(operation_date) AS available_from,
                       max(operation_date) AS available_to,
                       count(*) AS rows_count
                FROM public.wb_finance_lines
                """
            )
            bounds = cur.fetchone() or {}
            available_from = bounds.get("available_from") or date.today()
            available_to = bounds.get("available_to") or date.today()
            rows_count = int(bounds.get("rows_count") or 0)

        date_from = date.fromisoformat(raw_from) if raw_from else max(
            available_from, available_to - timedelta(days=29)
        )
        date_to = date.fromisoformat(raw_to) if raw_to else available_to
        if date_from > date_to:
            raise ValueError("Дата начала позже даты окончания")
        if not relation_exists or rows_count == 0:
            return {
                "ok": True,
                "available": False,
                "reason_code": "wb_finance_details_missing",
                "client": client_key,
                "marketplace": "wb",
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "available_date_from": available_from.isoformat(),
                "available_date_to": available_to.isoformat(),
                "message": (
                    "Детальные строки отчётов реализации WB ещё не загружены. "
                    "Запустите раздел «Финансовые отчёты» в исторической загрузке WB."
                ),
                "methodology": {
                    "source": "P&L WB строится только из детальных строк Finance API выбранного аккаунта.",
                },
            }

        cur.execute(
            """
            WITH base AS (
                SELECT *,
                    CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%%возврат%%'
                              OR lower(coalesce(doc_type_name, '')) LIKE '%%return%%'
                         THEN -1::numeric ELSE 1::numeric END AS sale_sign
                FROM public.wb_finance_lines
                WHERE operation_date BETWEEN %s AND %s
            )
            SELECT
                count(*) AS lines,
                count(DISTINCT operation_date) AS operation_days,
                coalesce(sum(retail_amount * sale_sign), 0) AS revenue,
                coalesce(sum(CASE WHEN retail_amount <> 0 OR for_pay <> 0
                                  THEN quantity * sale_sign ELSE 0 END), 0) AS units,
                coalesce(sum(for_pay * sale_sign), 0) AS for_pay,
                coalesce(sum(ppvz_sales_commission * sale_sign), 0) AS commission_without_vat,
                coalesce(sum((retail_amount - for_pay - acquiring_fee) * sale_sign), 0) AS commission,
                coalesce(sum(acquiring_fee * sale_sign), 0) AS acquiring,
                coalesce(sum(delivery_service), 0) AS delivery,
                coalesce(sum(paid_storage), 0) AS storage,
                coalesce(sum(paid_acceptance), 0) AS acceptance,
                coalesce(sum(deduction), 0) AS deduction,
                coalesce(sum(penalty), 0) AS penalty,
                coalesce(sum(additional_payment), 0) AS additional_payment,
                coalesce(sum(cashback_amount), 0) AS cashback,
                coalesce(sum(rebill_logistic_cost), 0) AS rebill_logistics
            FROM base
            """,
            (date_from, date_to),
        )
        totals_row = dict(cur.fetchone() or {})
        expected_days = (date_to - date_from).days + 1
        cur.execute(
            """
            WITH report_periods AS (
                SELECT DISTINCT
                    greatest(coalesce(report_from, operation_date), %s::date) AS covered_from,
                    least(coalesce(report_to, operation_date), %s::date) AS covered_to
                FROM public.wb_finance_lines
                WHERE coalesce(report_to, operation_date) >= %s
                  AND coalesce(report_from, operation_date) <= %s
            ), covered_days AS (
                SELECT DISTINCT day::date AS covered_day
                FROM report_periods
                CROSS JOIN LATERAL generate_series(
                    covered_from::timestamp,
                    covered_to::timestamp,
                    interval '1 day'
                ) AS day
                WHERE covered_from <= covered_to
            )
            SELECT count(*) AS source_days,
                   min(covered_day) AS source_date_from,
                   max(covered_day) AS source_date_to
            FROM covered_days
            """,
            (date_from, date_to, date_from, date_to),
        )
        coverage_row = dict(cur.fetchone() or {})
        source_days = int(coverage_row.get("source_days") or 0)
        source_partial = source_days < expected_days
        cur.execute(
            """
            WITH base AS (
                SELECT *,
                    CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%%возврат%%'
                              OR lower(coalesce(doc_type_name, '')) LIKE '%%return%%'
                         THEN -1::numeric ELSE 1::numeric END AS sale_sign
                FROM public.wb_finance_lines
                WHERE operation_date BETWEEN %s AND %s
            )
            SELECT to_char(date_trunc('month', operation_date), 'YYYY-MM') AS month,
                   coalesce(sum(retail_amount * sale_sign), 0) AS revenue,
                   coalesce(sum(for_pay * sale_sign - delivery_service - paid_storage - paid_acceptance
                       - deduction - penalty + additional_payment + cashback_amount
                       - rebill_logistic_cost), 0) AS marketplace_net
            FROM base
            GROUP BY date_trunc('month', operation_date)
            ORDER BY date_trunc('month', operation_date)
            """,
            (date_from, date_to),
        )
        monthly_rows = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """
            WITH base AS (
                SELECT *,
                    CASE WHEN lower(coalesce(doc_type_name, '')) LIKE '%%возврат%%'
                              OR lower(coalesce(doc_type_name, '')) LIKE '%%return%%'
                         THEN -1::numeric ELSE 1::numeric END AS sale_sign
                FROM public.wb_finance_lines
                WHERE operation_date BETWEEN %s AND %s
                  AND nm_id IS NOT NULL
            )
            SELECT nm_id::text AS sku,
                   max(nullif(vendor_code, '')) AS article,
                   max(nullif(title, '')) AS product_name,
                   max(nullif(subject_name, '')) AS category_name,
                   coalesce(sum(CASE WHEN retail_amount <> 0 OR for_pay <> 0
                                     THEN quantity * sale_sign ELSE 0 END), 0) AS units,
                   coalesce(sum(retail_amount * sale_sign), 0) AS revenue,
                   coalesce(sum((retail_amount - for_pay - acquiring_fee) * sale_sign), 0) AS commission,
                   coalesce(sum(acquiring_fee * sale_sign), 0) AS acquiring,
                   coalesce(sum(delivery_service + rebill_logistic_cost), 0) AS logistics,
                   coalesce(sum(paid_storage), 0) AS storage,
                   coalesce(sum(paid_acceptance + deduction + penalty
                       - additional_payment - cashback_amount), 0) AS other_costs
            FROM base
            GROUP BY nm_id
            ORDER BY abs(sum(retail_amount * sale_sign)) DESC, nm_id

            """,
            (date_from, date_to),
        )
        product_rows = [dict(row) for row in cur.fetchall()]

    revenue = decimal_value(totals_row.get("revenue"))
    for_pay = decimal_value(totals_row.get("for_pay"))
    commission = decimal_value(totals_row.get("commission"))
    commission_without_vat = decimal_value(totals_row.get("commission_without_vat"))
    commission_vat = commission - commission_without_vat
    acquiring = decimal_value(totals_row.get("acquiring"))
    delivery = decimal_value(totals_row.get("delivery"))
    storage = decimal_value(totals_row.get("storage"))
    acceptance = decimal_value(totals_row.get("acceptance"))
    deduction = decimal_value(totals_row.get("deduction"))
    penalty = decimal_value(totals_row.get("penalty"))
    additional_payment = decimal_value(totals_row.get("additional_payment"))
    cashback = decimal_value(totals_row.get("cashback"))
    rebill_logistics = decimal_value(totals_row.get("rebill_logistics"))
    marketplace_net = (
        for_pay - delivery - storage - acceptance - deduction - penalty
        + additional_payment + cashback - rebill_logistics
    )
    explicit_total = (
        revenue - commission - acquiring - delivery - storage - acceptance
        - deduction - penalty + additional_payment + cashback - rebill_logistics
    )
    reconciliation = marketplace_net - explicit_total

    statement = [
        {"key": "revenue", "label": "Продажи и возвраты", "amount": number(revenue), "kind": "total"},
        {"key": "commission", "label": "Комиссия WB с НДС", "amount": number(-commission), "kind": "expense"},
        {"key": "acquiring", "label": "Эквайринг", "amount": number(-acquiring), "kind": "expense"},
        {"key": "delivery", "label": "Логистика WB", "amount": number(-delivery), "kind": "expense"},
        {"key": "storage", "label": "Хранение WB", "amount": number(-storage), "kind": "expense"},
        {"key": "acceptance", "label": "Платная приёмка", "amount": number(-acceptance), "kind": "expense"},
        {"key": "deduction", "label": "Удержания", "amount": number(-deduction), "kind": "expense"},
        {"key": "penalty", "label": "Штрафы", "amount": number(-penalty), "kind": "expense"},
        {"key": "additional_payment", "label": "Доплаты и кешбэк", "amount": number(additional_payment + cashback), "kind": "expense"},
        {"key": "rebill_logistics", "label": "Корректировка логистики", "amount": number(-rebill_logistics), "kind": "expense"},
        {"key": "reconciliation", "label": "Прочие корректировки до суммы к выплате", "amount": number(reconciliation), "kind": "expense"},
        {"key": "marketplace_net", "label": "К выплате продавцу до себестоимости и налогов", "amount": number(marketplace_net), "kind": "subtotal"},
        {"key": "cogs", "label": "Себестоимость", "amount": None, "kind": "expense"},
        {"key": "net_profit", "label": "Чистая прибыль", "amount": None, "kind": "total"},
    ]
    for item in statement:
        amount = item.get("amount")
        item["revenue_pct"] = number(
            Decimal(str(amount)) / revenue * 100 if amount is not None and revenue else None
        )

    monthly = []
    for row in monthly_rows:
        month_revenue = decimal_value(row.get("revenue"))
        month_net = decimal_value(row.get("marketplace_net"))
        monthly.append({
            "month": row.get("month"),
            "revenue": number(month_revenue),
            "marketplace_net": number(month_net),
            "ozon_costs": number(month_revenue - month_net),
            "cogs": None,
            "cogs_complete": False,
            "gross_profit": None,
            "seller_costs": 0.0,
            "manual_expenses": 0.0,
            "taxes": None,
            "net_profit": None,
            "margin_pct": None,
        })

    products = []
    for row in product_rows:
        product_revenue = decimal_value(row.get("revenue"))
        product_costs = sum((
            decimal_value(row.get("commission")),
            decimal_value(row.get("acquiring")),
            decimal_value(row.get("logistics")),
            decimal_value(row.get("storage")),
            decimal_value(row.get("other_costs")),
        ), Decimal("0"))
        products.append({
            "sku": row.get("sku"),
            "article": row.get("article") or row.get("sku"),
            "name": row.get("article") or row.get("sku"),
            "full_name": row.get("product_name") or row.get("article") or row.get("sku"),
            "product_name": row.get("product_name"),
            "category": row.get("category_name") or "Без категории",
            "category_name": row.get("category_name") or "Без категории",
            "units": number(decimal_value(row.get("units"))),
            "revenue": number(product_revenue),
            "seller_revenue": number(product_revenue),
            "cogs": None,
            "cogs_known": False,
            "gross_profit": None,
            "commission": number(-decimal_value(row.get("commission"))),
            "acquiring": number(-decimal_value(row.get("acquiring"))),
            "delivery": number(-decimal_value(row.get("logistics"))),
            "logistics": number(-decimal_value(row.get("logistics"))),
            "storage": number(-decimal_value(row.get("storage"))),
            "advertising": 0.0,
            "other_ozon": number(-decimal_value(row.get("other_costs"))),
            "seller_costs": 0.0,
            "income_tax": 0.0,
            "vat": 0.0,
            "net_profit": None,
            "margin_pct": None,
            "marketplace_costs_before_cogs": number(product_costs),
        })

    return {
        "ok": True,
        "available": True,
        "partial": source_partial,
        "client": client_key,
        "marketplace": "wb",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "available_date_from": available_from.isoformat(),
        "available_date_to": available_to.isoformat(),
        "totals": {
            "revenue": number(revenue),
            "seller_revenue": number(revenue),
            "units": number(decimal_value(totals_row.get("units"))),
            "marketplace_net": number(marketplace_net),
            "ozon_costs": number(revenue - marketplace_net),
            "commission_without_vat": number(commission_without_vat),
            "commission_vat": number(commission_vat),
            "commission_including_vat": number(commission),
            "cogs": None,
            "gross_profit": None,
            "profit_ready": False,
            "seller_unit_costs": 0.0,
            "tax": None,
            "manual_expenses": 0.0,
            "net_profit": None,
            "margin_pct": None,
            "events": int(totals_row.get("lines") or 0),
            "xlsx_events": 0,
            "api_events": int(totals_row.get("lines") or 0),
            "cogs_coverage_units_pct": 0.0,
            "unallocated_marketplace_net": number(reconciliation),
            "tax_configured": False,
        },
        "statement": statement,
        "monthly": monthly,
        "months": monthly,
        "products": products,
        "products_total": len(products),
        "products_limited": len(products) >= 300,
        "expenses": [],
        "expense_items": [],
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "available_from": available_from.isoformat(),
            "available_to": available_to.isoformat(),
        },
        "taxes": {"income_tax_pct": None, "vat_pct": None},
        "line_breakdown": [],
        "data_status": {
            "detail_rows": int(totals_row.get("lines") or 0),
            "source": "POST /api/finance/v1/sales-reports/detailed",
            "coverage_status": "partial" if source_partial else "covered",
            "source_days": source_days,
            "operation_days": int(totals_row.get("operation_days") or 0),
            "expected_days": expected_days,
            "missing_days": max(expected_days - source_days, 0),
            "source_date_from": str(coverage_row.get("source_date_from") or ""),
            "source_date_to": str(coverage_row.get("source_date_to") or ""),
            "requested_date_from": date_from.isoformat(),
            "requested_date_to": date_to.isoformat(),
            "available_date_from": available_from.isoformat(),
            "available_date_to": available_to.isoformat(),
        },
        "methodology": {
            "source": "WB Finance API: детальные строки отчётов реализации выбранного аккаунта и периода.",
            "commission": "Комиссия WB показана с НДС и рассчитана по строкам как retailAmount − forPay − acquiringFee; ppvzSalesCommission содержит сумму без НДС.",
            "net": "forPay уже учитывает комиссию WB и эквайринг; сумма к выплате дополнительно уменьшается только на логистику, хранение, приёмку, удержания, штрафы и корректировки.",
            "sku": "Товарная таблица содержит только начисления с nmId; общие удержания остаются в итоговом P&L.",
            "warning": "Себестоимость WB пока не подключена, поэтому валовая и чистая прибыль намеренно не рассчитываются.",
        },
    }


def historical_supply_unit_costs(
    cur, available_from: date, available_to: date
) -> dict[str, Any]:
    """Normalize account-level supply charges to one historical sale."""
    cur.execute(
        """
        WITH normalized AS (
            SELECT
                line_kind,
                type_id,
                lower(regexp_replace(coalesce(type_name, ''), '[^a-z0-9]+', '', 'g')) AS type_key,
                amount,
                quantity
            FROM public.ozon_finance_lines
        )
        SELECT
            coalesce(sum(abs(quantity)) FILTER (WHERE line_kind = 'revenue'), 0) AS sold_units,
            coalesce(sum(amount) FILTER (
                WHERE line_kind = 'fulfillment_ozon'
                  AND (type_id = 12 OR type_key = 'crossdock')
            ), 0) AS crossdock_amount,
            count(*) FILTER (
                WHERE line_kind = 'fulfillment_ozon'
                  AND (type_id = 12 OR type_key = 'crossdock')
            ) AS crossdock_lines,
            coalesce(sum(amount) FILTER (
                WHERE line_kind = 'fulfillment_ozon'
                  AND (type_id = 77 OR type_key = 'supplyinbound')
            ), 0) AS acceptance_amount,
            count(*) FILTER (
                WHERE line_kind = 'fulfillment_ozon'
                  AND (type_id = 77 OR type_key = 'supplyinbound')
            ) AS acceptance_lines
        FROM normalized
        """
    )
    row = cur.fetchone() or {}
    sold_units = decimal_value(row.get("sold_units"))

    def average(amount_key: str, lines_key: str) -> Decimal | None:
        lines = int(row.get(lines_key) or 0)
        if sold_units <= 0 or lines <= 0:
            return None
        return -decimal_value(row.get(amount_key)) / sold_units

    return {
        "date_from": available_from,
        "date_to": available_to,
        "sold_units": sold_units,
        "crossdock_total": -decimal_value(row.get("crossdock_amount")),
        "crossdock_lines": int(row.get("crossdock_lines") or 0),
        "crossdock_per_unit": average("crossdock_amount", "crossdock_lines"),
        "acceptance_total": -decimal_value(row.get("acceptance_amount")),
        "acceptance_lines": int(row.get("acceptance_lines") or 0),
        "acceptance_per_unit": average("acceptance_amount", "acceptance_lines"),
    }


def historical_supply_source(
    history: dict[str, Any], key: str, label: str
) -> str:
    per_unit = history.get(f"{key}_per_unit")
    if per_unit is None:
        return f"{label}: исторического факта для расчёта среднего нет"
    date_from = history["date_from"].strftime("%d.%m.%Y")
    date_to = history["date_to"].strftime("%d.%m.%Y")
    total = decimal_value(history[f"{key}_total"])
    sold_units = decimal_value(history["sold_units"])
    lines = int(history[f"{key}_lines"])
    return (
        f"{label}: среднее факта за всю историю {date_from}–{date_to}; "
        f"{total.quantize(Decimal('0.01'))} ₽ / "
        f"{sold_units.quantize(Decimal('0.01'))} проданных единиц; "
        f"начислений: {lines}"
    )


def resolve_range(
    cur, raw_from: str | None, raw_to: str | None
) -> tuple[date, date, date, date]:
    available_from, available_to = available_range(cur)
    date_from = date.fromisoformat(raw_from) if raw_from else max(
        available_from, available_to - timedelta(days=29)
    )
    date_to = date.fromisoformat(raw_to) if raw_to else available_to
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    return date_from, date_to, available_from, available_to


def get_global_settings(cur) -> dict[str, Any]:
    cur.execute(
        """
        SELECT tax_pct, vat_pct, acquiring_pct, capital_days, capital_rate_pct,
               advertising_pct, return_rate_pct, target_margin_pct,
               mrc_margin_pct, rrc_margin_pct, updated_at
        FROM public.ozon_unit_global_settings
        WHERE singleton = true
        """
    )
    row = cur.fetchone() or {}
    return row_numbers(row)


def wb_unit_payload(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
) -> dict[str, Any]:
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_api_entities') AS relation")
        relation_exists = bool((cur.fetchone() or {}).get("relation"))
        source = {
            "orders": 0,
            "sales": 0,
            "date_from": None,
            "date_to": None,
            "captured_at": None,
        }
        if relation_exists:
            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE source_key = 'statistics.orders') AS orders,
                    count(*) FILTER (WHERE source_key = 'statistics.sales') AS sales,
                    min(record_date) FILTER (
                        WHERE source_key IN ('statistics.orders', 'statistics.sales')
                    ) AS date_from,
                    max(record_date) FILTER (
                        WHERE source_key IN ('statistics.orders', 'statistics.sales')
                    ) AS date_to,
                    max(captured_at) FILTER (
                        WHERE source_key IN ('statistics.orders', 'statistics.sales')
                    ) AS captured_at
                FROM public.wb_api_entities
                """
            )
            source = dict(cur.fetchone() or source)

    today = date.today()
    available_from = source.get("date_from") or today
    available_to = source.get("date_to") or today
    date_from = date.fromisoformat(raw_from) if raw_from else max(
        available_from, available_to - timedelta(days=29)
    )
    date_to = date.fromisoformat(raw_to) if raw_to else available_to
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    return {
        "ok": True,
        "available": False,
        "reason_code": "wb_unit_costs_not_normalized",
        "client": client_key,
        "marketplace": "wb",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "available_date_from": available_from.isoformat(),
        "available_date_to": available_to.isoformat(),
        "data_status": {
            "orders_loaded": int(source.get("orders") or 0),
            "sales_loaded": int(source.get("sales") or 0),
            "last_snapshot_at": str(source.get("captured_at") or ""),
        },
        "message": (
            "Заказы и продажи WB загружены, но детальные комиссии, логистика и удержания "
            "ещё не нормализованы по SKU. Юнит-экономика не рассчитывается, чтобы не "
            "подменять WB-данные расходами Ozon."
        ),
        "rows": [],
    }



def unit_payload(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
    marketplace: str = "ozon",
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    marketplace = str(marketplace or "ozon").strip().lower()
    if marketplace == "wb":
        from pulse_financial_model import wb_unit_model
        return wb_unit_model(config, raw_from, raw_to, client_key, page, page_size)
    if marketplace != "ozon":
        raise ValueError(f"Неподдерживаемая площадка юнит-экономики: {marketplace}")
    required_relations = [
        "ozon_finance_events",
        "ozon_finance_lines",
        "ozon_unit_global_settings",
        "ozon_adv_daily_raw",
        "ozon_cat_products",
        "ozon_product_price_snapshots",
        "ozon_products",
        "ozon_unit_product_settings",
        "vw_ozon_current_stock_by_sku",
    ]
    with connect_km(config) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT relation_name
            FROM unnest(%s::text[]) AS relation_name
            WHERE to_regclass('public.' || relation_name) IS NULL
            ORDER BY relation_name
            """,
            (required_relations,),
        )
        missing_relations = [row["relation_name"] for row in cur.fetchall()]
        if missing_relations:
            return unavailable_unit_payload(
                raw_from, raw_to, client_key, missing_relations
            )
        date_from, date_to, available_from, available_to = resolve_range(
            cur, raw_from, raw_to
        )
        global_settings = get_global_settings(cur)
        historical_supply = historical_supply_unit_costs(
            cur, available_from, available_to
        )
        cur.execute(
            """
            WITH actual AS (
                SELECT
                    l.sku,
                    max(l.article) FILTER (WHERE nullif(l.article, '') IS NOT NULL) AS article,
                    max(l.product_name) FILTER (WHERE nullif(l.product_name, '') IS NOT NULL) AS product_name,
                    max(l.delivery_schema) FILTER (WHERE nullif(l.delivery_schema, '') IS NOT NULL) AS delivery_schema,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'revenue'), 0) AS revenue,
                    coalesce(sum(l.quantity) FILTER (WHERE l.line_kind = 'revenue'), 0) AS units,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'commission'), 0) AS commission,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'acquiring'), 0) AS acquiring,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'advertising'), 0) AS finance_advertising,
                    coalesce(sum(l.amount) FILTER (
                        WHERE l.line_kind IN ('logistics', 'last_mile', 'fulfillment_ozon')
                    ), 0) AS forward_logistics,
                    coalesce(sum(l.amount) FILTER (
                        WHERE l.line_kind = 'reverse_logistics'
                    ), 0) AS reverse_logistics,
                    coalesce(sum(l.amount) FILTER (
                        WHERE l.line_kind NOT IN (
                            'revenue', 'commission', 'acquiring', 'advertising',
                            'logistics', 'last_mile', 'reverse_logistics', 'fulfillment_ozon'
                        )
                    ), 0) AS other_ozon
                FROM public.ozon_finance_lines l
                WHERE l.operation_date BETWEEN %s AND %s
                  AND nullif(l.sku, '') IS NOT NULL
                GROUP BY l.sku
            ),
            adv AS (
                SELECT
                    ozon_marketplace_article::text AS sku,
                    coalesce(sum(coalesce(fact_expense_rub, expense_rub, 0)), 0) AS ad_spend
                FROM public.ozon_adv_daily_raw
                WHERE report_date BETWEEN %s AND %s
                GROUP BY ozon_marketplace_article::text
            ),
            latest_price_by_sku AS NOT MATERIALIZED (
                SELECT DISTINCT ON (p.sku) p.*
                FROM public.ozon_product_price_snapshots p
                ORDER BY p.sku, p.snapshot_date DESC
            ),
            latest_price_by_offer AS NOT MATERIALIZED (
                SELECT DISTINCT ON (p.offer_id) p.*
                FROM public.ozon_product_price_snapshots p
                WHERE nullif(p.offer_id, '') IS NOT NULL
                ORDER BY p.offer_id, p.snapshot_date DESC, p.sku DESC
            ),
            product_ref_raw AS (
                SELECT
                    p.sku::text AS sku,
                    nullif(p.artikul, '') AS article,
                    nullif(p.nazvanie_tovara, '') AS product_name,
                    nullif(p.kategoriya, '') AS category_name,
                    nullif(p.obem_tovara_l, '') AS source_volume_l,
                    nullif(p.obemnyy_ves_kg, '') AS source_volume_weight,
                    NULL::numeric AS source_depth,
                    NULL::numeric AS source_width,
                    NULL::numeric AS source_height,
                    NULL::text AS source_dimension_unit,
                    NULL::numeric AS source_weight,
                    NULL::text AS source_weight_unit,
                    10 AS source_priority,
                    p.id::bigint AS source_sort_id
                FROM public.ozon_products p
                WHERE nullif(p.sku, '') IS NOT NULL
                UNION ALL
                SELECT
                    ref.sku,
                    nullif(p.artikul, '') AS article,
                    nullif(p.nazvanie_tovara, '') AS product_name,
                    coalesce(nullif(p.category_name, ''), nullif(p.tip, '')) AS category_name,
                    NULL::text AS source_volume_l,
                    NULL::text AS source_volume_weight,
                    nullif(nullif(replace(regexp_replace(p.dlina_upakovki_mm, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric, 0) AS source_depth,
                    nullif(nullif(replace(regexp_replace(p.shirina_upakovki_mm, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric, 0) AS source_width,
                    nullif(nullif(replace(regexp_replace(p.vysota_upakovki_mm, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric, 0) AS source_height,
                    'mm'::text AS source_dimension_unit,
                    nullif(nullif(replace(regexp_replace(p.ves_v_upakovke_g, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric, 0) AS source_weight,
                    'g'::text AS source_weight_unit,
                    20 AS source_priority,
                    p.product_id::bigint AS source_sort_id
                FROM public.ozon_cat_products p
                CROSS JOIN LATERAL (
                    VALUES
                        (nullif(p.sku::text, '')),
                        (nullif(p.product_id::text, '')),
                        (nullif(p.artikul, ''))
                ) AS ref(sku)
                WHERE ref.sku IS NOT NULL
            ),
            product_ref AS (
                SELECT DISTINCT ON (sku)
                    sku,
                    article,
                    product_name,
                    category_name,
                    source_volume_l,
                    source_volume_weight,
                    source_depth,
                    source_width,
                    source_height,
                    source_dimension_unit,
                    source_weight,
                    source_weight_unit
                FROM product_ref_raw
                WHERE sku IS NOT NULL
                ORDER BY sku, source_priority DESC, source_sort_id DESC
            ),
            all_skus AS (
                SELECT sku FROM actual
                UNION
                SELECT lp.sku
                FROM latest_price_by_sku lp
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM actual a
                    LEFT JOIN product_ref apr ON apr.sku = a.sku
                    WHERE nullif(coalesce(a.article, apr.article), '') =
                          nullif(lp.offer_id, '')
                )
                UNION SELECT sku FROM public.ozon_unit_product_settings
                UNION
                SELECT st.sku::text
                FROM public.vw_ozon_current_stock_by_sku st
                WHERE st.sku IS NOT NULL
            ),
            stock_by_sku AS (
                SELECT
                    st.sku::text AS sku,
                    coalesce(sum(st.available_to_sell), 0)::numeric AS available_to_sell,
                    coalesce(sum(st.preparing_to_sell), 0)::numeric AS preparing_to_sell,
                    coalesce(sum(st.in_supply_orders), 0)::numeric AS in_supply_orders,
                    coalesce(sum(st.in_transit_supply), 0)::numeric AS in_transit_supply,
                    max(st.imported_at) AS stock_updated_at
                FROM public.vw_ozon_current_stock_by_sku st
                WHERE st.sku IS NOT NULL
                GROUP BY st.sku::text
            ),
            stock_by_article AS (
                SELECT
                    nullif(st.article, '') AS article,
                    coalesce(sum(st.available_to_sell), 0)::numeric AS available_to_sell,
                    coalesce(sum(st.preparing_to_sell), 0)::numeric AS preparing_to_sell,
                    coalesce(sum(st.in_supply_orders), 0)::numeric AS in_supply_orders,
                    coalesce(sum(st.in_transit_supply), 0)::numeric AS in_transit_supply,
                    max(st.imported_at) AS stock_updated_at
                FROM public.vw_ozon_current_stock_by_sku st
                WHERE nullif(st.article, '') IS NOT NULL
                GROUP BY nullif(st.article, '')
            )
            SELECT
                s.sku,
                coalesce(a.article, pr.article, lp.offer_id) AS article,
                coalesce(a.product_name, pr.product_name, 'SKU ' || s.sku) AS product_name,
                pr.category_name,
                coalesce(a.delivery_schema, 'FBO') AS delivery_schema,
                coalesce(a.revenue, 0) AS actual_revenue,
                coalesce(a.units, 0) AS actual_units,
                coalesce(a.commission, 0) AS actual_commission,
                coalesce(a.acquiring, 0) AS actual_acquiring,
                coalesce(a.finance_advertising, 0) AS actual_finance_advertising,
                coalesce(a.forward_logistics, 0) AS actual_forward_logistics,
                coalesce(a.reverse_logistics, 0) AS actual_reverse_logistics,
                coalesce(a.other_ozon, 0) AS actual_other_ozon,
                coalesce(adv.ad_spend, 0) AS actual_ad_spend,
                lp.snapshot_date,
                lp.marketing_seller_price AS current_price,
                lp.price,
                lp.min_price,
                lp.old_price,
                lp.net_price,
                lp.vat_pct,
                lp.acquiring,
                lp.sales_percent_fbo,
                lp.sales_percent_fbs,
                lp.sales_percent_rfbs,
                lp.fbo_delivery_amount,
                lp.fbo_logistics_min,
                lp.fbo_logistics_max,
                lp.fbo_return_amount,
                lp.fbs_delivery_amount,
                lp.fbs_logistics_min,
                lp.fbs_logistics_max,
                lp.fbs_first_mile_min,
                lp.fbs_first_mile_max,
                lp.fbs_return_amount,
                lp.price_index_color,
                lp.ozon_price_index,
                lp.market_price_index,
                lp.self_price_index,
                coalesce(lp.volume_weight, nullif(replace(regexp_replace(pr.source_volume_weight, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric) AS volume_weight,
                coalesce(lp.depth, pr.source_depth) AS ozon_depth,
                coalesce(lp.width, pr.source_width) AS ozon_width,
                coalesce(lp.height, pr.source_height) AS ozon_height,
                coalesce(lp.dimension_unit, pr.source_dimension_unit) AS ozon_dimension_unit,
                coalesce(lp.weight, pr.source_weight) AS ozon_weight,
                coalesce(lp.weight_unit, pr.source_weight_unit) AS ozon_weight_unit,
                coalesce(us.depth, lp.depth, pr.source_depth) AS depth,
                coalesce(us.width, lp.width, pr.source_width) AS width,
                coalesce(us.height, lp.height, pr.source_height) AS height,
                coalesce(nullif(us.dimension_unit, ''), lp.dimension_unit, pr.source_dimension_unit) AS dimension_unit,
                coalesce(us.weight, lp.weight, pr.source_weight) AS weight,
                coalesce(nullif(us.weight_unit, ''), lp.weight_unit, pr.source_weight_unit) AS weight_unit,
                (
                    us.depth IS NOT NULL OR us.width IS NOT NULL OR us.height IS NOT NULL
                    OR us.weight IS NOT NULL
                ) AS dimensions_overridden,
                nullif(replace(regexp_replace(pr.source_volume_l, '[^0-9,.-]', '', 'g'), ',', '.'), '')::numeric AS volume_l,
                us.cogs_per_unit,
                coalesce(us.fulfillment_per_unit, 0) AS fulfillment_per_unit,
                coalesce(us.inbound_per_unit, 0) AS inbound_per_unit,
                coalesce(us.crossdock_per_unit, 0) AS crossdock_per_unit,
                coalesce(us.acceptance_per_unit, 0) AS acceptance_per_unit,
                coalesce(us.other_per_unit, 0) AS other_per_unit,
                us.planned_price,
                us.return_rate_pct,
                us.target_margin_pct,
                us.target_advertising_pct,
                us.notes,
                coalesce(stock_sku.available_to_sell, stock_article.available_to_sell, 0) AS stock_available,
                coalesce(stock_sku.preparing_to_sell, stock_article.preparing_to_sell, 0) AS stock_preparing,
                coalesce(stock_sku.in_supply_orders, stock_article.in_supply_orders, 0) AS stock_in_supply_orders,
                coalesce(stock_sku.in_transit_supply, stock_article.in_transit_supply, 0) AS stock_in_transit,
                coalesce(stock_sku.stock_updated_at, stock_article.stock_updated_at) AS stock_updated_at
            FROM all_skus s
            LEFT JOIN actual a ON a.sku = s.sku
            LEFT JOIN adv ON adv.sku = s.sku
            LEFT JOIN product_ref pr ON pr.sku = s.sku
            LEFT JOIN LATERAL (
                SELECT candidate.*
                FROM (
                    SELECT price_sku.*, 0 AS match_priority
                    FROM latest_price_by_sku price_sku
                    WHERE price_sku.sku = s.sku
                    UNION ALL
                    SELECT price_offer.*, 1 AS match_priority
                    FROM latest_price_by_offer price_offer
                    WHERE nullif(coalesce(a.article, pr.article), '') IS NOT NULL
                      AND price_offer.offer_id = nullif(coalesce(a.article, pr.article), '')
                ) candidate
                ORDER BY candidate.match_priority
                LIMIT 1
            ) lp ON TRUE
            LEFT JOIN public.ozon_unit_product_settings us ON us.sku = s.sku
            LEFT JOIN stock_by_sku stock_sku ON stock_sku.sku = s.sku
            LEFT JOIN stock_by_article stock_article
              ON stock_sku.sku IS NULL
             AND stock_article.article = nullif(coalesce(a.article, pr.article, lp.offer_id), '')
            ORDER BY coalesce(a.revenue, 0) DESC, s.sku
            """,
            (date_from, date_to, date_from, date_to),
        )
        raw_rows = cur.fetchall()
        cost_structure = unit_cost_structure(cur, date_from, date_to)

    finance_advertising_total_amount = next(
        (
            decimal_value(item.get("amount"))
            for item in cost_structure.get("items", [])
            if item.get("key") == "advertising"
        ),
        Decimal("0"),
    )
    performance_ad_spend_total = sum(
        (
            max(decimal_value(row.get("actual_ad_spend")), Decimal("0"))
            for row in raw_rows
        ),
        Decimal("0"),
    )

    tax_pct = decimal_value(global_settings.get("tax_pct"), nullable=True)
    vat_pct = decimal_value(global_settings.get("vat_pct"), nullable=True)
    if vat_pct is None:
        vat_pct = Decimal("0")
    global_acquiring_pct = decimal_value(
        global_settings.get("acquiring_pct"), nullable=True
    )
    capital_days = decimal_value(
        global_settings.get("capital_days"), nullable=True
    )
    if capital_days is None:
        capital_days = Decimal("30")
    capital_rate_pct = decimal_value(
        global_settings.get("capital_rate_pct"), nullable=True
    )
    if capital_rate_pct is None:
        capital_rate_pct = Decimal("20")
    global_ad_pct = decimal_value(
        global_settings.get("advertising_pct"), nullable=True
    )
    global_return_pct = decimal_value(
        global_settings.get("return_rate_pct"), nullable=True
    )
    mrc_margin_pct = decimal_value(
        global_settings.get("mrc_margin_pct"), nullable=True
    )
    if mrc_margin_pct is None:
        mrc_margin_pct = Decimal("0")
    rrc_margin_pct = decimal_value(
        global_settings.get("rrc_margin_pct"), nullable=True
    )
    if rrc_margin_pct is None:
        rrc_margin_pct = (
            decimal_value(global_settings.get("target_margin_pct"), nullable=True)
            or Decimal("20")
        )
    rows: list[dict[str, Any]] = []
    totals = {
        "revenue": Decimal("0"),
        "units": Decimal("0"),
        "commission": Decimal("0"),
        "acquiring": Decimal("0"),
        "finance_advertising": Decimal("0"),
        "finance_advertising_total": finance_advertising_total_amount,
        "advertising_expense": Decimal("0"),
        "forward_logistics": Decimal("0"),
        "reverse_logistics": Decimal("0"),
        "other_ozon": Decimal("0"),
        "ad_spend": Decimal("0"),
        "cogs": Decimal("0"),
        "vat": Decimal("0"),
        "capital": Decimal("0"),
        "known_profit": Decimal("0"),
        "cogs_units": Decimal("0"),
        "actual_cogs_units": Decimal("0"),
        "stock_units": Decimal("0"),
        "stock_units_with_model": Decimal("0"),
        "planned_profit_on_stock": Decimal("0"),
        "forecast_profit_on_stock": Decimal("0"),
    }
    for source in raw_rows:
        revenue = decimal_value(source["actual_revenue"])
        units = decimal_value(source["actual_units"])
        commission = decimal_value(source["actual_commission"])
        actual_acquiring = decimal_value(source["actual_acquiring"])
        finance_advertising = decimal_value(source["actual_finance_advertising"])
        forward_logistics = decimal_value(source["actual_forward_logistics"])
        reverse_logistics = decimal_value(source["actual_reverse_logistics"])
        other_ozon = decimal_value(source["actual_other_ozon"])
        ad_spend = decimal_value(source["actual_ad_spend"])
        advertising_expense, actual_advertising_source = actual_advertising_allocation(
            finance_advertising_total_amount,
            finance_advertising,
            ad_spend,
            performance_ad_spend_total,
        )
        abs_units = abs(units)
        current_price = decimal_value(
            source["current_price"] or source["price"], nullable=True
        )
        delivery_schema = str(source["delivery_schema"] or "FBO").upper()
        observed_forward = -forward_logistics / abs_units if abs_units else None
        observed_reverse = -reverse_logistics / abs_units if abs_units else None
        if delivery_schema == "FBS":
            commission_pct = decimal_value(
                source["sales_percent_fbs"], nullable=True
            )
            delivery_amount = decimal_value(source["fbs_delivery_amount"])
            logistics_min = decimal_value(source["fbs_logistics_min"])
            logistics_max = decimal_value(source["fbs_logistics_max"])
            first_mile_min = decimal_value(source["fbs_first_mile_min"])
            first_mile_max = decimal_value(source["fbs_first_mile_max"])
            return_tariff = decimal_value(source["fbs_return_amount"])
        else:
            commission_pct = decimal_value(
                source["sales_percent_fbo"], nullable=True
            )
            delivery_amount = decimal_value(source["fbo_delivery_amount"])
            logistics_min = decimal_value(source["fbo_logistics_min"])
            logistics_max = decimal_value(source["fbo_logistics_max"])
            first_mile_min = Decimal("0")
            first_mile_max = Decimal("0")
            return_tariff = decimal_value(source["fbo_return_amount"])
        effective_volume_l = volume_l_from_dimensions(
            decimal_value(source["depth"], nullable=True),
            decimal_value(source["width"], nullable=True),
            decimal_value(source["height"], nullable=True),
            source["dimension_unit"],
        )
        model_volume_weight = (
            effective_volume_l / Decimal("5")
            if effective_volume_l is not None
            else decimal_value(source["volume_weight"], nullable=True)
        )
        api_volume_weight = decimal_value(source["volume_weight"], nullable=True)
        tariff_forward, forward_source = volume_scaled_forward_logistics(
            delivery_amount=delivery_amount,
            logistics_min=logistics_min,
            logistics_max=logistics_max,
            api_volume_weight=api_volume_weight,
            model_volume_weight=model_volume_weight,
            observed_per_unit=observed_forward,
            first_mile_min=first_mile_min,
            first_mile_max=first_mile_max,
        )
        actual_commission_pct = -commission / revenue * 100 if revenue else None
        actual_acquiring_pct = (
            -actual_acquiring / revenue * 100 if revenue else None
        )
        snapshot_acquiring = decimal_value(source["acquiring"], nullable=True)
        acquiring_pct = (
            snapshot_acquiring / current_price * 100
            if current_price and snapshot_acquiring is not None
            else None
        )
        return_rate_pct = (
            decimal_value(source["return_rate_pct"], nullable=True)
            if source["return_rate_pct"] is not None
            else global_return_pct
        )
        if return_rate_pct is not None:
            expected_reverse = return_tariff * return_rate_pct / 100
            reverse_source = f"Тариф × {number(return_rate_pct):g}% возвратов"
        else:
            expected_reverse = None
            reverse_source = "Не задана цель возвратов"
        actual_ad_pct = ad_spend / revenue * 100 if revenue else None
        sku_target_ad_pct = decimal_value(
            source["target_advertising_pct"], nullable=True
        )
        if sku_target_ad_pct is not None:
            ad_pct = sku_target_ad_pct
            ad_source = "Цель SKU"
        elif global_ad_pct is not None:
            ad_pct = global_ad_pct
            ad_source = "Общая цель"
        else:
            ad_pct = None
            ad_source = "Не задана цель ДРР"
        ad_pct_buyout = advertising_pct_per_buyout(ad_pct, return_rate_pct)
        buyout_rate_pct = (
            Decimal("100") - return_rate_pct
            if return_rate_pct is not None and Decimal("0") <= return_rate_pct < Decimal("100")
            else None
        )
        advertising_adjustment_source = (
            f"{ad_source}: {number(ad_pct):g}% ДРР / {number(buyout_rate_pct):g}% выкупа"
            if ad_pct is not None and buyout_rate_pct is not None
            else "Нужны ДРР и доля невыкупа менее 100%"
        )
        from pulse_financial_model import cost_basis
        cogs, cogs_source = cost_basis(source["cogs_per_unit"], abs(revenue / units) if units and revenue else current_price)
        if cogs_source == "actual":
            totals["actual_cogs_units"] += abs_units
        fulfillment = decimal_value(source["fulfillment_per_unit"])
        inbound = decimal_value(source["inbound_per_unit"])
        crossdock = decimal_value(
            historical_supply["crossdock_per_unit"], nullable=True
        )
        acceptance = decimal_value(
            historical_supply["acceptance_per_unit"], nullable=True
        )
        other = decimal_value(source["other_per_unit"])
        seller_components = (fulfillment, inbound, other)
        seller_costs = (
            sum(seller_components, Decimal("0"))
            if all(value is not None for value in seller_components)
            else None
        )
        supply_logistics_components = (crossdock, acceptance)
        supply_logistics = (
            sum(supply_logistics_components, Decimal("0"))
            if all(value is not None for value in supply_logistics_components)
            else None
        )
        planned_forward_logistics = (
            tariff_forward + supply_logistics
            if supply_logistics is not None
            else None
        )
        preparation_costs = (
            seller_costs + supply_logistics
            if seller_costs is not None and supply_logistics is not None
            else None
        )
        capital_cost = (
            (cogs + preparation_costs)
            * capital_rate_pct
            / Decimal("100")
            * capital_days
            / Decimal("365")
            if cogs is not None and preparation_costs is not None
            else None
        )
        planned_acquiring_pct = (
            global_acquiring_pct
            if global_acquiring_pct is not None
            else acquiring_pct
        )
        mrc, rrc = price_corridor(
            commission_pct=commission_pct,
            acquiring_pct=planned_acquiring_pct,
            tax_pct=tax_pct,
            advertising_pct=ad_pct,
            mrc_margin_pct=mrc_margin_pct,
            rrc_margin_pct=rrc_margin_pct,
            forward_logistics=planned_forward_logistics,
            reverse_logistics=expected_reverse,
            cogs=cogs,
            seller_costs=seller_costs,
            vat_pct=vat_pct,
            capital_cost=capital_cost,
            non_buyout_pct=return_rate_pct,
        )
        current_scenario = unit_scenario(
            price=current_price,
            commission_pct=commission_pct,
            acquiring_pct=acquiring_pct,
            tax_pct=tax_pct,
            advertising_pct=ad_pct,
            forward_logistics=planned_forward_logistics,
            reverse_logistics=expected_reverse,
            cogs=cogs,
            seller_costs=seller_costs,
            vat_pct=vat_pct,
            capital_cost=capital_cost,
            non_buyout_pct=return_rate_pct,
        )
        mrc_scenario = unit_scenario(
            price=mrc,
            commission_pct=commission_pct,
            acquiring_pct=planned_acquiring_pct,
            tax_pct=tax_pct,
            advertising_pct=ad_pct,
            forward_logistics=planned_forward_logistics,
            reverse_logistics=expected_reverse,
            cogs=cogs,
            seller_costs=seller_costs,
            vat_pct=vat_pct,
            capital_cost=capital_cost,
            non_buyout_pct=return_rate_pct,
        )
        rrc_scenario = unit_scenario(
            price=rrc,
            commission_pct=commission_pct,
            acquiring_pct=planned_acquiring_pct,
            tax_pct=tax_pct,
            advertising_pct=ad_pct,
            forward_logistics=planned_forward_logistics,
            reverse_logistics=expected_reverse,
            cogs=cogs,
            seller_costs=seller_costs,
            vat_pct=vat_pct,
            capital_cost=capital_cost,
            non_buyout_pct=return_rate_pct,
        )
        actual_cogs = revenue / 3 if cogs_source == "estimated_price_div_3" else cogs * units if cogs is not None else None
        actual_tax = revenue * tax_pct / 100 if tax_pct is not None else None
        actual_vat = revenue * vat_pct / 100
        actual_capital = capital_cost * abs_units if capital_cost is not None else None
        actual_profit = (
            revenue
            + commission
            + actual_acquiring
            + forward_logistics
            + reverse_logistics
            + other_ozon
            - advertising_expense
            - actual_cogs
            - seller_costs * units
            - supply_logistics * units
            - actual_tax
            - actual_vat
            - actual_capital
            if (
                actual_cogs is not None
                and actual_tax is not None
                and actual_capital is not None
                and seller_costs is not None
                and supply_logistics is not None
            )
            else None
        )
        actual_avg_price = revenue / abs_units if abs_units else None
        stock_units = max(decimal_value(source["stock_available"]), Decimal("0"))
        has_dimension_data = any(
            source.get(key) is not None for key in ("depth", "width", "height", "weight")
        )
        dimension_source = (
            "Ручная корректировка"
            if source["dimensions_overridden"]
            else ("Ozon API" if has_dimension_data else "Нет карточки/габаритов в Ozon API")
        )
        dimension_missing_reason = None if has_dimension_data else (
            "Finance Ozon передал только SKU; карточка товара не найдена в "
            "ozon_product_price_snapshots или ozon_cat_products"
        )
        row = row_numbers(source)
        row.update(
            {
                "actual_average_price": number(actual_avg_price),
                "cogs_per_unit": number(source["cogs_per_unit"]),
                "effective_cogs_per_unit": number(cogs),
                "cogs_source": cogs_source,
                "cogs_input": number(source["cogs_per_unit"]),
                "management_result": number(revenue + commission + actual_acquiring + forward_logistics + reverse_logistics + other_ozon - advertising_expense - actual_cogs - seller_costs * units - actual_vat - (actual_tax or Decimal(0))) if cogs is not None and seller_costs is not None else None,
                "actual_commission_pct": number(actual_commission_pct),
                "actual_commission_per_unit": number(-commission / abs_units if abs_units else None),
                "actual_acquiring_pct": number(actual_acquiring_pct),
                "actual_acquiring_per_unit": number(-actual_acquiring / abs_units if abs_units else None),
                "actual_forward_logistics_per_unit": number(observed_forward),
                "actual_reverse_logistics_per_unit": number(observed_reverse),
                "actual_cogs": number(actual_cogs),
                "actual_seller_costs": number(seller_costs * units) if seller_costs is not None else None,
                "actual_tax": number(actual_tax),
                "actual_tax_per_unit": number(actual_tax / abs_units if actual_tax is not None and abs_units else None),
                "actual_vat": number(actual_vat),
                "actual_vat_per_unit": number(actual_vat / abs_units if abs_units else None),
                "actual_capital": number(actual_capital),
                "actual_capital_per_unit": number(actual_capital / abs_units if actual_capital is not None and abs_units else None),
                "actual_ad_pct": number(actual_ad_pct),
                "actual_ad_per_unit": number(ad_spend / abs_units if abs_units else None),
                "actual_advertising_expense": number(advertising_expense),
                "actual_advertising_source": actual_advertising_source,
                "actual_profit": number(actual_profit),
                "actual_profit_per_unit": number(actual_profit / abs_units if actual_profit is not None and abs_units else None),
                "actual_margin_pct": number(actual_profit / revenue * 100 if actual_profit is not None and revenue else None),
                "commission_pct": number(commission_pct),
                "acquiring_pct": number(acquiring_pct),
                "crossdock_per_unit": number(crossdock),
                "acceptance_per_unit": number(acceptance),
                "crossdock_source": historical_supply_source(
                    historical_supply, "crossdock", "CrossDock"
                ),
                "acceptance_source": historical_supply_source(
                    historical_supply, "acceptance", "SupplyInbound"
                ),
                "tax_pct": number(tax_pct),
                "vat_pct_effective": number(vat_pct),
                "model_acquiring_pct": number(planned_acquiring_pct),
                "capital_days_effective": number(capital_days),
                "capital_rate_pct_effective": number(capital_rate_pct),
                "capital_cost_per_unit": number(capital_cost),
                "advertising_pct": number(ad_pct),
                "advertising_pct_buyout_effective": number(ad_pct_buyout),
                "buyout_rate_pct_effective": number(buyout_rate_pct),
                "target_advertising_pct": number(sku_target_ad_pct),
                "advertising_source": ad_source,
                "advertising_adjustment_source": advertising_adjustment_source,
                "return_rate_pct_effective": number(return_rate_pct),
                "return_tariff_per_unit": number(return_tariff),
                "expected_reverse_logistics_per_unit": number(expected_reverse),
                "reverse_logistics_source": reverse_source,
                "tariff_forward_logistics_per_unit": number(tariff_forward),
                "supply_logistics_per_unit": number(supply_logistics),
                "planned_forward_logistics_per_unit": number(planned_forward_logistics),
                "forward_logistics_source": forward_source,
                "model_volume_l": number(effective_volume_l),
                "model_volume_weight": number(model_volume_weight),
                "mrc_margin_pct_effective": number(mrc_margin_pct),
                "rrc_margin_pct_effective": number(rrc_margin_pct),
                "mrc_price": number(mrc),
                "rrc_price": number(rrc),
                "price_corridor_width": number(rrc - mrc if rrc is not None and mrc is not None else None),
                "dimension_source": dimension_source,
                "dimension_missing_reason": dimension_missing_reason,
                "current_scenario": current_scenario,
                "mrc_scenario": mrc_scenario,
                "rrc_scenario": rrc_scenario,
                "planned_scenario": mrc_scenario,
                "forecast_scenario": rrc_scenario,
                "forecast_profit_per_unit": rrc_scenario["profit"],
                "forecast_margin_pct": rrc_scenario["margin_pct"],
                "target_price": number(rrc),
                "cogs_status": "ok" if cogs_source == "actual" else "estimated" if cogs is not None else "missing",
                "planned_profit_on_stock": number(
                    decimal_value(mrc_scenario["profit"]) * stock_units
                    if mrc_scenario["profit"] is not None
                    else None
                ),
                "forecast_profit_on_stock": number(
                    decimal_value(rrc_scenario["profit"]) * stock_units
                    if rrc_scenario["profit"] is not None
                    else None
                ),
            }
        )
        rows.append(row)
        totals["revenue"] += revenue
        totals["units"] += units
        totals["commission"] += commission
        totals["acquiring"] += actual_acquiring
        totals["finance_advertising"] += finance_advertising
        totals["advertising_expense"] += advertising_expense
        totals["forward_logistics"] += forward_logistics
        totals["reverse_logistics"] += reverse_logistics
        totals["other_ozon"] += other_ozon
        totals["ad_spend"] += ad_spend
        totals["vat"] += actual_vat
        totals["stock_units"] += stock_units
        if (
            mrc_scenario["profit"] is not None
            and rrc_scenario["profit"] is not None
        ):
            totals["stock_units_with_model"] += stock_units
            totals["planned_profit_on_stock"] += (
                decimal_value(mrc_scenario["profit"]) * stock_units
            )
            totals["forecast_profit_on_stock"] += (
                decimal_value(rrc_scenario["profit"]) * stock_units
            )
        if actual_capital is not None:
            totals["capital"] += actual_capital
        if cogs is not None:
            totals["cogs"] += actual_cogs
            totals["cogs_units"] += abs_units
            if actual_profit is not None:
                totals["known_profit"] += actual_profit

    rows.sort(key=unit_row_sort_key)
    total_rows = len(rows)
    pagination = None
    if page_size is not None:
        safe_page_size = max(25, min(int(page_size), 300))
        total_pages = max(1, (total_rows + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(int(page or 1), total_pages))
        offset = (safe_page - 1) * safe_page_size
        rows = rows[offset:offset + safe_page_size]
        pagination = {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total_rows,
            "total_pages": total_pages,
        }
    total_abs_units = sum(abs(decimal_value(row["actual_units"])) for row in raw_rows)
    actual_result = (
        totals["known_profit"]
        if tax_pct is not None and totals["cogs_units"] >= total_abs_units
        else None
    )
    stock_model_complete = (
        totals["stock_units_with_model"] >= totals["stock_units"]
    )
    planned_result = (
        totals["planned_profit_on_stock"] if stock_model_complete else None
    )
    forecast_result = (
        totals["forecast_profit_on_stock"] if stock_model_complete else None
    )
    payload = {
        "ok": True,
        "client": client_key,
        "marketplace": "ozon",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "available_date_from": available_from.isoformat(),
        "available_date_to": available_to.isoformat(),
        "global_settings": global_settings,
        "totals": {
            **{key: number(value) for key, value in totals.items()},
            "logistics": number(totals["forward_logistics"] + totals["reverse_logistics"]),
            "sku_count": total_rows,
            "cogs_model_coverage_pct": number(totals["cogs_units"] / total_abs_units * 100) if total_abs_units else None,
            "cogs_coverage_units_pct": number(
                totals["actual_cogs_units"] / total_abs_units * 100
                if total_abs_units
                else Decimal("0")
            ),
            "tax_configured": tax_pct is not None,
            "actual_result": number(actual_result),
            "planned_result": number(planned_result),
            "forecast_result": number(forecast_result),
            "forecast_vs_plan": number(
                forecast_result - planned_result
                if forecast_result is not None and planned_result is not None
                else None
            ),
            "forecast_vs_fact": number(
                forecast_result - actual_result
                if forecast_result is not None and actual_result is not None
                else None
            ),
            "stock_model_coverage_pct": number(
                totals["stock_units_with_model"] / totals["stock_units"] * 100
                if totals["stock_units"]
                else Decimal("100")
            ),
        },
        "historical_supply_costs": {
            "date_from": historical_supply["date_from"].isoformat(),
            "date_to": historical_supply["date_to"].isoformat(),
            "sold_units": number(historical_supply["sold_units"]),
            "crossdock_total": number(historical_supply["crossdock_total"]),
            "crossdock_lines": historical_supply["crossdock_lines"],
            "crossdock_per_unit": number(historical_supply["crossdock_per_unit"]),
            "acceptance_total": number(historical_supply["acceptance_total"]),
            "acceptance_lines": historical_supply["acceptance_lines"],
            "acceptance_per_unit": number(historical_supply["acceptance_per_unit"]),
        },
        "rows": rows,
        "cost_structure": cost_structure,
        "methodology": {
            "actual": (
                "Факт — точные начисления Ozon за период. Комиссия, эквайринг, "
                "прямая и обратная логистика, налоги и прочие услуги показаны раздельно. "
                "Общая сумма рекламы берётся из Finance Ozon и распределяется по SKU "
                "пропорционально расходам Performance API; при отсутствии статьи Finance "
                "используется фактический расход Performance."
            ),
            "current": (
                "Текущая модель — текущая цена, комиссия, эквайринг и тарифы из "
                "/v5/product/info/prices; габариты — /v4/product/info/attributes. "
                "Налог, НДС и стоимость капитала берутся из настроек KM Trade."
            ),
            "plan": (
                "Модель — себестоимость, корректируемые габариты и расходы продавца по SKU. "
                "CrossDock и SupplyInbound автоматически нормализуются по всему доступному "
                "финансовому факту кабинета на одну проданную единицу. "
                "Прямая логистика пересчитывается от объёмного веса: текущая маршрутная "
                "смесь калибруется по факту и ограничивается актуальным диапазоном Ozon API. "
                "Обратная логистика и фактические средние показаны отдельно."
            ),
            "forecast": (
                "МРЦ и РРЦ — расчётные цены под две отдельные цели маржи. "
                "В обе цены включены комиссия, эквайринг, налог, НДС, реклама, "
                "стоимость капитала, прямая и ожидаемая обратная логистика, "
                "себестоимость и расходы продавца."
            ),
            "price_index": (
                "Индекс цены — индикатор конкурентности, не расход и не вычитается из прибыли."
            ),
            "warning": (
                "При незаданной себестоимости или ставке налога прибыль и коридор МРЦ–РРЦ "
                "не рассчитываются; неизвестные значения не подменяются нулём."
            ),
        },
    }
    from pulse_financial_model import MODEL_NOTICE
    payload["model_notice"] = MODEL_NOTICE
    payload["methodology"]["warning"] = MODEL_NOTICE + " Вклад периода исключает общие расходы. При незаданном налоге вклад показан до налога; коридор цен требует полной настройки."
    payload["totals"]["result_basis"] = "after_configured_taxes" if tax_pct is not None else "before_income_tax"
    for row in rows:
        quantity = abs(row.get("actual_units") or 0)
        if row.get("management_result") is not None and quantity:
            row["actual_profit_per_unit"] = row["management_result"] / quantity
            row["actual_margin_pct"] = row["management_result"] / row["actual_revenue"] * 100 if row.get("actual_revenue") else None
    if pagination is not None:
        payload["pagination"] = pagination
    return payload



def save_unit_settings(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Ожидается массив не более 1000 SKU")
    global_payload = payload.get("global_settings") or {}
    prepared: list[tuple[Any, ...]] = []
    with connect_km(config) as conn:
        try:
            with conn.cursor() as cur:
                for source in rows:
                    sku = str(source.get("sku") or "").strip()
                    if not sku:
                        continue
                    return_rate_pct = decimal_value(
                        source.get("return_rate_pct"), nullable=True
                    )
                    if return_rate_pct is not None and not Decimal("0") <= return_rate_pct <= Decimal("100"):
                        raise ValueError("Возвраты: ожидается значение от 0 до 100")
                    target_advertising_pct = decimal_value(
                        source.get("target_advertising_pct"), nullable=True
                    )
                    if (
                        target_advertising_pct is not None
                        and not Decimal("0") <= target_advertising_pct <= Decimal("100")
                    ):
                        raise ValueError("Целевой ДРР: ожидается значение от 0 до 100")
                    dimensions = {
                        key: decimal_value(source.get(key), nullable=True)
                        for key in ("depth", "width", "height", "weight")
                    }
                    for label, value in dimensions.items():
                        if value is not None and value <= 0:
                            raise ValueError(f"{label}: ожидается положительное значение")
                    prepared.append(
                        (
                            sku,
                            decimal_value(source.get("cogs_per_unit"), nullable=True),
                            decimal_value(source.get("fulfillment_per_unit")),
                            decimal_value(source.get("inbound_per_unit")),
                            decimal_value(source.get("crossdock_per_unit")),
                            decimal_value(source.get("acceptance_per_unit")),
                            decimal_value(source.get("other_per_unit")),
                            return_rate_pct,
                            target_advertising_pct,
                            dimensions["depth"],
                            dimensions["width"],
                            dimensions["height"],
                            str(source.get("dimension_unit") or "").strip()[:16] or None,
                            dimensions["weight"],
                            str(source.get("weight_unit") or "").strip()[:16] or None,
                            str(source.get("notes") or "").strip()[:1000] or None,
                        )
                    )
                if prepared:
                    execute_values(
                        cur,
                        """
                        INSERT INTO public.ozon_unit_product_settings (
                            sku, cogs_per_unit, fulfillment_per_unit,
                            inbound_per_unit, crossdock_per_unit, acceptance_per_unit,
                            other_per_unit, return_rate_pct,
                            target_advertising_pct, depth, width, height,
                            dimension_unit, weight, weight_unit, notes
                        ) VALUES %s
                        ON CONFLICT (sku) DO UPDATE SET
                            cogs_per_unit = EXCLUDED.cogs_per_unit,
                            fulfillment_per_unit = EXCLUDED.fulfillment_per_unit,
                            inbound_per_unit = EXCLUDED.inbound_per_unit,
                            crossdock_per_unit = EXCLUDED.crossdock_per_unit,
                            acceptance_per_unit = EXCLUDED.acceptance_per_unit,
                            other_per_unit = EXCLUDED.other_per_unit,
                            return_rate_pct = EXCLUDED.return_rate_pct,
                            target_advertising_pct = EXCLUDED.target_advertising_pct,
                            depth = EXCLUDED.depth,
                            width = EXCLUDED.width,
                            height = EXCLUDED.height,
                            dimension_unit = EXCLUDED.dimension_unit,
                            weight = EXCLUDED.weight,
                            weight_unit = EXCLUDED.weight_unit,
                            notes = EXCLUDED.notes,
                            updated_at = now()
                        """,
                        prepared,
                    )
                if global_payload:
                    tax_pct = decimal_value(global_payload.get("tax_pct"), nullable=True)
                    vat_pct = decimal_value(global_payload.get("vat_pct"), nullable=True)
                    acquiring_pct = decimal_value(global_payload.get("acquiring_pct"), nullable=True)
                    capital_days = decimal_value(global_payload.get("capital_days"), nullable=True)
                    capital_rate_pct = decimal_value(global_payload.get("capital_rate_pct"), nullable=True)
                    advertising_pct = decimal_value(global_payload.get("advertising_pct"), nullable=True)
                    return_rate_pct = decimal_value(global_payload.get("return_rate_pct"), nullable=True)
                    mrc_margin_pct = decimal_value(global_payload.get("mrc_margin_pct"), nullable=True)
                    rrc_margin_pct = decimal_value(global_payload.get("rrc_margin_pct"), nullable=True)
                    if mrc_margin_pct is None:
                        mrc_margin_pct = Decimal("0")
                    if rrc_margin_pct is None:
                        rrc_margin_pct = Decimal("20")
                    for label, value in (
                        ("Налог", tax_pct),
                        ("НДС", vat_pct),
                        ("Эквайринг", acquiring_pct),
                        ("Стоимость денег", capital_rate_pct),
                        ("Реклама", advertising_pct),
                        ("Возвраты", return_rate_pct),
                        ("Цель маржи МРЦ", mrc_margin_pct),
                        ("Цель маржи РРЦ", rrc_margin_pct),
                    ):
                        if value is not None and not Decimal("0") <= value <= Decimal("100"):
                            raise ValueError(f"{label}: ожидается значение от 0 до 100")
                    if mrc_margin_pct > rrc_margin_pct:
                        raise ValueError("Цель маржи МРЦ не может быть выше цели маржи РРЦ")
                    if capital_days is not None and not Decimal("0") <= capital_days <= Decimal("3650"):
                        raise ValueError("Срок капитала: ожидается значение от 0 до 3650 дней")
                    cur.execute(
                        """
                        INSERT INTO public.ozon_unit_global_settings (
                            singleton, tax_pct, vat_pct, acquiring_pct,
                            capital_days, capital_rate_pct, advertising_pct,
                            return_rate_pct, target_margin_pct,
                            mrc_margin_pct, rrc_margin_pct
                        ) VALUES (true, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (singleton) DO UPDATE SET
                            tax_pct = EXCLUDED.tax_pct,
                            vat_pct = EXCLUDED.vat_pct,
                            acquiring_pct = EXCLUDED.acquiring_pct,
                            capital_days = EXCLUDED.capital_days,
                            capital_rate_pct = EXCLUDED.capital_rate_pct,
                            advertising_pct = EXCLUDED.advertising_pct,
                            return_rate_pct = EXCLUDED.return_rate_pct,
                            target_margin_pct = EXCLUDED.target_margin_pct,
                            mrc_margin_pct = EXCLUDED.mrc_margin_pct,
                            rrc_margin_pct = EXCLUDED.rrc_margin_pct,
                            updated_at = now()
                        """,
                        (
                            tax_pct, vat_pct, acquiring_pct, capital_days,
                            capital_rate_pct, advertising_pct, return_rate_pct,
                            rrc_margin_pct, mrc_margin_pct, rrc_margin_pct,
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"ok": True, "saved_rows": len(prepared)}


def _cogs_header(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", "", str(value or "").strip().lower())


def parse_cogs_workbook(workbook_bytes: bytes) -> list[dict[str, Any]]:
    if not workbook_bytes:
        raise ValueError("Файл пуст")
    if len(workbook_bytes) > 8 * 1024 * 1024:
        raise ValueError("Размер XLSX не должен превышать 8 МБ")
    try:
        workbook = load_workbook(BytesIO(workbook_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Не удалось прочитать XLSX. Используйте скачанный шаблон.") from exc
    sheet = workbook.active
    aliases = {
        "sku": {"sku", "артикул", "ozonsku"},
        "name": {"наименование", "название", "товар"},
        "cogs": {
            "себестоимость", "себестоимостьруб", "себестоимостьрублей",
            "себес", "себесруб", "себесрублей",
        },
    }
    header_row = None
    columns: dict[str, int] = {}
    for row_index, row in enumerate(sheet.iter_rows(min_row=1, max_row=20, values_only=True), 1):
        normalized = [_cogs_header(value) for value in row]
        found = {
            key: next((index for index, value in enumerate(normalized) if value in values), None)
            for key, values in aliases.items()
        }
        if all(found[key] is not None for key in aliases):
            header_row = row_index
            columns = {key: int(value) for key, value in found.items()}
            break
    if header_row is None:
        raise ValueError("Нужны колонки: SKU, Наименование, Себестоимость, ₽")

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for row_index, row in enumerate(
        sheet.iter_rows(min_row=header_row + 1, max_row=header_row + 2000, values_only=True),
        header_row + 1,
    ):
        sku = str(row[columns["sku"]] or "").strip()
        name = str(row[columns["name"]] or "").strip()
        raw_cogs = row[columns["cogs"]]
        if raw_cogs in (None, ""):
            continue
        if not sku:
            errors.append(f"Строка {row_index}: не указан SKU")
            continue
        if sku in seen:
            errors.append(f"Строка {row_index}: SKU {sku} повторяется")
            continue
        seen.add(sku)
        try:
            cogs = decimal_value(raw_cogs, nullable=True)
        except ValueError:
            errors.append(f"Строка {row_index}: некорректная себестоимость для SKU {sku}")
            continue
        if cogs is None or cogs < 0:
            errors.append(f"Строка {row_index}: себестоимость SKU {sku} должна быть неотрицательной")
            continue
        parsed.append({"sku": sku, "name": name, "cogs_per_unit": cogs})
    workbook.close()
    if errors:
        raise ValueError("; ".join(errors[:12]))
    if not parsed:
        raise ValueError("В шаблоне нет заполненных строк")
    return parsed


def import_cogs_workbook(config: dict[str, Any], workbook_bytes: bytes) -> dict[str, Any]:
    source_rows = parse_cogs_workbook(workbook_bytes)
    current_rows = unit_payload(config).get("rows") or []
    by_sku = {
        str(row.get("sku") or "").strip(): str(row.get("sku") or "").strip()
        for row in current_rows
    }
    by_article = {
        str(row.get("article") or "").strip(): str(row.get("sku") or "").strip()
        for row in current_rows
        if str(row.get("article") or "").strip()
    }
    prepared: list[tuple[str, Decimal]] = []
    unknown: list[str] = []
    for row in source_rows:
        source_sku = str(row["sku"])
        target_sku = by_sku.get(source_sku) or by_article.get(source_sku)
        if not target_sku:
            unknown.append(source_sku)
            continue
        prepared.append((target_sku, row["cogs_per_unit"]))
    if unknown:
        raise ValueError("SKU не найдены в KM Trade: " + ", ".join(unknown[:20]))

    with connect_km(config) as conn:
        try:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO public.ozon_unit_product_settings (sku, cogs_per_unit)
                    VALUES %s
                    ON CONFLICT (sku) DO UPDATE SET
                        cogs_per_unit = EXCLUDED.cogs_per_unit,
                        updated_at = now()
                    """,
                    prepared,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"ok": True, "saved_rows": len(prepared)}


def _pl_payload_base(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
    marketplace: str = "ozon",
) -> dict[str, Any]:
    marketplace = str(marketplace or "ozon").strip().lower()
    if marketplace == "wb":
        return wb_pl_payload(config, raw_from, raw_to, client_key)
    if marketplace != "ozon":
        raise ValueError(f"Неподдерживаемый маркетплейс P&L: {marketplace}")
    with connect_km(config) as conn, conn.cursor() as cur:
        date_from, date_to, available_from, available_to = resolve_range(
            cur, raw_from, raw_to
        )
        settings = get_global_settings(cur)
        tax_pct = decimal_value(settings.get("tax_pct"), nullable=True)
        cur.execute(
            """
            SELECT
                coalesce(sum(total_amount), 0) AS marketplace_net,
                count(*) AS events,
                count(*) FILTER (WHERE source_kind = 'ozon_xlsx') AS xlsx_events,
                count(*) FILTER (WHERE source_kind = 'ozon_api') AS api_events
            FROM public.ozon_finance_events
            WHERE operation_date BETWEEN %s AND %s
            """,
            (date_from, date_to),
        )
        event_totals = cur.fetchone()
        cur.execute(
            """
            SELECT
                line_kind,
                coalesce(sum(amount), 0) AS amount,
                coalesce(sum(quantity), 0) AS units,
                count(*) AS lines
            FROM public.ozon_finance_lines
            WHERE operation_date BETWEEN %s AND %s
            GROUP BY line_kind
            ORDER BY line_kind
            """,
            (date_from, date_to),
        )
        line_rows = cur.fetchall()
        by_kind = {
            row["line_kind"]: decimal_value(row["amount"]) for row in line_rows
        }
        units = by_kind.get("__never__", Decimal("0"))
        for row in line_rows:
            if row["line_kind"] == "revenue":
                units = decimal_value(row["units"])
                break
        revenue = by_kind.get("revenue", Decimal("0"))
        marketplace_net = decimal_value(event_totals["marketplace_net"])

        cur.execute(
            """
            SELECT
                coalesce(sum(l.quantity * s.cogs_per_unit)
                    FILTER (WHERE s.cogs_per_unit IS NOT NULL), 0) AS cogs,
                coalesce(sum(l.quantity * (
                    coalesce(s.fulfillment_per_unit, 0)
                    + coalesce(s.inbound_per_unit, 0)
                    + coalesce(s.crossdock_per_unit, 0)
                    + coalesce(s.acceptance_per_unit, 0)
                    + coalesce(s.other_per_unit, 0)
                )), 0) AS seller_unit_costs,
                coalesce(sum(abs(l.quantity))
                    FILTER (WHERE s.cogs_per_unit IS NOT NULL), 0) AS covered_units,
                coalesce(sum(abs(l.quantity)), 0) AS total_units
            FROM public.ozon_finance_lines l
            LEFT JOIN public.ozon_unit_product_settings s ON s.sku = l.sku
            WHERE l.operation_date BETWEEN %s AND %s
              AND l.line_kind = 'revenue'
            """,
            (date_from, date_to),
        )
        cost_totals = cur.fetchone()
        cogs = decimal_value(cost_totals["cogs"])
        seller_unit_costs = decimal_value(cost_totals["seller_unit_costs"])
        covered_units = decimal_value(cost_totals["covered_units"])
        total_units = decimal_value(cost_totals["total_units"])
        tax = revenue * (tax_pct or Decimal("0")) / 100

        cur.execute(
            """
            SELECT expense_id, date_from, date_to, expense_name, amount,
                   allocation_method, notes
            FROM public.ozon_pl_expenses
            WHERE date_from = %s AND date_to = %s
            ORDER BY expense_id
            """,
            (date_from, date_to),
        )
        expenses = [row_numbers(row) for row in cur.fetchall()]
        manual_expenses = sum(
            (decimal_value(row["amount"]) for row in expenses), Decimal("0")
        )
        cogs_complete = total_units == 0 or covered_units >= total_units
        profit_ready = cogs_complete and tax_pct is not None
        net_profit = (
            marketplace_net - cogs - seller_unit_costs - tax - manual_expenses
            if profit_ready
            else None
        )
        gross_profit = revenue - cogs if cogs_complete else None

        cur.execute(
            """
            WITH monthly_events AS (
                SELECT date_trunc('month', operation_date)::date AS month,
                       sum(total_amount) AS marketplace_net
                FROM public.ozon_finance_events
                WHERE operation_date BETWEEN %s AND %s
                GROUP BY 1
            ),
            monthly_lines AS (
                SELECT date_trunc('month', l.operation_date)::date AS month,
                       sum(l.amount) FILTER (WHERE l.line_kind = 'revenue') AS revenue,
                       sum(l.quantity) FILTER (WHERE l.line_kind = 'revenue') AS units,
                       sum(l.quantity * s.cogs_per_unit)
                           FILTER (WHERE l.line_kind = 'revenue' AND s.cogs_per_unit IS NOT NULL) AS cogs,
                       sum(abs(l.quantity))
                           FILTER (WHERE l.line_kind = 'revenue' AND s.cogs_per_unit IS NOT NULL) AS covered_units,
                       sum(abs(l.quantity))
                           FILTER (WHERE l.line_kind = 'revenue') AS total_units,
                       sum(l.quantity * (
                           coalesce(s.fulfillment_per_unit, 0)
                           + coalesce(s.inbound_per_unit, 0)
                           + coalesce(s.crossdock_per_unit, 0)
                           + coalesce(s.acceptance_per_unit, 0)
                           + coalesce(s.other_per_unit, 0)
                       )) FILTER (WHERE l.line_kind = 'revenue') AS seller_costs
                FROM public.ozon_finance_lines l
                LEFT JOIN public.ozon_unit_product_settings s ON s.sku = l.sku
                WHERE l.operation_date BETWEEN %s AND %s
                GROUP BY 1
            )
            SELECT
                e.month,
                coalesce(l.revenue, 0) AS revenue,
                coalesce(l.units, 0) AS units,
                coalesce(e.marketplace_net, 0) AS marketplace_net,
                coalesce(l.cogs, 0) AS cogs,
                coalesce(l.covered_units, 0) AS covered_units,
                coalesce(l.total_units, 0) AS total_units,
                coalesce(l.seller_costs, 0) AS seller_costs
            FROM monthly_events e
            LEFT JOIN monthly_lines l USING (month)
            ORDER BY e.month
            """,
            (date_from, date_to, date_from, date_to),
        )
        monthly = []
        for row in cur.fetchall():
            item = row_numbers(row)
            month_revenue = decimal_value(row["revenue"])
            month_net = decimal_value(row["marketplace_net"])
            month_cogs = decimal_value(row["cogs"])
            month_seller = decimal_value(row["seller_costs"])
            month_tax = month_revenue * (tax_pct or Decimal("0")) / 100
            month_covered = decimal_value(row["covered_units"])
            month_total = decimal_value(row["total_units"])
            month_cogs_complete = month_total == 0 or month_covered >= month_total
            month_profit = (
                month_net - month_cogs - month_seller - month_tax
                if month_cogs_complete and tax_pct is not None
                else None
            )
            item["tax"] = number(month_tax) if tax_pct is not None else None
            item["cogs_complete"] = month_cogs_complete
            item["cogs_coverage_units_pct"] = number(
                month_covered / month_total * 100 if month_total else Decimal("100")
            )
            item["net_profit"] = number(month_profit)
            item["margin_pct"] = number(
                month_profit / month_revenue * 100
                if month_profit is not None and month_revenue
                else None
            )
            monthly.append(item)

        cur.execute(
            """
            WITH sku_lines AS (
                SELECT
                    l.sku,
                    max(l.article) FILTER (WHERE nullif(l.article, '') IS NOT NULL) AS article,
                    max(l.product_name) FILTER (WHERE nullif(l.product_name, '') IS NOT NULL) AS product_name,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'revenue'), 0) AS revenue,
                    coalesce(sum(l.quantity) FILTER (WHERE l.line_kind = 'revenue'), 0) AS units,
                    coalesce(sum(l.amount) FILTER (WHERE l.line_kind = 'commission'), 0) AS commission,
                    coalesce(sum(l.amount) FILTER (
                        WHERE l.line_kind IN ('logistics', 'last_mile', 'reverse_logistics', 'fulfillment_ozon')
                    ), 0) AS logistics,
                    coalesce(sum(l.amount) FILTER (
                        WHERE l.line_kind NOT IN (
                            'revenue', 'commission', 'logistics', 'last_mile',
                            'reverse_logistics', 'fulfillment_ozon'
                        )
                    ), 0) AS other_ozon,
                    coalesce(sum(l.amount), 0) AS component_net
                FROM public.ozon_finance_lines l
                WHERE l.operation_date BETWEEN %s AND %s
                  AND nullif(l.sku, '') IS NOT NULL
                GROUP BY l.sku
            ),
            product_ref AS (
                SELECT DISTINCT ON (p.sku)
                    p.sku::text AS sku,
                    nullif(p.artikul, '') AS article,
                    nullif(p.nazvanie_tovara, '') AS product_name,
                    nullif(p.kategoriya, '') AS category_name
                FROM public.ozon_products p
                WHERE nullif(p.sku, '') IS NOT NULL
                ORDER BY p.sku, p.id DESC
            )
            SELECT
                l.*, coalesce(l.article, p.article) AS article_resolved,
                coalesce(l.product_name, p.product_name, 'SKU ' || l.sku) AS product_name_resolved,
                p.category_name,
                count(*) OVER() AS products_total,
                sum(l.component_net) OVER() AS sku_component_total_all,
                s.cogs_per_unit,
                coalesce(s.fulfillment_per_unit, 0) AS fulfillment_per_unit,
                coalesce(s.inbound_per_unit, 0) AS inbound_per_unit,
                coalesce(s.crossdock_per_unit, 0) AS crossdock_per_unit,
                coalesce(s.acceptance_per_unit, 0) AS acceptance_per_unit,
                coalesce(s.other_per_unit, 0) AS other_per_unit
            FROM sku_lines l
            LEFT JOIN product_ref p ON p.sku = l.sku
            LEFT JOIN public.ozon_unit_product_settings s ON s.sku = l.sku
            ORDER BY l.revenue DESC

            """,
            (date_from, date_to),
        )
        products = []
        products_total = 0
        sku_component_total = Decimal("0")
        for row in cur.fetchall():
            products_total = max(products_total, int(row.get("products_total") or 0))
            sku_component_total = decimal_value(row.get("sku_component_total_all"))
            product_revenue = decimal_value(row["revenue"])
            product_units = decimal_value(row["units"])
            product_component_net = decimal_value(row["component_net"])
            product_cogs_per_unit = decimal_value(
                row["cogs_per_unit"], nullable=True
            )
            product_cogs = (
                product_units * product_cogs_per_unit
                if product_cogs_per_unit is not None
                else None
            )
            product_seller_cost = product_units * (
                decimal_value(row["fulfillment_per_unit"])
                + decimal_value(row["inbound_per_unit"])
                + decimal_value(row["crossdock_per_unit"])
                + decimal_value(row["acceptance_per_unit"])
                + decimal_value(row["other_per_unit"])
            )
            product_tax = product_revenue * (tax_pct or Decimal("0")) / 100
            product_profit = (
                product_component_net
                - product_cogs
                - product_seller_cost
                - product_tax
                if product_cogs is not None and tax_pct is not None
                else None
            )
            products.append(
                {
                    "sku": row["sku"],
                    "article": row["article_resolved"],
                    "product_name": row["product_name_resolved"],
                    "category_name": row["category_name"],
                    "units": number(product_units),
                    "revenue": number(product_revenue),
                    "commission": number(decimal_value(row["commission"])),
                    "logistics": number(decimal_value(row["logistics"])),
                    "other_ozon": number(decimal_value(row["other_ozon"])),
                    "marketplace_component_net": number(product_component_net),
                    "cogs": number(product_cogs),
                    "seller_costs": number(product_seller_cost),
                    "tax": number(product_tax),
                    "profit_before_common_costs": number(product_profit),
                    "margin_pct": number(
                        product_profit / product_revenue * 100
                        if product_profit is not None and product_revenue
                        else None
                    ),
                    "cogs_status": (
                        "ok" if product_cogs_per_unit is not None else "missing"
                    ),
                }
            )

    profit_before_tax = (
        marketplace_net - cogs - seller_unit_costs - manual_expenses
        if cogs_complete
        else None
    )
    statement = [
        {"key": "revenue", "label": "Продажи и возвраты до комиссий", "amount": number(revenue)},
        {"key": "cogs", "label": "Себестоимость", "amount": number(-cogs), "is_partial": not cogs_complete},
        {"key": "gross_profit", "label": "Валовая прибыль", "amount": number(gross_profit)},
        {
            "key": "commission",
            "label": "Комиссия Ozon",
            "amount": number(by_kind.get("commission", Decimal("0"))),
        },
        {
            "key": "acquiring",
            "label": "Эквайринг Ozon",
            "amount": number(by_kind.get("acquiring", Decimal("0"))),
        },
        {
            "key": "logistics",
            "label": "Логистика и возвраты Ozon",
            "amount": number(
                sum(
                    (
                        by_kind.get(key, Decimal("0"))
                        for key in (
                            "logistics",
                            "last_mile",
                            "reverse_logistics",
                        )
                    ),
                    Decimal("0"),
                )
            ),
        },
        {
            "key": "fulfillment_ozon",
            "label": "Кросс-докинг, приёмка и упаковка Ozon",
            "amount": number(by_kind.get("fulfillment_ozon", Decimal("0"))),
        },
        {
            "key": "storage",
            "label": "Хранение Ozon",
            "amount": number(by_kind.get("storage", Decimal("0"))),
        },
        {
            "key": "advertising",
            "label": "Реклама Ozon по финансовым начислениям",
            "amount": number(by_kind.get("advertising", Decimal("0"))),
        },
        {
            "key": "other_ozon",
            "label": "Прочие начисления Ozon",
            "amount": number(
                marketplace_net
                - revenue
                - by_kind.get("commission", Decimal("0"))
                - by_kind.get("acquiring", Decimal("0"))
                - by_kind.get("fulfillment_ozon", Decimal("0"))
                - by_kind.get("storage", Decimal("0"))
                - sum(
                    (
                        by_kind.get(key, Decimal("0"))
                        for key in (
                            "logistics",
                            "last_mile",
                            "reverse_logistics",
                            "advertising",
                        )
                    ),
                    Decimal("0"),
                )
            ),
        },
        {
            "key": "seller_unit_costs",
            "label": "Фулфилмент, поставка и прочее продавца",
            "amount": number(-seller_unit_costs),
        },
        {"key": "tax", "label": "Налог", "amount": number(-tax) if tax_pct is not None else None},
        {
            "key": "manual_expenses",
            "label": "Дополнительные статьи затрат",
            "amount": number(-manual_expenses),
        },
        {
            "key": "profit_before_tax",
            "label": "Прибыль до налогов",
            "amount": number(profit_before_tax),
        },
        {"key": "net_profit", "label": "Чистая прибыль", "amount": number(net_profit)},
    ]
    statement_kinds = {
        "revenue": "total",
        "gross_profit": "subtotal",
        "profit_before_tax": "subtotal",
        "net_profit": "total",
    }
    for item in statement:
        item["kind"] = statement_kinds.get(item["key"], "expense")
        amount = item.get("amount")
        item["revenue_pct"] = (
            number(Decimal(str(amount)) / revenue * 100)
            if amount is not None and revenue
            else None
        )

    for item in monthly:
        month_revenue = decimal_value(item.get("revenue"))
        month_marketplace_net = decimal_value(item.get("marketplace_net"))
        month_cogs = decimal_value(item.get("cogs"))
        item["gross_profit"] = (
            number(month_revenue - month_cogs)
            if item.get("cogs_complete")
            else None
        )
        item["ozon_costs"] = number(month_revenue - month_marketplace_net)
        item["manual_expenses"] = 0.0
        item["taxes"] = item.get("tax")

    for item in products:
        cogs_known = item.get("cogs_status") != "missing"
        product_revenue = decimal_value(item.get("revenue"))
        product_cogs = decimal_value(item.get("cogs"), nullable=True)
        item.update(
            {
                "name": item.get("article") or item.get("sku"),
                "full_name": item.get("product_name") or item.get("article") or item.get("sku"),
                "category": item.get("category_name") or "Без категории",
                "seller_revenue": item.get("revenue"),
                "cogs_known": cogs_known,
                "gross_profit": (
                    number(product_revenue - product_cogs)
                    if product_cogs is not None
                    else None
                ),
                "acquiring": 0.0,
                "delivery": item.get("logistics") or 0.0,
                "storage": 0.0,
                "advertising": 0.0,
                "acceptance": 0.0,
                "penalties": 0.0,
                "income_tax": item.get("tax") or 0.0,
                "vat": 0.0,
                "net_profit": item.get("profit_before_common_costs"),
            }
        )

    expense_items = [
        {
            "item_id": row.get("expense_id"),
            "is_active": True,
            "expense_month": str(row.get("date_from") or date_from.isoformat())[:7],
            "category": "Операционные расходы",
            "label": row.get("expense_name") or "",
            "amount_rub": row.get("amount") or 0.0,
            "comment": row.get("notes") or "",
        }
        for row in expenses
    ]
    return {
        "ok": True,
        "client": client_key,
        "marketplace": "ozon",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "available_date_from": available_from.isoformat(),
        "available_date_to": available_to.isoformat(),
        "totals": {
            "revenue": number(revenue),
            "seller_revenue": number(revenue),
            "units": number(units),
            "marketplace_net": number(marketplace_net),
            "ozon_costs": number(revenue - marketplace_net),
            "cogs": number(cogs),
            "gross_profit": number(gross_profit),
            "profit_ready": profit_ready,
            "seller_unit_costs": number(seller_unit_costs),
            "tax": number(tax),
            "manual_expenses": number(manual_expenses),
            "net_profit": number(net_profit),
            "margin_pct": number(
                net_profit / revenue * 100
                if net_profit is not None and revenue
                else None
            ),
            "events": int(event_totals["events"]),
            "xlsx_events": int(event_totals["xlsx_events"]),
            "api_events": int(event_totals["api_events"]),
            "cogs_coverage_units_pct": number(
                covered_units / total_units * 100
                if total_units
                else Decimal("0")
            ),
            "unallocated_marketplace_net": number(
                marketplace_net - sku_component_total
            ),
            "tax_configured": tax_pct is not None,
        },
        "statement": statement,
        "monthly": monthly,
        "months": monthly,
        "products": products,
        "products_total": products_total,
        "products_limited": products_total > len(products),
        "expenses": expenses,
        "expense_items": expense_items,
        "period": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "available_from": available_from.isoformat(),
            "available_to": available_to.isoformat(),
        },
        "taxes": {
            "income_tax_pct": number(tax_pct) if tax_pct is not None else None,
            "vat_pct": number(decimal_value(settings.get("vat_pct"))),
        },
        "global_settings": settings,
        "line_breakdown": [row_numbers(row) for row in line_rows],
        "methodology": {
            "net": (
                "Итог Ozon считается по total_amount каждого начисления и полностью "
                "сверяется с финансовым источником."
            ),
            "sku": (
                "SKU-таблица использует только начисления с точной привязкой к SKU; "
                "общие начисления показаны отдельно и не размазываются скрыто."
            ),
            "advertising": (
                "В P&L реклама берётся из финансовых начислений Ozon. "
                "Performance API используется для операционной атрибуции по SKU, "
                "но повторно из прибыли не вычитается."
            ),
            "warning": (
                "Незаданная себестоимость оставляет прибыль соответствующего SKU пустой. "
                "Налог применяется только после явной настройки."
            ),
        },
    }


def pl_workbook_bytes(data: dict[str, Any]) -> bytes:
    """Build the same three-sheet P&L export contract used by Konstex."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "P&L"
    period = data["period"]
    sheet.append(
        [
            f"P&L {data.get('client', '')} · {data.get('marketplace', '').upper()}",
            f"{period['date_from']} — {period['date_to']}",
            "Сумма, ₽",
            "% выручки",
        ]
    )
    for row in data["statement"]:
        revenue_pct = row.get("revenue_pct")
        sheet.append(
            [
                row["label"],
                "",
                row.get("amount"),
                revenue_pct / 100 if revenue_pct is not None else None,
            ]
        )
        current = sheet.max_row
        if row.get("kind") in {"subtotal", "total"}:
            for cell in sheet[current]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill(
                    "solid",
                    fgColor="E8F0FE" if row["kind"] == "subtotal" else "D9EAD3",
                )
    sheet.append([data.get("model_notice", ""), "", None, None])
    sheet.append(["Без ставки налога результат показан до налога с дохода", "", None, None])
    sheet["A1"].font = Font(bold=True, size=16)
    sheet.column_dimensions["A"].width = 58
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["C"].width = 18
    sheet.column_dimensions["D"].width = 14
    for cell in sheet["C"][1:]:
        cell.number_format = '#,##0.00" ₽"'
    for cell in sheet["D"][1:]:
        cell.number_format = "0.0%"
    sheet.freeze_panes = "A2"

    monthly = workbook.create_sheet("По месяцам")
    monthly.append(
        [
            "Месяц", "Выручка", "Себестоимость", "Валовая прибыль",
            "Расходы площадки", "Расходы продавца", "Доп. затраты", "Налоги",
            "Результат модели", "Маржа",
        ]
    )
    for row in data["months"]:
        margin = row.get("margin_pct")
        monthly.append(
            [
                row["month"], row.get("revenue"), row.get("cogs"),
                row.get("gross_profit"), row.get("ozon_costs"),
                row.get("seller_costs"), row.get("manual_expenses", 0),
                row.get("taxes"), row.get("management_result"),
                margin / 100 if margin is not None else None,
            ]
        )

    products = workbook.create_sheet("По товарам")
    products.append(
        [
            "SKU", "Артикул", "Товар", "Категория", "Продано", "Выручка",
            "Себестоимость", "Валовая прибыль", "Комиссия", "Логистика",
            "Прочие площадки", "Расходы продавца", "Налоги", "Вклад до общих затрат",
            "Маржа", "Источник себестоимости",
        ]
    )
    for row in data["products"]:
        margin = row.get("margin_pct")
        products.append(
            [
                row.get("sku"), row.get("article"), row.get("full_name"),
                row.get("category"), row.get("units"), row.get("seller_revenue"),
                row.get("cogs"), row.get("gross_profit"), row.get("commission"),
                row.get("delivery"), row.get("other_ozon"), row.get("seller_costs"),
                (row["income_tax"] + (row.get("vat") or 0)) if row.get("income_tax") is not None else None,
                row.get("management_result"),
                margin / 100 if margin is not None else None,
                "Факт" if row.get("cogs_known") else "Цена / 3",
            ]
        )

    for target in (monthly, products):
        target.freeze_panes = "A2"
        target.auto_filter.ref = target.dimensions
        for cell in target[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="111827")
            cell.alignment = Alignment(wrap_text=True)
        for column in range(1, target.max_column + 1):
            target.column_dimensions[get_column_letter(column)].width = min(
                42,
                max(
                    12,
                    max(
                        len(str(target.cell(row, column).value or ""))
                        for row in range(1, target.max_row + 1)
                    )
                    + 2,
                ),
            )
    for row in monthly.iter_rows(min_row=2, min_col=2, max_col=9):
        for cell in row:
            cell.number_format = '#,##0.00" ₽"'
    for cell in monthly["J"][1:]:
        cell.number_format = "0.0%"
    for row in products.iter_rows(min_row=2, min_col=6, max_col=14):
        for cell in row:
            cell.number_format = '#,##0.00" ₽"'
    for cell in products["O"][1:]:
        cell.number_format = "0.0%"

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def save_pl_expenses(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from pulse_financial_model import ensure_expense_scope
    with connect_km(config) as scope_conn:
        ensure_expense_scope(scope_conn)
        scope_conn.commit()
    date_from = date.fromisoformat(str(payload.get("date_from") or ""))
    date_to = date.fromisoformat(str(payload.get("date_to") or ""))
    marketplace = str(payload.get("marketplace") or "ozon")
    if marketplace not in {"ozon", "wb", "yandex"}:
        raise ValueError("Неизвестная площадка расходов")
    if date_from > date_to:
        raise ValueError("Дата начала позже даты окончания")
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("Ожидается массив не более 200 расходов")
    prepared = []
    for row in rows:
        name = str(row.get("expense_name") or "").strip()
        amount = decimal_value(row.get("amount"))
        if not name or amount == 0:
            continue
        prepared.append(
            (
                date_from,
                date_to,
                name[:300],
                amount,
                str(row.get("allocation_method") or "period")[:50],
                str(row.get("notes") or "").strip()[:1000] or None,
                marketplace,
            )
        )
    with connect_km(config) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM public.ozon_pl_expenses
                    WHERE date_from = %s AND date_to = %s AND marketplace = %s
                    """,
                    (date_from, date_to, marketplace),
                )
                if prepared:
                    execute_values(
                        cur,
                        """
                        INSERT INTO public.ozon_pl_expenses (
                            date_from, date_to, expense_name, amount,
                            allocation_method, notes, marketplace
                        ) VALUES %s
                        """,
                        prepared,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"ok": True, "saved_rows": len(prepared)}


def pl_payload(config, raw_from=None, raw_to=None, client_key="km_trade", marketplace="ozon"):
    from pulse_financial_model import enrich_pl
    return enrich_pl(config, _pl_payload_base(config, raw_from, raw_to, client_key, marketplace))
