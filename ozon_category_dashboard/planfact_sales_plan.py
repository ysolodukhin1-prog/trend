"""Read revenue targets from Sales Planning; preserve Plan/Fact actuals/budgets."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode


def ratio(value, plan):
    return round(float(value) / plan * 100, 2) if value is not None and plan else None


def effective_plan(row, anchor):
    approved = row.get("approved_plan_revenue")
    if approved is not None and (approved > 0 or row.get("approved_version_id")):
        return float(approved), "approved"
    month = row["month_start"]
    key = "calculated_plan_revenue" if month == anchor else "forecast_revenue" if month > anchor else None
    value = row.get(key) if key else None
    return (float(value), "calculated" if month == anchor else "forecast") if value is not None else (None, "missing")


def strict_sum(values):
    values = list(values)
    return round(sum(values), 2) if values and all(v is not None for v in values) else None


def synchronize(payload, kind, raw_query, snapshots):
    """All inputs are request-local; snapshots cover whole portfolios, never pages."""
    params = parse_qs(raw_query)
    selected = (params.get("marketplace") or [""])[0]
    lookups = {
        market: {r["month_start"]: effective_plan(r, snap["period"]["anchor_month"])
                 for r in snap.get("months", [])}
        for market, snap in snapshots.items()
    }
    rows = [dict(r) for r in payload.get("rows", [])] if kind != "summary" else [dict(payload)]
    if kind == "monthly" and selected == "total":
        grouped = {}
        for row in rows:
            grouped.setdefault(row["plan_month"], []).append(row)
        rows = []
        for month, items in grouped.items():
            total = {"marketplace": "total", "marketplace_label": "Итого", "plan_month": month}
            for key in ("sales_rub", "orders_rub", "ad_spend_plan_rub", "ad_spend_rub"):
                total[key] = strict_sum(r.get(key) for r in items)
            total["ad_spend_budget_used_pct"] = ratio(total["ad_spend_rub"], total["ad_spend_plan_rub"])
            total["tacos_fact_pct"] = ratio(total["ad_spend_rub"], total["sales_rub"])
            total["days_with_fact"] = max((r.get("days_with_fact") or 0 for r in items), default=0)
            rows.append(total)
    start = (params.get("date_from") or [None])[0]
    end = (params.get("date_to") or [None])[0]
    if not end:
        end = max((str(r.get("date_to") or r.get("report_date") or r.get("plan_month")) for r in rows if r.get("date_to") or r.get("report_date") or r.get("plan_month")), default=None)
    if not start:
        start = next((r.get("plan_month") or r.get("date_from") for r in rows if r.get("plan_month") or r.get("date_from")), None)
    if not start:
        start = max(s["period"]["anchor_month"] for s in snapshots.values())
    first = date.fromisoformat(start[:10]).replace(day=1)
    last = date.fromisoformat((end or start)[:10]).replace(day=1)
    months = []
    cursor = first
    while cursor <= last:
        months.append(cursor.isoformat())
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    def target(market, month):
        markets = list(snapshots) if market in ("", "total", None) else [market]
        items = [lookups.get(m, {}).get(month, (None, "missing")) for m in markets]
        return strict_sum(i[0] for i in items), "+".join(sorted({i[1] for i in items}))

    # Future months also have a target, even when the fact view has no rows yet.
    row_markets = ["total"] if selected == "total" else [selected] if selected else list(snapshots)
    if kind in ("scorecard", "monthly", "daily"):
        if kind == "scorecard":
            rows = [r for r in rows if r.get("plan_month") == months[-1]]
        wanted = months[-1:] if kind == "scorecard" else months
        for month in wanted:
            for market in row_markets:
                if kind == "daily":
                    day = date.fromisoformat(month)
                    for n in range(calendar.monthrange(day.year, day.month)[1]):
                        report_date = (day + timedelta(days=n)).isoformat()
                        if (start and report_date < start) or (end and report_date > end):
                            continue
                        if not any(r.get("report_date") == report_date and r.get("marketplace") == market for r in rows):
                            rows.append({"marketplace": market, "plan_month": month, "report_date": report_date})
                elif not any(r.get("plan_month") == month and r.get("marketplace") == market for r in rows):
                    rows.append({"marketplace": market, "plan_month": month})
    for row in rows:
        month = str(row.get("plan_month") or row.get("report_date") or months[-1])[:7] + "-01"
        market = row.get("marketplace", selected)
        plan, source = target(market, month)
        if kind == "summary":
            plan = strict_sum(target(selected, m)[0] for m in months)
            source = "+".join(sorted({target(selected, m)[1] for m in months}))
        order_markets = [m for m in snapshots if snapshots[m].get('metric_contract', {}).get('primary') == 'orders']
        relevant = list(snapshots) if market in ('', 'total', None) else [market]
        if order_markets and any(m in order_markets for m in relevant):
            selected_months = months if kind == 'summary' else [month]
            order_rows = [next((r for r in snapshots.get(m, {}).get('months', []) if r['month_start'] == mo), {}) for m in relevant for mo in selected_months]
            orders_plan = strict_sum(r.get('orders_plan_rub') for r in order_rows)
            snapshot_orders_fact = strict_sum(r.get('actual_revenue') for r in order_rows) if kind in ('scorecard', 'monthly') else None
            # The Plan/Fact payload is the reconciled Seller Analytics control.
            # Keep it authoritative instead of replacing it with the product-funnel
            # snapshot, whose portfolio total can omit orders without SKU detail.
            payload_orders_fact = row.get('orders_rub') if kind in ('scorecard', 'monthly') else None
            orders_fact = float(payload_orders_fact) if payload_orders_fact is not None else snapshot_orders_fact
            orders_runrate = None
            if kind == 'scorecard' and len(relevant) == 1:
                anchor = snapshots[relevant[0]]['period']['anchor_month']
                if month <= anchor and order_rows:
                    orders_runrate = order_rows[0].get('forecast_revenue') if month == anchor else orders_fact
            row.update(orders_fact_rub=orders_fact, orders_fact_source='WB Seller Analytics',
                       orders_runrate_rub=orders_runrate, orders_runrate_pct=ratio(orders_runrate, orders_plan),
                       orders_plan_rub=orders_plan, orders_plan_units=strict_sum(r.get('orders_plan_units') for r in order_rows),
                       orders_plan_source='sales_planning_approved',
                       orders_plan_version_ids=sorted({v for r in order_rows for v in r.get('version_ids', [])}),
                       orders_plan_fact_pct=ratio(orders_fact, orders_plan))
            if kind == 'daily':
                value = row.get('orders_rub')
                row['orders_fact_daily_rub'] = float(value) if value is not None else None
            # No buyout/revenue plan is inferred from an orders target.
            plan, source = None, 'buyout_plan_missing'
        row.update(sales_plan_rub=plan, sales_plan_source="sales_planning", sales_plan_kind=source)
        for numerator, output in (("sales_rub", "sales_plan_fact_pct"), ("sales_runrate_rub", "sales_runrate_pct"), ("sales_recent_runrate_rub", "sales_recent_runrate_pct"), ("ad_spend_plan_rub", "tacos_plan_pct")):
            if kind != "daily":
                row[output] = ratio(row.get(numerator), plan)
        if kind == "daily":
            day = date.fromisoformat(str(row["report_date"])[:10])
            daily = plan / calendar.monthrange(day.year, day.month)[1] if plan is not None else None
            elapsed = daily * day.day if daily is not None else None
            row.update(sales_plan_daily_rub=daily, sales_plan_elapsed_rub=elapsed,
                       sales_month_plan_fact_pct=ratio(row.get("sales_cum_rub"), plan),
                       sales_elapsed_plan_fact_pct=ratio(row.get("sales_cum_rub"), elapsed))
    if kind == "summary":
        return rows[0]
    rows.sort(key=lambda r: (str(r.get("report_date") or r.get("plan_month")), {"wb": 0, "ozon": 1, "total": 2}.get(r.get("marketplace"), 3)))
    return {**payload, "rows": rows}


def apply_sales_planning(payload, kind, raw_query, client, config, marketplaces):
    from km_trade_sales_planning import sales_forecast_payload

    selected = (parse_qs(raw_query).get("marketplace") or [""])[0]
    markets = [m for m in marketplaces if m in ("wb", "ozon") and (selected in ("", "total") or selected == m)]
    if not markets:
        return payload
    # Do not cache an approved target: the next request must see plan changes.
    snapshots = {m: sales_forecast_payload(config, urlencode({"client": client, "marketplace": m, "horizon": "rolling"})) for m in markets}
    result = synchronize(payload, kind, raw_query, snapshots)
    if selected == 'wb' and snapshots.get('wb', {}).get('metric_contract', {}).get('primary') == 'orders':
        snapshot = snapshots['wb']
        rows = result.get('rows', []) if kind != 'summary' else [result]
        media_months = {}
        if kind == 'scorecard':
            from km_trade_media_plan import media_plan_payload
            media = media_plan_payload(config, client, 'wb')
            media_months = {m['month_start'][:7]: m for m in media.get('months', [])}
        for row in rows:
            row['orders_available_to'] = snapshot['period']['available_to']
            row['orders_available_from'] = snapshot['period']['available_from']
            if kind == 'scorecard':
                media_month = media_months.get(str(row.get('plan_month', ''))[:7], {})
                row['ad_budget_target_rub'] = media_month.get('plan', {}).get('ad_expense_rub')
                row['ad_budget_target_source'] = 'Расчёт медиаплана по сохранённым параметрам'
            month = str(row.get('plan_month') or row.get('report_date') or '')[:7]
            scenario = next((v for v in snapshot.get('plan_versions', [])
                             if v.get('assumptions', {}).get('snapshot', {}).get('metric') == 'orders'
                             and v['assumptions']['snapshot'].get('scope') == 'portfolio'
                             and any(m['month_start'][:7] == month for m in v.get('months', []))), None)
            if scenario:
                planned = next((m for m in scenario['months'] if m['month_start'][:7] == month), None)
                if planned:
                    row['orders_scenario_plan_rub'] = planned['plan_revenue']
                    row['orders_scenario_name'] = scenario['name']
                    row['orders_scenario_status'] = scenario['status']
                    row['orders_scenario_version_id'] = scenario['version_id']
    return result
