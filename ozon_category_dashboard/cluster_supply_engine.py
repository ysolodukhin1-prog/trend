"""Pure, source-agnostic cluster replenishment comparison.

No marketplace write, no API call, and no assumption that regional sales are
incremental. Callers must supply dated, verified SKU/cluster inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Optional


@dataclass(frozen=True)
class Demand:
    sku: str
    cluster: str
    observed_orders: Optional[float]
    observed_days: Optional[int]
    available_stock: Optional[float]
    confirmed_inbound: Optional[float]
    inbound_eta_days: Optional[int]
    plan_units: Optional[float]
    plan_days: Optional[int]
    buyout_probability: Optional[float]
    contribution_per_buyout: Optional[float]
    cogs_per_unit: Optional[float]
    inputs_verified: bool = False
    pack_size: int = 1
    review_days: int = 14
    safety_days: int = 7


@dataclass(frozen=True)
class Route:
    name: str
    lead_days: int
    freight_fixed: Optional[float]
    freight_per_unit: Optional[float]
    intake_per_unit: Optional[float]
    service_per_buyout: Optional[float]
    storage_per_unit_day: Optional[float]
    capital_annual_pct: Optional[float]
    uplift_orders: float = 0.0
    uplift_verified: bool = False
    capacity_units: Optional[int] = None
    available: bool = True
    costs_verified: bool = False


def _missing(d: Demand) -> list[str]:
    required = {
        'заказы по SKU и кластеру': d.observed_orders,
        'наблюдаемые дни': d.observed_days,
        'актуальный остаток': d.available_stock,
        'подтверждённые поступления': d.confirmed_inbound,
        'доля выкупа': d.buyout_probability,
        'вклад с выкупа': d.contribution_per_buyout,
        'себестоимость': d.cogs_per_unit,
    }
    missing = [label for label, value in required.items() if value is None]
    if d.plan_units is None or d.plan_days is None:
        missing.append('утверждённый план')
    if d.confirmed_inbound and d.inbound_eta_days is None:
        missing.append('дата подтверждённого поступления')
    if not d.inputs_verified:
        missing.append('полнота спроса, свежесть запаса и финансовых входов')
    return missing


def _valid_number(value: float, name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f'{name}: требуется неотрицательное конечное число')


def _round_pack(value: float, pack: int) -> int:
    return max(0, ceil(value / pack) * pack)


def compare(demand: Demand, routes: list[Route], baseline: str) -> dict:
    """Compare routes on one horizon. Result is a scenario, never booked profit.

    ``contribution_per_buyout`` is revenue less COGS and common marketplace
    costs. Route-specific services, own freight, intake, storage and capital
    are deducted below. ``uplift_orders`` is zero unless supplied explicitly.
    """
    if not demand.sku or not demand.cluster or demand.pack_size < 1 or demand.review_days < 1 or demand.safety_days < 0:
        raise ValueError('Некорректные SKU, кластер, кратность или горизонт')
    if not routes or len({r.name for r in routes}) != len(routes) or baseline not in {r.name for r in routes}:
        raise ValueError('Нужны уникальные маршруты и базовый маршрут')
    missing = _missing(demand)
    if missing:
        return {'status': 'blocked', 'missing': missing, 'sku': demand.sku, 'cluster': demand.cluster, 'routes': []}
    assert demand.observed_orders is not None and demand.observed_days is not None
    assert demand.available_stock is not None and demand.confirmed_inbound is not None
    assert demand.plan_units is not None and demand.plan_days is not None
    assert demand.buyout_probability is not None and demand.contribution_per_buyout is not None
    assert demand.cogs_per_unit is not None
    for name, value in [('заказы', demand.observed_orders), ('остаток', demand.available_stock),
                        ('поступления', demand.confirmed_inbound), ('план', demand.plan_units),
                        ('себестоимость', demand.cogs_per_unit)]:
        _valid_number(value, name)
    if not isfinite(demand.contribution_per_buyout):
        raise ValueError('Вклад с выкупа должен быть конечным числом')
    if demand.observed_days < 1 or demand.plan_days < 1 or not 0 <= demand.buyout_probability <= 1:
        raise ValueError('Некорректное окно наблюдения, плана или доля выкупа')
    if demand.inbound_eta_days is not None and demand.inbound_eta_days < 0:
        raise ValueError('Дата поступления не может быть в прошлом')

    daily_forecast = demand.observed_orders / demand.observed_days
    daily_plan = demand.plan_units / demand.plan_days
    comparison_days = max((r.lead_days for r in routes if r.available), default=0) + demand.review_days + demand.safety_days
    result = []
    for route in routes:
        required = {
            'срок маршрута': route.lead_days,
            'фрахт рейса': route.freight_fixed,
            'фрахт на единицу': route.freight_per_unit,
            'приёмка на единицу': route.intake_per_unit,
            'услуги на выкуп': route.service_per_buyout,
            'хранение на единицу в день': route.storage_per_unit_day,
            'стоимость капитала': route.capital_annual_pct,
        }
        route_missing = [key for key, value in required.items() if value is None]
        if not route.available:
            route_missing.append('маршрут недоступен')
        if not route.costs_verified:
            route_missing.append('тариф и собственная перевозка не подтверждены')
        if route_missing:
            result.append({'route': route.name, 'status': 'blocked', 'missing': route_missing})
            continue
        if route.lead_days < 0:
            raise ValueError('Срок маршрута не может быть отрицательным')
        for key, value in required.items():
            _valid_number(value, key)
        _valid_number(route.uplift_orders, 'дополнительный спрос')
        horizon = comparison_days
        inbound_before_horizon = demand.confirmed_inbound if demand.inbound_eta_days is not None and demand.inbound_eta_days <= horizon else 0
        forecast_need = _round_pack(daily_forecast * horizon - demand.available_stock - inbound_before_horizon, demand.pack_size)
        plan_need = _round_pack(daily_plan * horizon - demand.available_stock - inbound_before_horizon, demand.pack_size)
        chosen_qty = forecast_need
        if route.capacity_units is not None and (route.capacity_units < 0 or chosen_qty > route.capacity_units):
            result.append({'route': route.name, 'status': 'blocked', 'missing': ['вместимость маршрута'],
                           'forecast_qty': forecast_need, 'plan_qty': plan_need})
            continue
        # All routes face the same evaluation horizon. Stockout before arrival
        # loses demand; it is never silently recovered by a later shipment.
        available = (demand.available_stock
                     + (demand.confirmed_inbound if demand.inbound_eta_days == 0 else 0)
                     + (chosen_qty if route.lead_days == 0 else 0))
        expected_orders = 0.0
        daily_uplift = route.uplift_orders / max(1, comparison_days - route.lead_days)
        for day in range(1, comparison_days + 1):
            if demand.inbound_eta_days == day:
                available += demand.confirmed_inbound
            if route.lead_days == day:
                available += chosen_qty
            wanted = daily_forecast + (daily_uplift if day > route.lead_days else 0)
            sold = min(available, wanted)
            expected_orders += sold
            available -= sold
        expected_buyouts = expected_orders * demand.buyout_probability
        freight = (route.freight_fixed if chosen_qty > 0 else 0) + chosen_qty * route.freight_per_unit
        intake = chosen_qty * route.intake_per_unit
        holding_days = demand.review_days / 2 + demand.safety_days
        storage = chosen_qty * route.storage_per_unit_day * holding_days
        capital = chosen_qty * demand.cogs_per_unit * route.capital_annual_pct / 100 * (route.lead_days + holding_days) / 365
        contribution = expected_buyouts * (demand.contribution_per_buyout - route.service_per_buyout)
        result.append({'route': route.name, 'status': 'scenario' if route.uplift_orders and not route.uplift_verified else 'ready',
                       'forecast_qty': forecast_need, 'plan_qty': plan_need, 'daily_forecast': daily_forecast,
                       'daily_plan': daily_plan, 'expected_orders': expected_orders, 'expected_buyouts': expected_buyouts,
                       'contribution': contribution, 'freight': freight, 'intake': intake, 'storage': storage,
                       'capital': capital, 'result_before_tax': contribution - freight - intake - storage - capital,
                       'uplift_orders': route.uplift_orders, 'uplift_verified': route.uplift_verified})
    baseline_row = next(r for r in result if r['route'] == baseline)
    baseline_result = baseline_row.get('result_before_tax')
    for row in result:
        row['incremental_vs_baseline'] = (row['result_before_tax'] - baseline_result
                                          if baseline_result is not None and row.get('result_before_tax') is not None else None)
    ready = [r for r in result if r['status'] == 'ready']
    best = max(ready, key=lambda r: r['result_before_tax']) if ready else None
    return {'status': 'ready' if len(ready) == len(routes) else 'partial',
            'sku': demand.sku, 'cluster': demand.cluster, 'baseline': baseline,
            'best_route': best['route'] if best else None, 'routes': result}
