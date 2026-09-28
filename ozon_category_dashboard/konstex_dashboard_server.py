#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime extension that adds the isolated Konstex source to the shared BI."""

from __future__ import annotations

from urllib.parse import parse_qs

import app


KONSTEX_IMPORTS = {
    "konstex_wb_daily": {
        "report": "Konstex: ежедневные WB воронка и остатки",
        "description": "Read-only API выгрузка за предыдущий день, затем импорт в БД Konstex и обновление матвьюх.",
        "policy": "Токен берётся только из отдельной переменной WB_API_TOKEN_KONSTEX и не сохраняется в лог.",
        "script": app.PROJECT_ROOT / "scripts" / "download_konstex_wb_daily.py",
        "source": r"D:\Codex\Clients\Konstex\Аналитика\Дашборды\Data\WB",
        "destination": "konstex.public.konstex_wb_*; mv_wb_funnel_*; mv_wb_finance_*",
    },
    "konstex_inventory_history": {
        "report": "Konstex: история запасов WB",
        "description": "Нормализует все датированные WB stock API JSON в дневную историю запасов и логистики.",
        "policy": "Upsert только в БД konstex; исходные JSON не изменяются.",
        "script": app.PROJECT_ROOT / "scripts" / "sync_inventory_history.py",
        "args": ["--client", "konstex"],
        "source": r"D:\Codex\Clients\Konstex\Аналитика\Дашборды\Data\WB\Stock",
        "destination": "konstex.public.inventory_history_daily",
    },
    "konstex_dashboard_views": {
        "report": "Konstex: витрины WB и финансы",
        "description": "Переимпортирует локальные JSON/XLSX Konstex и пересчитывает матвьюхи без обращения к WB API.",
        "policy": "Работает только с БД и папкой Konstex.",
        "daily_stage": "views",
        "script": app.PROJECT_ROOT / "scripts" / "run_konstex_wb_bootstrap.py",
        "source": r"D:\Codex\Clients\Konstex\Аналитика\Дашборды\Data\WB",
        "destination": "konstex.public.mv_wb_funnel_*; mv_wb_finance_*",
    },
}
KONSTEX_DAILY = ["konstex_wb_daily", "konstex_inventory_history", "konstex_dashboard_views"]


app.ADMIN_CLIENTS["konstex"] = {
    "label": "Konstex",
    "db_name": "konstex",
    "status": "active",
    "description": r"Отдельная БД и API-выгрузки: D:\Codex\Clients\Konstex\Аналитика\Дашборды\Data\WB",
    "show_in_dashboard": True,
    "reports": ["funnel", "inventoryHistory", "planfact"],
    "marketplaces": ["wb"],
}

_admin_specs = app.admin_import_specs_for_client
def admin_import_specs_for_client(client):
    if client == "konstex":
        return KONSTEX_IMPORTS, KONSTEX_DAILY
    return _admin_specs(client)
app.admin_import_specs_for_client = admin_import_specs_for_client

_mapping_join = app.mapping_join_for_report
def mapping_join_for_report(report, marketplace=None):
    if app.current_client_key() == "konstex":
        return app.sql.SQL("")
    return _mapping_join(report, marketplace)
app.mapping_join_for_report = mapping_join_for_report

def finance_rows(parsed):
    params = parse_qs(parsed.query)
    date_from = params.get("date_from", [""])[0]
    date_to = params.get("date_to", [""])[0]
    clauses, values = [], []
    if date_from:
        clauses.append("report_date >= %s"); values.append(date_from)
    if date_to:
        clauses.append("report_date <= %s"); values.append(date_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with app.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT report_date, sales_rub, payout_rub, expense_rub AS ad_spend_rub, profit_rub AS profit_rub, quantity AS orders_qty, sales_rub AS orders_rub FROM public.mv_wb_finance_daily" + where + " ORDER BY report_date", values)
        return app.normalize_rows(cur.fetchall())

def handle_planfact_daily(parsed):
    return {"rows": finance_rows(parsed), "report_kind": "konstex_finance"}

def handle_planfact_summary(parsed):
    rows = finance_rows(parsed)
    return {"sales_rub": sum(app.to_float(row.get("sales_rub")) for row in rows), "ad_spend_rub": sum(app.to_float(row.get("ad_spend_rub")) for row in rows), "profit_rub": sum(app.to_float(row.get("profit_rub")) for row in rows), "orders_qty": sum(app.to_float(row.get("orders_qty")) for row in rows)}

def handle_planfact_monthly(parsed):
    return {"rows": finance_rows(parsed)}

def handle_planfact_scorecard(parsed):
    summary = handle_planfact_summary(parsed)
    return {"rows": [{"report_kind": "konstex_finance", "sales_rub": summary["sales_rub"], "orders_rub": summary["sales_rub"], "ad_spend_rub": summary["ad_spend_rub"], "profit_rub": summary["profit_rub"], "sales_plan_rub": 0, "ad_spend_plan_rub": 0, "last_day": "", "recent_start_day": ""}]}

app.handle_planfact_daily = handle_planfact_daily
app.handle_planfact_summary = handle_planfact_summary
app.handle_planfact_monthly = handle_planfact_monthly
app.handle_planfact_scorecard = handle_planfact_scorecard

if __name__ == "__main__":
    from run_dashboard_with_konstex import main as run_full_dashboard

    run_full_dashboard()