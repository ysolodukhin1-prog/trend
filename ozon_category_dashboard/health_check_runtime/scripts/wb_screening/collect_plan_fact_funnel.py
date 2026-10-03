#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect a compact, read-only source snapshot for the plan-fact workbook."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from scripts.wb_screening.build_daily_client_radar import (
    BI_PROJECT_DEFAULT,
    MOSCOW_TZ,
    SourceTask,
    analyze_source,
    as_date,
    load_bi,
    load_registry,
    query,
    safe_error,
)


def root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def shift_month(month: date, delta: int) -> date:
    absolute = month.year * 12 + month.month - 1 + delta
    return date(absolute // 12, absolute % 12 + 1, 1)


def calendar_days(start: date, end: date) -> Iterable[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def rows_by_date(rows: Iterable[Mapping[str, Any]]) -> dict[date, dict[str, Any]]:
    result: dict[date, dict[str, Any]] = {}
    for raw in rows:
        day = as_date(raw.get("report_date"))
        if day is None:
            continue
        result[day] = {str(key): json_value(value) for key, value in raw.items()}
    return result


def merge_daily_rows(
    start: date,
    end: date,
    funnel_rows: Iterable[Mapping[str, Any]],
    planfact_rows: Iterable[Mapping[str, Any]],
    inventory_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    funnel = rows_by_date(funnel_rows)
    planfact = rows_by_date(planfact_rows)
    inventory = rows_by_date(inventory_rows)
    merged: list[dict[str, Any]] = []
    for day in calendar_days(start, end):
        row: dict[str, Any] = {"report_date": day.isoformat()}
        row.update(funnel.get(day, {}))
        for key, value in planfact.get(day, {}).items():
            if key not in {"report_date", "marketplace", "marketplace_label"}:
                row[f"pf_{key}"] = value
        for key, value in inventory.get(day, {}).items():
            if key != "report_date":
                row[key] = value
        merged.append(row)
    return merged


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


OOS_COMPARISON_DAYS = 28
OOS_STRONG_DROP_PCT = -30.0
OOS_LOW_COVER_DAYS = 3.0


def summarize_oos_diagnostic(
    sales_rows: Iterable[Mapping[str, Any]],
    stock_rows: Iterable[Mapping[str, Any]],
    *,
    comparison_cutoff: date | None,
    current_observed_days: int,
    previous_observed_days: int,
    stock_snapshot_date: date | None,
) -> dict[str, Any]:
    """Evaluate OOS only where a sales loss and a low-stock fact coincide.

    Missing stock rows remain missing and are never converted to zero. The
    previous aligned window supplies the sales rate so current OOS cannot
    suppress its own demand baseline.
    """

    stock_by_sku = {
        str(row.get("sku") or ""): row
        for row in stock_rows
        if str(row.get("sku") or "")
    }
    candidates: list[dict[str, Any]] = []
    missing_stock = 0
    considered = 0
    evaluated = 0
    for raw in sales_rows:
        sku = str(raw.get("sku") or "")
        previous_units = _number(raw.get("previous_units"))
        current_units = _number(raw.get("current_units"))
        if not sku or previous_units is None or previous_units <= 0 or current_units is None:
            continue
        considered += 1
        change_pct = (current_units / previous_units - 1) * 100
        sales_lost = current_units <= 0 or change_pct <= OOS_STRONG_DROP_PCT
        stock = stock_by_sku.get(sku)
        stock_qty = _number((stock or {}).get("stock_available_qty"))
        if stock_qty is None:
            missing_stock += 1
            continue
        evaluated += 1
        average_daily_units = previous_units / max(previous_observed_days, 1)
        cover_days = stock_qty / average_daily_units if average_daily_units > 0 else None
        stock_limited = stock_qty <= 0 or (
            cover_days is not None and cover_days < OOS_LOW_COVER_DAYS
        )
        if not (sales_lost and stock_limited):
            continue
        previous_revenue = _number(raw.get("previous_revenue_rub"))
        current_revenue = _number(raw.get("current_revenue_rub"))
        candidates.append(
            {
                "sku": sku,
                "seller_article": raw.get("seller_article") or (stock or {}).get("seller_article") or "",
                "product_name": raw.get("product_name") or (stock or {}).get("product_name") or "Без названия",
                "category_name": raw.get("category_name") or (stock or {}).get("category_name") or "Без категории",
                "previous_units": round(previous_units, 2),
                "current_units": round(current_units, 2),
                "sales_change_pct": round(change_pct, 1),
                "average_daily_units": round(average_daily_units, 3),
                "stock_available_qty": round(stock_qty, 2),
                "stock_cover_days": round(cover_days, 1) if cover_days is not None else None,
                "estimated_lost_units": round(max(previous_units - current_units, 0), 2),
                "previous_revenue_rub": round(previous_revenue, 2) if previous_revenue is not None else None,
                "current_revenue_rub": round(current_revenue, 2) if current_revenue is not None else None,
                "estimated_lost_revenue_rub": (
                    round(max(previous_revenue - current_revenue, 0), 2)
                    if previous_revenue is not None and current_revenue is not None
                    else None
                ),
                "stock_condition": "zero" if stock_qty <= 0 else "below_3_days",
            }
        )

    candidates.sort(
        key=lambda row: (
            -(_number(row.get("estimated_lost_revenue_rub")) or 0),
            -(_number(row.get("estimated_lost_units")) or 0),
            str(row.get("sku") or ""),
        )
    )
    coverage_ok = current_observed_days >= 20 and previous_observed_days >= 20
    if comparison_cutoff is None or stock_snapshot_date is None:
        status = "evidence_gap"
        reason = "Нет синхронизированной даты продаж и последнего снимка остатков."
    elif not coverage_ok:
        status = "evidence_gap"
        reason = "Недостаточно наблюдаемых дней в одном из сопоставимых 28-дневных окон."
    elif candidates:
        status = "supported"
        reason = "Падение продаж совпало с нулевым остатком или покрытием менее трёх дней."
    elif evaluated:
        status = "refuted"
        reason = "Среди SKU с продажами в предыдущем периоде связка падения продаж и низкого остатка не найдена."
    else:
        status = "evidence_gap"
        reason = "Нет SKU, по которым одновременно доступны продажи и последний остаток."

    lost_revenues = [
        value
        for row in candidates
        if (value := _number(row.get("estimated_lost_revenue_rub"))) is not None
    ]
    return {
        "version": "1.0.0",
        "status": status,
        "reason": reason,
        "comparison_cutoff": comparison_cutoff.isoformat() if comparison_cutoff else None,
        "stock_snapshot_date": stock_snapshot_date.isoformat() if stock_snapshot_date else None,
        "stock_lag_days": (
            (comparison_cutoff - stock_snapshot_date).days
            if comparison_cutoff and stock_snapshot_date
            else None
        ),
        "current_period": {
            "from": (comparison_cutoff - timedelta(days=27)).isoformat() if comparison_cutoff else None,
            "to": comparison_cutoff.isoformat() if comparison_cutoff else None,
            "observed_days": current_observed_days,
        },
        "previous_period": {
            "from": (comparison_cutoff - timedelta(days=55)).isoformat() if comparison_cutoff else None,
            "to": (comparison_cutoff - timedelta(days=28)).isoformat() if comparison_cutoff else None,
            "observed_days": previous_observed_days,
        },
        "rule": {
            "previous_sales_required": True,
            "strong_sales_drop_pct": OOS_STRONG_DROP_PCT,
            "stock_zero_or_cover_below_days": OOS_LOW_COVER_DAYS,
            "sales_rate_basis": "previous_aligned_window_observed_day_average",
            "missing_stock_is_zero": False,
        },
        "considered_sku_count": considered,
        "evaluated_sku_count": evaluated,
        "missing_stock_sku_count": missing_stock,
        "affected_sku_count": len(candidates),
        "estimated_lost_units": round(sum(row["estimated_lost_units"] for row in candidates), 2),
        "estimated_lost_revenue_rub": round(sum(lost_revenues), 2) if lost_revenues else None,
        "affected_skus": candidates[:20],
        "partial": missing_stock > 0,
    }


def planfact_payload(
    app: Any,
    client: str,
    marketplace: str,
    handler_name: str,
    start: date,
    cutoff: date,
) -> Mapping[str, Any]:
    handler = getattr(app, handler_name, None)
    if not callable(handler):
        return {"rows": [], "reason": f"{handler_name} недоступен"}
    return app.review_source_payload(
        client,
        handler,
        "planfact",
        marketplace,
        {"date_from": start.isoformat(), "date_to": cutoff.isoformat()},
    )


def _oos_diagnostic_payload(
    cursor: Any,
    marketplace: str,
    cutoff: date,
    filters: Mapping[str, str],
) -> dict[str, Any]:
    funnel_view = {
        "wb": "mv_wb_funnel_daily_by_article_category",
        "ozon": "mv_ozon_funnel_daily_by_article_category",
    }[marketplace]
    cursor.execute("SELECT to_regclass(%s) AS table_name", (f"public.{funnel_view}",))
    exists = cursor.fetchone()
    if not exists or not exists.get("table_name"):
        return summarize_oos_diagnostic([], [], comparison_cutoff=None, current_observed_days=0, previous_observed_days=0, stock_snapshot_date=None)

    def scoped_clauses(alias: str, *, inventory: bool) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        category = filters.get("category")
        product = filters.get("product")
        article = filters.get("article")
        if category:
            clauses.append(f"{alias}.category_name = %s")
            values.append(category)
        if product:
            clauses.append(f"{alias}.product_name = %s")
            values.append(product)
        if article:
            clauses.append(
                f"({alias}.sku::text ILIKE %s OR {alias}.seller_article ILIKE %s OR {alias}.product_name ILIKE %s)"
            )
            pattern = f"%{article}%"
            values.extend([pattern, pattern, pattern])
        if inventory:
            clauses.insert(0, f"{alias}.marketplace = %s")
            values.insert(0, marketplace)
        return clauses, values

    inventory_scope, inventory_values = scoped_clauses("i", inventory=True)
    funnel_scope, funnel_values = scoped_clauses("v", inventory=False)
    funnel_where = " AND ".join(funnel_scope) if funnel_scope else "TRUE"

    stock_source = "inventory_history_daily.stock_available_qty"
    wb_stock_scope: list[str] = []
    wb_stock_values: list[Any] = []
    if marketplace == "wb":
        category = filters.get("category")
        product = filters.get("product")
        article = filters.get("article")
        if category:
            wb_stock_scope.append("r.category_name = %s")
            wb_stock_values.append(category)
        if product:
            wb_stock_scope.append("r.product_name = %s")
            wb_stock_values.append(product)
        if article:
            pattern = f"%{article}%"
            wb_stock_scope.append(
                "(r.wb_nmid::text ILIKE %s OR r.seller_article ILIKE %s OR r.product_name ILIKE %s)"
            )
            wb_stock_values.extend([pattern, pattern, pattern])
        wb_stock_where = " AND ".join(wb_stock_scope) if wb_stock_scope else "TRUE"
        cursor.execute("SELECT to_regclass('public.wb_funnel_daily') AS table_name")
        raw_stock_exists = cursor.fetchone()
        if raw_stock_exists and raw_stock_exists.get("table_name"):
            cursor.execute(
                f"""
                SELECT max(r.report_date) AS cutoff
                  FROM public.wb_funnel_daily r
                 WHERE ({wb_stock_where})
                   AND r.stock_wb_qty IS NOT NULL
                   AND r.report_date <= %s
                """,
                [*wb_stock_values, cutoff],
            )
            stock_snapshot_date = as_date((cursor.fetchone() or {}).get("cutoff"))
            stock_source = "wb_funnel_daily.stock_wb_qty"
        else:
            stock_snapshot_date = None
    else:
        cursor.execute(
            f"SELECT max(i.snapshot_date) AS cutoff FROM public.inventory_history_daily i WHERE {' AND '.join(inventory_scope)} AND i.snapshot_date <= %s",
            [*inventory_values, cutoff],
        )
        stock_snapshot_date = as_date((cursor.fetchone() or {}).get("cutoff"))

    cursor.execute(
        f"SELECT max(v.report_date) AS cutoff FROM public.{funnel_view} v WHERE {funnel_where} AND v.report_date <= %s",
        [*funnel_values, cutoff],
    )
    sales_cutoff = as_date((cursor.fetchone() or {}).get("cutoff"))
    comparison_cutoff = (
        min(stock_snapshot_date, sales_cutoff)
        if stock_snapshot_date is not None and sales_cutoff is not None
        else None
    )
    if comparison_cutoff is None:
        return summarize_oos_diagnostic([], [], comparison_cutoff=None, current_observed_days=0, previous_observed_days=0, stock_snapshot_date=stock_snapshot_date)

    current_from = comparison_cutoff - timedelta(days=27)
    previous_from = comparison_cutoff - timedelta(days=55)
    previous_to = comparison_cutoff - timedelta(days=28)
    cursor.execute(
        f"""
        SELECT
          COUNT(DISTINCT v.report_date) FILTER (WHERE v.report_date BETWEEN %s AND %s)::int AS current_days,
          COUNT(DISTINCT v.report_date) FILTER (WHERE v.report_date BETWEEN %s AND %s)::int AS previous_days
        FROM public.{funnel_view} v
        WHERE ({funnel_where}) AND v.report_date BETWEEN %s AND %s
        """,
        [current_from, comparison_cutoff, previous_from, previous_to, *funnel_values, previous_from, comparison_cutoff],
    )
    coverage = dict(cursor.fetchone() or {})
    cursor.execute(
        f"""
        SELECT
          v.sku::text AS sku,
          max(v.seller_article) AS seller_article,
          max(v.product_name) AS product_name,
          max(v.category_name) AS category_name,
          SUM(COALESCE(v.ordered_units, 0)) FILTER (WHERE v.report_date BETWEEN %s AND %s)::numeric AS current_units,
          SUM(COALESCE(v.ordered_units, 0)) FILTER (WHERE v.report_date BETWEEN %s AND %s)::numeric AS previous_units,
          SUM(COALESCE(v.ordered_amount_rub, 0)) FILTER (WHERE v.report_date BETWEEN %s AND %s)::numeric AS current_revenue_rub,
          SUM(COALESCE(v.ordered_amount_rub, 0)) FILTER (WHERE v.report_date BETWEEN %s AND %s)::numeric AS previous_revenue_rub
        FROM public.{funnel_view} v
        WHERE ({funnel_where})
          AND v.report_date BETWEEN %s AND %s
          AND NULLIF(v.sku::text, '') IS NOT NULL
        GROUP BY v.sku::text
        HAVING SUM(COALESCE(v.ordered_units, 0)) FILTER (WHERE v.report_date BETWEEN %s AND %s) > 0
        """,
        [
            current_from, comparison_cutoff,
            previous_from, previous_to,
            current_from, comparison_cutoff,
            previous_from, previous_to,
            *funnel_values,
            previous_from, comparison_cutoff,
            previous_from, previous_to,
        ],
    )
    sales_rows = [dict(row) for row in cursor.fetchall()]
    if marketplace == "wb" and stock_source == "wb_funnel_daily.stock_wb_qty":
        wb_stock_where = " AND ".join(wb_stock_scope) if wb_stock_scope else "TRUE"
        cursor.execute(
            f"""
            SELECT
              r.wb_nmid::text AS sku,
              max(r.seller_article) AS seller_article,
              max(r.product_name) AS product_name,
              max(r.category_name) AS category_name,
              SUM(r.stock_wb_qty)::numeric AS stock_available_qty
            FROM public.wb_funnel_daily r
            WHERE ({wb_stock_where})
              AND r.report_date = %s
              AND r.stock_wb_qty IS NOT NULL
              AND NULLIF(r.wb_nmid::text, '') IS NOT NULL
            GROUP BY r.wb_nmid::text
            """,
            [*wb_stock_values, stock_snapshot_date],
        )
    else:
        cursor.execute(
            f"""
            SELECT
              i.sku::text AS sku,
              max(i.seller_article) AS seller_article,
              max(i.product_name) AS product_name,
              max(i.category_name) AS category_name,
              SUM(i.stock_available_qty)::numeric AS stock_available_qty
            FROM public.inventory_history_daily i
            WHERE {' AND '.join(inventory_scope)}
              AND i.snapshot_date = %s
              AND NULLIF(i.sku::text, '') IS NOT NULL
            GROUP BY i.sku::text
            """,
            [*inventory_values, stock_snapshot_date],
        )
    stock_rows = [dict(row) for row in cursor.fetchall()]
    result = summarize_oos_diagnostic(
        sales_rows,
        stock_rows,
        comparison_cutoff=comparison_cutoff,
        current_observed_days=int(coverage.get("current_days") or 0),
        previous_observed_days=int(coverage.get("previous_days") or 0),
        stock_snapshot_date=stock_snapshot_date,
    )
    result["stock_lag_days"] = (cutoff - stock_snapshot_date).days if stock_snapshot_date else None
    result["stock_source"] = stock_source
    result["stock_source_label"] = (
        "Ежедневная воронка WB · остаток на маркетплейсе"
        if stock_source == "wb_funnel_daily.stock_wb_qty"
        else "Каноническая история остатков"
    )
    return result


def inventory_payload(
    app: Any,
    client: str,
    marketplace: str,
    start: date,
    cutoff: date,
    filters: Mapping[str, str] | None = None,
    *,
    include_oos_diagnostic: bool = True,
) -> Mapping[str, Any]:
    active_filters = {
        str(key): str(value).strip()
        for key, value in (filters or {}).items()
        if str(value).strip()
    }

    def handler(_parsed: Any) -> Mapping[str, Any]:
        with app.get_conn() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.inventory_history_daily') AS table_name")
            exists = cursor.fetchone()
            if not exists or not exists.get("table_name"):
                return {"rows": [], "reason": "inventory_history_daily отсутствует"}
            if not active_filters:
                cursor.execute(
                    "SELECT to_regclass('public.mv_pulse_home_inventory_daily_v1') AS table_name"
                )
                mart = cursor.fetchone()
                if mart and mart.get("table_name"):
                    cursor.execute(
                        """
                        SELECT
                          report_date,
                          stock_available_qty::numeric AS inventory_available_stock_qty,
                          stock_sku_count::numeric AS inventory_active_sku,
                          in_stock_sku_count::numeric AS inventory_in_stock_sku
                        FROM public.mv_pulse_home_inventory_daily_v1
                        WHERE marketplace = %s
                          AND report_date BETWEEN %s AND %s
                        ORDER BY report_date
                        """,
                        (marketplace, start, cutoff),
                    )
                    rows = [dict(row) for row in cursor.fetchall()]
                    if rows and not include_oos_diagnostic:
                        return {
                            "rows": rows,
                            "oos_diagnostic": {},
                            "source": "mv_pulse_home_inventory_daily_v1",
                        }
            clauses = [
                "marketplace = %s",
                "snapshot_date BETWEEN %s AND %s",
            ]
            values: list[Any] = [marketplace, start, cutoff]
            category = active_filters.get("category")
            product = active_filters.get("product")
            article = active_filters.get("article")
            if category:
                clauses.append("category_name = %s")
                values.append(category)
            if product:
                clauses.append("product_name = %s")
                values.append(product)
            if article:
                clauses.append(
                    "(sku::text ILIKE %s OR seller_article ILIKE %s "
                    "OR product_name ILIKE %s)"
                )
                pattern = f"%{article}%"
                values.extend([pattern, pattern, pattern])
            cursor.execute(
                f"""
                SELECT
                  snapshot_date AS report_date,
                  SUM(COALESCE(stock_available_qty, 0))::numeric
                    AS inventory_available_stock_qty,
                  COUNT(DISTINCT sku)::numeric AS inventory_active_sku,
                  COUNT(DISTINCT sku) FILTER (
                    WHERE COALESCE(stock_available_qty, 0) > 0
                  )::numeric AS inventory_in_stock_sku
                FROM public.inventory_history_daily
                WHERE {' AND '.join(clauses)}
                GROUP BY snapshot_date
                ORDER BY snapshot_date
                """,
                values,
            )
            rows = [dict(row) for row in cursor.fetchall()]
            # The SKU-level OOS diagnostic scans two aligned 28-day windows
            # and the latest stock snapshot. It is intentionally lazy: the
            # primary Health Check table only needs daily inventory totals,
            # while the hypothesis drill-down requests this evidence.
            oos_diagnostic = (
                _oos_diagnostic_payload(
                    cursor,
                    marketplace,
                    cutoff,
                    active_filters,
                )
                if include_oos_diagnostic
                else {}
            )
            return {"rows": rows, "oos_diagnostic": oos_diagnostic}

    return app.review_source_payload(
        client,
        handler,
        "inventory",
        marketplace,
        {
            "date_from": start.isoformat(),
            "date_to": cutoff.isoformat(),
            **active_filters,
        },
    )


def client_label(app: Any, client: str) -> str:
    config = app.ADMIN_CLIENTS.get(client) or {}
    return str(config.get("label") or client)


def current_plan(
    monthly_rows: Iterable[Mapping[str, Any]],
    marketplace: str,
    month: date,
) -> dict[str, Any]:
    target = month.isoformat()
    row = next(
        (
            item
            for item in monthly_rows
            if str(item.get("marketplace") or "").lower() == marketplace
            and str(item.get("plan_month") or "")[:10] == target
        ),
        None,
    )
    if row is None:
        return {}
    return {
        key: json_value(row.get(key))
        for key in (
            "sales_plan_rub",
            "ad_spend_plan_rub",
            "tacos_plan_pct",
            "sales_rub",
            "ad_spend_rub",
            "orders_rub",
            "days_with_fact",
        )
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_value) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    now = datetime.now(ZoneInfo(MOSCOW_TZ))
    requested_cutoff = (
        date.fromisoformat(args.cutoff)
        if args.cutoff
        else now.date() - timedelta(days=1)
    )
    current_month = requested_cutoff.replace(day=1)
    start = shift_month(current_month, -args.history_months)
    registry, registry_version = load_registry(Path(args.registry))
    app = load_bi(Path(args.bi_project))
    label = client_label(app, args.client)
    query_count = 4
    print(
        "ПЛАН: "
        f"клиент {label}, площадка {args.marketplace.upper()}, "
        f"окно {start}..{requested_cutoff}, запросов {query_count}; "
        "все PostgreSQL-соединения read-only.",
        flush=True,
    )

    task = SourceTask(
        "daily",
        args.client,
        label,
        "funnel",
        args.marketplace,
        start,
        requested_cutoff,
    )
    funnel_payload = query(app, task)
    source = analyze_source(task, funnel_payload, registry)
    analysis_date = as_date(source.get("analysis_date")) or requested_cutoff
    print(
        "ПРОГРЕСС: 1/4 (25%) | дневная воронка | "
        f"строк {len(funnel_payload.get('rows') or [])}, "
        f"последний полный день {analysis_date} | "
        f"прошло {time.monotonic() - started:.1f}с",
        flush=True,
    )

    optional_errors: list[str] = []
    try:
        daily_planfact = planfact_payload(
            app,
            args.client,
            args.marketplace,
            "handle_planfact_daily",
            start,
            analysis_date,
        )
    except Exception as exc:  # optional source
        optional_errors.append(f"planfact_daily: {safe_error(exc)}")
        daily_planfact = {"rows": []}
    print(
        "ПРОГРЕСС: 2/4 (50%) | дневной план-факт | "
        f"строк {len(daily_planfact.get('rows') or [])}, "
        f"ошибок {len(optional_errors)} | прошло {time.monotonic() - started:.1f}с",
        flush=True,
    )

    try:
        monthly_planfact = planfact_payload(
            app,
            args.client,
            args.marketplace,
            "handle_planfact_monthly",
            start,
            analysis_date,
        )
    except Exception as exc:  # optional source
        optional_errors.append(f"planfact_monthly: {safe_error(exc)}")
        monthly_planfact = {"rows": []}
    print(
        "ПРОГРЕСС: 3/4 (75%) | месячный план-факт | "
        f"строк {len(monthly_planfact.get('rows') or [])}, "
        f"ошибок {len(optional_errors)} | прошло {time.monotonic() - started:.1f}с",
        flush=True,
    )

    try:
        inventory = inventory_payload(
            app,
            args.client,
            args.marketplace,
            start,
            requested_cutoff,
        )
    except Exception as exc:  # optional source
        optional_errors.append(f"inventory: {safe_error(exc)}")
        inventory = {"rows": []}
    print(
        "ПРОГРЕСС: 4/4 (100%) | остатки | "
        f"снимков {len(inventory.get('rows') or [])}, "
        f"ошибок {len(optional_errors)} | прошло {time.monotonic() - started:.1f}с",
        flush=True,
    )

    daily_rows = merge_daily_rows(
        start,
        requested_cutoff,
        funnel_payload.get("rows") or [],
        daily_planfact.get("rows") or [],
        inventory.get("rows") or [],
    )
    dq = dict(source.get("dq") or {})
    metadata_values = {
        "analysis_freshness_days": dq.get("analysis_freshness_days"),
        "coverage_14_pct_fraction": (
            float(dq["coverage_14_pct"]) / 100
            if dq.get("coverage_14_pct") is not None
            else None
        ),
        "duplicate_share_fraction": None,
        "outcome_maturity_fraction": None,
    }
    payload = {
        "version": "1.0.0",
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": MOSCOW_TZ,
        "client": args.client,
        "client_label": label,
        "marketplace": args.marketplace,
        "requested_cutoff": requested_cutoff.isoformat(),
        "analysis_date": analysis_date.isoformat(),
        "current_month": current_month.isoformat(),
        "history_start": start.isoformat(),
        "registry_version": registry_version,
        "source_status": source.get("dq", {}).get("status") or "blocked",
        "source_dq": dq,
        "metadata_values": metadata_values,
        "current_plan": current_plan(
            monthly_planfact.get("rows") or [],
            args.marketplace,
            current_month,
        ),
        "monthly_planfact": [
            {str(key): json_value(value) for key, value in row.items()}
            for row in monthly_planfact.get("rows") or []
            if str(row.get("marketplace") or "").lower() == args.marketplace
        ],
        "daily_rows": daily_rows,
        "source_fields": sorted(
            {
                key
                for row in daily_rows
                for key, value in row.items()
                if key != "report_date" and value is not None
            }
        ),
        "optional_errors": optional_errors,
        "sources": [
            {
                "name": "WB funnel daily",
                "canonical_object": "mv_wb_funnel_daily_by_article_category",
                "role": "воронка, реклама, органика",
                "cutoff": analysis_date.isoformat(),
            },
            {
                "name": "Plan-fact daily/monthly",
                "canonical_object": "mv_planfact_daily / mv_planfact_monthly",
                "role": "GMV, продажи, рекламный бюджет и факты",
                "cutoff": analysis_date.isoformat(),
            },
            {
                "name": "Inventory history",
                "canonical_object": "inventory_history_daily",
                "role": "остатки и доступность ассортимента",
                "cutoff": max(
                    (
                        str(row.get("report_date"))[:10]
                        for row in inventory.get("rows") or []
                    ),
                    default=None,
                ),
            },
        ],
    }
    output = Path(args.output)
    write_json(output, payload)
    print(
        "ИТОГ: "
        f"дней {len(daily_rows)}, полей {len(payload['source_fields'])}, "
        f"опциональных ошибок {len(optional_errors)}; файл {output}; "
        f"время {time.monotonic() - started:.1f}с; завершено полностью.",
        flush=True,
    )
    return payload


def parser() -> argparse.ArgumentParser:
    root = root_dir()
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bi-project", default=str(BI_PROJECT_DEFAULT))
    result.add_argument("--client", default="gloria_jeans")
    result.add_argument("--marketplace", choices=("wb", "ozon"), default="wb")
    result.add_argument("--cutoff", default="")
    result.add_argument("--history-months", type=int, default=3)
    result.add_argument(
        "--registry",
        default=str(root / "config" / "wb-screening" / "metric_registry.v1.json"),
    )
    result.add_argument(
        "--output",
        default=str(
            root
            / "outputs"
            / "plan-fact-funnel-20260731"
            / "source_snapshot.json"
        ),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.history_months < 1 or args.history_months > 12:
        raise SystemExit("--history-months должен быть от 1 до 12")
    try:
        run(args)
        return 0
    except Exception as exc:
        print(f"ИТОГ: снимок не сформирован: {safe_error(exc)}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
