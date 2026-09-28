"""Source-backed planned P&L for KM Trade.

The sales volume and revenue come from Sales Planning. Cost and profitability
components are recalculated per SKU from the current Unit Economics model.
Missing model inputs stay explicit and never become zero-cost profit.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from km_trade_finance import decimal_value, number, unit_payload, unit_scenario
from km_trade_sales_planning import sales_forecast_payload


COMPONENT_KEYS = (
    "commission",
    "acquiring",
    "advertising",
    "forward_logistics",
    "reverse_logistics",
    "cogs",
    "seller_costs",
    "tax",
    "vat",
    "capital",
)


def _positive(value: Any) -> float | None:
    numeric = number(decimal_value(value, nullable=True))
    return numeric if numeric is not None and numeric > 0 else None


def _effective_product_plan(
    row: dict[str, Any],
    *,
    current_month: bool,
) -> tuple[float, float, str]:
    approved_units = number(decimal_value(row.get("approved_plan_units"), nullable=True))
    approved_revenue = number(decimal_value(row.get("approved_plan_revenue"), nullable=True))
    if approved_units is not None and approved_revenue is not None and approved_units >= 0 and approved_revenue >= 0:
        return approved_units, approved_revenue, "approved_plan"
    if current_month:
        calculated_units = _positive(row.get("calculated_plan_units"))
        calculated_revenue = _positive(row.get("calculated_plan_revenue"))
        if calculated_units is not None and calculated_revenue is not None:
            return calculated_units, calculated_revenue, "calculated_plan"
    return (
        max(number(decimal_value(row.get("forecast_units"))) or 0.0, 0.0),
        max(number(decimal_value(row.get("forecast_revenue"))) or 0.0, 0.0),
        "forecast_plan",
    )


def _unit_model_missing_inputs(row: dict[str, Any] | None) -> list[str]:
    if not row:
        return ["SKU отсутствует в юнит-экономике"]
    fields = (
        ("commission_pct", "комиссия"),
        ("model_acquiring_pct", "эквайринг"),
        ("tax_pct", "налог"),
        ("advertising_pct", "ДРР"),
        ("return_rate_pct_effective", "невыкуп/возвраты"),
        ("planned_forward_logistics_per_unit", "прямая логистика"),
        ("expected_reverse_logistics_per_unit", "обратная логистика"),
        ("cogs_per_unit", "себестоимость"),
        ("capital_cost_per_unit", "стоимость капитала"),
    )
    return [label for key, label in fields if (row.get("effective_cogs_per_unit") if key == "cogs_per_unit" and row.get(key) is None else row.get(key)) is None]


def _scenario_for_plan(
    row: dict[str, Any],
    price: Decimal,
) -> dict[str, float | None]:
    seller_costs = sum(
        (
            decimal_value(row.get("fulfillment_per_unit")),
            decimal_value(row.get("inbound_per_unit")),
            decimal_value(row.get("other_per_unit")),
        ),
        Decimal("0"),
    )
    return unit_scenario(
        price=price,
        commission_pct=decimal_value(row.get("commission_pct"), nullable=True),
        acquiring_pct=decimal_value(row.get("model_acquiring_pct"), nullable=True),
        tax_pct=decimal_value(row.get("tax_pct"), nullable=True),
        advertising_pct=decimal_value(row.get("advertising_pct"), nullable=True),
        forward_logistics=decimal_value(
            row.get("planned_forward_logistics_per_unit"), nullable=True
        ),
        reverse_logistics=decimal_value(
            row.get("expected_reverse_logistics_per_unit"), nullable=True
        ),
        cogs=decimal_value(row.get("cogs_per_unit") if row.get("cogs_per_unit") is not None else row.get("effective_cogs_per_unit"), nullable=True),
        seller_costs=seller_costs,
        vat_pct=decimal_value(row.get("vat_pct_effective")),
        capital_cost=decimal_value(row.get("capital_cost_per_unit"), nullable=True),
        non_buyout_pct=decimal_value(
            row.get("return_rate_pct_effective"), nullable=True
        ),
    )


def required_sales_plan(
    *,
    model_margin_pct: Any,
    baseline_revenue: Any,
    baseline_units: Any,
    target_profit_rub: Any = 0,
    target_margin_pct: Any = 0,
    fixed_expenses_rub: Any = 0,
) -> dict[str, Any]:
    """Return the minimum sales plan satisfying profit and margin goals.

    The model assumes the current SKU mix and unit economics remain unchanged.
    Fixed expenses are the only non-scaling cost.
    """
    margin_pct = decimal_value(model_margin_pct, nullable=True)
    revenue = max(decimal_value(baseline_revenue), Decimal("0"))
    units = max(decimal_value(baseline_units), Decimal("0"))
    target_profit = max(decimal_value(target_profit_rub), Decimal("0"))
    target_margin = max(decimal_value(target_margin_pct), Decimal("0"))
    fixed_expenses = max(decimal_value(fixed_expenses_rub), Decimal("0"))
    if margin_pct is None:
        return {"status": "unavailable", "required_revenue": None, "required_units": None}
    margin_rate = margin_pct / Decimal("100")
    target_margin_rate = target_margin / Decimal("100")
    baseline_profit = revenue * margin_rate - fixed_expenses
    baseline_margin = baseline_profit / revenue * 100 if revenue else None
    if margin_rate <= 0:
        return {
            "status": "unreachable_negative_margin",
            "required_revenue": None,
            "required_units": None,
            "baseline_profit": number(baseline_profit),
            "baseline_margin_pct": number(baseline_margin),
        }
    if target_margin_rate > margin_rate or (
        target_margin_rate == margin_rate and fixed_expenses > 0
    ):
        return {
            "status": "unreachable_margin",
            "required_revenue": None,
            "required_units": None,
            "baseline_profit": number(baseline_profit),
            "baseline_margin_pct": number(baseline_margin),
        }
    revenue_for_profit = (target_profit + fixed_expenses) / margin_rate
    revenue_for_margin = (
        fixed_expenses / (margin_rate - target_margin_rate)
        if fixed_expenses > 0 and target_margin_rate > 0
        else Decimal("0")
    )
    required_revenue = max(revenue_for_profit, revenue_for_margin)
    scale = required_revenue / revenue if revenue > 0 else None
    required_units = units * scale if scale is not None else None
    return {
        "status": "ok",
        "required_revenue": number(required_revenue),
        "required_units": number(required_units),
        "revenue_delta": number(required_revenue - revenue),
        "scale": number(scale),
        "baseline_profit": number(baseline_profit),
        "baseline_margin_pct": number(baseline_margin),
        "target_profit_rub": number(target_profit),
        "target_margin_pct": number(target_margin),
        "fixed_expenses_rub": number(fixed_expenses),
    }


def planned_pl_payload(
    config: dict[str, Any],
    raw_from: str | None = None,
    raw_to: str | None = None,
    client_key: str = "km_trade",
) -> dict[str, Any]:
    sales = sales_forecast_payload(config, f"client={client_key}&marketplace=ozon", include_all_products=True)
    units = unit_payload(config, raw_from, raw_to, client_key)
    unit_by_sku = {str(row.get("sku") or ""): row for row in units.get("rows") or []}
    anchor_month = str(sales.get("period", {}).get("anchor_month") or "")
    year_end = str(sales.get("period", {}).get("year_end") or "")
    month_keys = [
        str(row.get("month_start") or "")
        for row in sales.get("months") or []
        if anchor_month <= str(row.get("month_start") or "") <= year_end
    ]

    def new_bucket(month_key: str) -> dict[str, Any]:
        return {
            "month": month_key,
            "revenue": Decimal("0"),
            "units": Decimal("0"),
            "modelled_revenue": Decimal("0"),
            "modelled_units": Decimal("0"),
            "known_profit": Decimal("0"),
            "plan_skus": set(),
            "modelled_skus": set(),
            "missing_skus": set(),
            **{key: Decimal("0") for key in COMPONENT_KEYS},
        }

    monthly = {key: new_bucket(key) for key in month_keys}
    product_totals: dict[str, dict[str, Any]] = {}
    for product in sales.get("products") or []:
        sku = str(product.get("sku") or "")
        unit_row = unit_by_sku.get(sku)
        product_total = {
            "sku": sku,
            "article": product.get("article") or "",
            "product_name": product.get("product_name") or "",
            "category_name": product.get("category_name") or "Без категории",
            "revenue": Decimal("0"),
            "units": Decimal("0"),
            "modelled_revenue": Decimal("0"),
            "modelled_units": Decimal("0"),
            "known_profit": Decimal("0"),
            "missing_inputs": set(),
            **{key: Decimal("0") for key in COMPONENT_KEYS},
        }
        for row in product.get("monthly") or []:
            month_key = str(row.get("month_start") or "")
            if month_key not in monthly:
                continue
            plan_units, plan_revenue, plan_source = _effective_product_plan(
                row,
                current_month=month_key == anchor_month,
            )
            if plan_units <= 0 and plan_revenue <= 0:
                continue
            bucket = monthly[month_key]
            units_dec = decimal_value(plan_units)
            revenue_dec = decimal_value(plan_revenue)
            bucket["units"] += units_dec
            bucket["revenue"] += revenue_dec
            bucket["plan_skus"].add(sku)
            product_total["units"] += units_dec
            product_total["revenue"] += revenue_dec
            missing_inputs = _unit_model_missing_inputs(unit_row)
            if plan_units <= 0 or plan_revenue <= 0:
                missing_inputs.append("несогласованные плановые ₽/шт")
            if missing_inputs:
                bucket["missing_skus"].add(sku)
                product_total["missing_inputs"].update(missing_inputs)
                continue
            scenario = _scenario_for_plan(
                unit_row,
                revenue_dec / units_dec,
            )
            if scenario.get("profit") is None:
                bucket["missing_skus"].add(sku)
                product_total["missing_inputs"].add("неполная модель")
                continue
            bucket["modelled_revenue"] += revenue_dec
            bucket["modelled_units"] += units_dec
            bucket["modelled_skus"].add(sku)
            product_total["modelled_revenue"] += revenue_dec
            product_total["modelled_units"] += units_dec
            for key in COMPONENT_KEYS:
                component = decimal_value(scenario.get(key), nullable=True)
                if component is not None:
                    amount = component * units_dec
                    bucket[key] += amount
                    product_total[key] += amount
            profit = decimal_value(scenario.get("profit")) * units_dec
            bucket["known_profit"] += profit
            product_total["known_profit"] += profit
            product_total.setdefault("plan_sources", set()).add(plan_source)
        if product_total["revenue"] > 0 or product_total["units"] > 0:
            product_totals[sku] = product_total

    month_rows: list[dict[str, Any]] = []
    for month_key in month_keys:
        bucket = monthly[month_key]
        revenue = bucket["revenue"]
        modelled_revenue = bucket["modelled_revenue"]
        plan_units = bucket["units"]
        modelled_units = bucket["modelled_units"]
        complete = (
            (revenue == 0 or modelled_revenue >= revenue - Decimal("0.01"))
            and (plan_units == 0 or modelled_units >= plan_units - Decimal("0.01"))
        )
        known_costs = sum((bucket[key] for key in COMPONENT_KEYS), Decimal("0"))
        known_profit = bucket["known_profit"]
        row = {
            "month": month_key,
            "revenue": number(revenue),
            "units": number(plan_units),
            "modelled_revenue": number(modelled_revenue),
            "modelled_units": number(modelled_units),
            "model_coverage_revenue_pct": number(
                modelled_revenue / revenue * 100 if revenue else Decimal("100")
            ),
            "model_coverage_units_pct": number(
                modelled_units / plan_units * 100 if plan_units else Decimal("100")
            ),
            "plan_sku_count": len(bucket["plan_skus"]),
            "modelled_sku_count": len(bucket["modelled_skus"]),
            "missing_sku_count": len(bucket["missing_skus"]),
            "known_costs": number(known_costs),
            "known_profit": number(known_profit),
            "known_margin_pct": number(
                known_profit / modelled_revenue * 100 if modelled_revenue else None
            ),
            "net_profit": number(known_profit) if complete else None,
            "margin_pct": number(
                known_profit / revenue * 100 if complete and revenue else None
            ),
            "model_complete": complete,
            **{key: number(bucket[key]) for key in COMPONENT_KEYS},
        }
        row["ozon_costs"] = number(
            bucket["commission"]
            + bucket["acquiring"]
            + bucket["advertising"]
            + bucket["forward_logistics"]
            + bucket["reverse_logistics"]
        )
        row["taxes"] = number(bucket["tax"] + bucket["vat"])
        row["gross_profit"] = number(
            modelled_revenue - bucket["cogs"] if modelled_revenue else None
        )
        month_rows.append(row)

    total = new_bucket(anchor_month)
    for bucket in monthly.values():
        for key in (
            "revenue",
            "units",
            "modelled_revenue",
            "modelled_units",
            "known_profit",
            *COMPONENT_KEYS,
        ):
            total[key] += bucket[key]
        total["plan_skus"].update(bucket["plan_skus"])
        total["modelled_skus"].update(bucket["modelled_skus"])
        total["missing_skus"].update(bucket["missing_skus"])
    total_revenue = total["revenue"]
    total_units = total["units"]
    modelled_revenue = total["modelled_revenue"]
    modelled_units = total["modelled_units"]
    model_complete = (
        (total_revenue == 0 or modelled_revenue >= total_revenue - Decimal("0.01"))
        and (total_units == 0 or modelled_units >= total_units - Decimal("0.01"))
    )
    known_costs = sum((total[key] for key in COMPONENT_KEYS), Decimal("0"))
    known_profit = total["known_profit"]
    known_margin_pct = (
        known_profit / modelled_revenue * 100 if modelled_revenue else None
    )
    roi_pct = known_profit / known_costs * 100 if known_costs else None

    product_rows: list[dict[str, Any]] = []
    for item in product_totals.values():
        revenue = item["revenue"]
        modelled_revenue_product = item["modelled_revenue"]
        product_complete = (
            revenue == 0
            or modelled_revenue_product >= revenue - Decimal("0.01")
        )
        product_rows.append(
            {
                "sku": item["sku"],
                "article": item["article"],
                "product_name": item["product_name"],
                "category_name": item["category_name"],
                "revenue": number(revenue),
                "units": number(item["units"]),
                "modelled_revenue": number(modelled_revenue_product),
                "model_coverage_pct": number(
                    modelled_revenue_product / revenue * 100
                    if revenue
                    else Decimal("100")
                ),
                "known_profit": number(item["known_profit"]),
                "known_margin_pct": number(
                    item["known_profit"] / modelled_revenue_product * 100
                    if modelled_revenue_product
                    else None
                ),
                "net_profit": number(item["known_profit"]) if product_complete else None,
                "model_complete": product_complete,
                "missing_inputs": sorted(item["missing_inputs"]),
                **{key: number(item[key]) for key in COMPONENT_KEYS},
            }
        )
    product_rows.sort(key=lambda row: -float(row.get("revenue") or 0))

    statement = [
        {"key": "revenue", "label": "План продаж", "amount": number(total_revenue), "kind": "total"},
        {"key": "modelled_revenue", "label": "Выручка с полной юнит-моделью", "amount": number(modelled_revenue), "kind": "subtotal"},
        {"key": "cogs", "label": "Себестоимость", "amount": number(-total["cogs"])},
        {"key": "commission", "label": "Комиссия Ozon", "amount": number(-total["commission"])},
        {"key": "acquiring", "label": "Эквайринг Ozon", "amount": number(-total["acquiring"])},
        {"key": "logistics", "label": "Логистика и возвраты Ozon", "amount": number(-(total["forward_logistics"] + total["reverse_logistics"]))},
        {"key": "advertising", "label": "Реклама по целевому ДРР", "amount": number(-total["advertising"])},
        {"key": "seller_costs", "label": "Расходы продавца", "amount": number(-total["seller_costs"])},
        {"key": "taxes", "label": "Налог и НДС", "amount": number(-(total["tax"] + total["vat"]))},
        {"key": "capital", "label": "Стоимость капитала", "amount": number(-total["capital"])},
        {"key": "known_profit", "label": "Финрез на покрытой выручке", "amount": number(known_profit), "kind": "subtotal", "is_partial": not model_complete},
        {"key": "net_profit", "label": "Чистая прибыль плана", "amount": number(known_profit) if model_complete else None, "kind": "total"},
    ]
    for row in statement:
        amount = row.get("amount")
        row["revenue_pct"] = (
            number(decimal_value(amount) / total_revenue * 100)
            if amount is not None and total_revenue
            else None
        )

    rrc_target = units.get("global_settings", {}).get("rrc_margin_pct")
    goal_example = required_sales_plan(
        model_margin_pct=number(known_margin_pct),
        baseline_revenue=number(total_revenue),
        baseline_units=number(total_units),
        target_profit_rub=0,
        target_margin_pct=rrc_target or 0,
        fixed_expenses_rub=0,
    )
    return {
        "ok": True,
        "period": {
            "date_from": month_keys[0] if month_keys else anchor_month,
            "date_to": year_end,
            "anchor_month": anchor_month,
        },
        "totals": {
            "revenue": number(total_revenue),
            "units": number(total_units),
            "modelled_revenue": number(modelled_revenue),
            "modelled_units": number(modelled_units),
            "model_coverage_revenue_pct": number(
                modelled_revenue / total_revenue * 100
                if total_revenue
                else Decimal("100")
            ),
            "model_coverage_units_pct": number(
                modelled_units / total_units * 100
                if total_units
                else Decimal("100")
            ),
            "plan_sku_count": len(total["plan_skus"]),
            "modelled_sku_count": len(total["modelled_skus"]),
            "missing_sku_count": len(total["missing_skus"]),
            "known_costs": number(known_costs),
            "known_profit": number(known_profit),
            "known_margin_pct": number(known_margin_pct),
            "roi_pct": number(roi_pct),
            "net_profit": number(known_profit) if model_complete else None,
            "margin_pct": number(
                known_profit / total_revenue * 100
                if model_complete and total_revenue
                else None
            ),
            "model_complete": model_complete,
            "average_price": number(
                total_revenue / total_units if total_units else None
            ),
            **{key: number(total[key]) for key in COMPONENT_KEYS},
        },
        "statement": statement,
        "months": month_rows,
        "products": product_rows,
        "goal_defaults": {
            "target_profit_rub": 0.0,
            "target_margin_pct": number(decimal_value(rrc_target)),
            "fixed_expenses_rub": 0.0,
        },
        "goal_example": goal_example,
        "methodology": {
            "sales": (
                "Объём и выручка по SKU берутся из показанного плана продаж: "
                "утверждённый план, затем расчётный план текущего месяца, затем прогноз."
            ),
            "unit_economics": (
                "Каждый SKU пересчитывается по плановой цене месяца через текущую "
                "юнит-экономику: себестоимость, комиссия, эквайринг, логистика, ДРР "
                "с поправкой на выкуп, налоги, НДС и стоимость капитала."
            ),
            "coverage": (
                "Неполные SKU не получают нулевые расходы. Известный финрез показан "
                "только на покрытой выручке; чистая прибыль плана доступна при 100% покрытия."
            ),
            "goal": (
                "Требуемый план сохраняет текущий SKU-микс и юнит-экономику. "
                "Объём масштабирует переменные расходы; постоянные расходы задаются отдельно. "
                "Если маржа модели неположительна или ниже цели, рост продаж сам по себе "
                "не может выполнить цель."
            ),
        },
    }
