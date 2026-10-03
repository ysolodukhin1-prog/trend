from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Iterable
from zoneinfo import ZoneInfo


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
MARKETPLACE_TIMEZONE = ZoneInfo("Europe/Moscow")


def marketplace_today() -> date:
    """Return the business date used by TOPTOP marketplace reports."""
    return datetime.now(MARKETPLACE_TIMEZONE).date()


API_COMPLETENESS_SPECS = {
    "ozon_funnel": {
        "run_table": "km_ozon_api_runs",
        "run_step": "funnel",
        "relation": "ozon_funnel_daily",
        "date_column": "report_date",
    },
    "ozon_stock": {
        "relation": "inventory_history_daily",
        "date_column": "snapshot_date",
        "where": "marketplace = 'ozon'",
        "snapshot": True,
    },
    "ozon_feedbacks": {
        # Отзывы — это снимок кабинета, а не ряд по дате публикации. Контролируем
        # дату синхронизации, иначе старые отзывы ошибочно выглядят как пробелы.
        "relation": "marketplace_reviews",
        "date_column": "source_synced_at",
        "where": "marketplace = 'ozon'",
        "snapshot": True,
    },
    "ozon_advertising": {
        "run_table": "km_ozon_api_runs",
        "run_step": "advertising",
        "relation": "ozon_adv_daily_raw",
        "date_column": "report_date",
    },
    "ozon_finance": {
        "relation": "ozon_product_price_snapshots",
        "date_column": "snapshot_date",
        "snapshot": True,
    },
    "wb_orders_sales": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "statistics",
        # flag=0 is a change feed: repeat recent change dates even when a
        # previous successful import already covers these calendar dates.
        "refresh_days": 2,
    },
    "wb_stock_current": {
        "relation": "wb_stock_api_current",
        "date_column": "snapshot_date",
        "snapshot": True,
    },
    "wb_stock_history": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "stock_history",
        "relation": "wb_inventory_history_daily_csv",
        "date_column": "snapshot_date",
        "source_max_depth_days": 92,
    },
    "wb_funnel": {
        "run_table": "km_wb_api_runs",
        "run_step": "funnel",
        "relation": "wb_funnel_daily",
        "date_column": "report_date",
        "retention_days": 365,
        "refresh_days": 31,
    },
    "wb_advertising": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "promotion",
        "relation": "wb_adv_daily_raw",
        "date_column": "report_date",
        "source_max_depth_days": 92,
        "include_current_day": True,
        "refresh_days": 2,
    },
    "wb_search": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "analytics",
        "retention_days": 7,
    },
    "wb_feedbacks_questions": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "communication",
        "source_max_depth_days": 92,
    },
    "wb_finance": {
        "run_table": "km_wb_extended_api_runs",
        "run_step": "finance",
        "source_max_depth_days": 92,
    },
    "avito_advertising": {
        "run_table": "avito_ads_api_runs",
        "run_step": "advertising",
        "relation": "avito_ads_stats_daily",
        "date_column": "report_date",
    },
}


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def merge_date_ranges(ranges: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    normalized = sorted((start, end) for start, end in ranges if start and end and start <= end)
    merged: list[list[date]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def missing_date_ranges(
    expected_from: date,
    expected_to: date,
    covered_ranges: Iterable[tuple[date, date]],
) -> list[tuple[date, date]]:
    if expected_from > expected_to:
        return []
    clipped = []
    for start, end in covered_ranges:
        start = max(start, expected_from)
        end = min(end, expected_to)
        if start <= end:
            clipped.append((start, end))
    missing = []
    cursor = expected_from
    for start, end in merge_date_ranges(clipped):
        if cursor < start:
            missing.append((cursor, start - timedelta(days=1)))
        cursor = max(cursor, end + timedelta(days=1))
    if cursor <= expected_to:
        missing.append((cursor, expected_to))
    return missing


def _relation_exists(cur, relation: str) -> bool:
    if not _IDENTIFIER_RE.fullmatch(relation):
        raise ValueError("Некорректное имя таблицы контроля полноты")
    cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
    return cur.fetchone()[0] is not None


def _run_ranges(cur, table: str, step: str) -> list[tuple[date, date]]:
    if not _relation_exists(cur, table):
        return []
    cur.execute(
        f"""
        SELECT date_from, date_to
        FROM public.{table}
        WHERE step = %s AND status = 'ok'
          AND date_from IS NOT NULL AND date_to IS NOT NULL
        """,
        (step,),
    )
    return [(_as_date(row[0]), _as_date(row[1])) for row in cur.fetchall() if row[0] and row[1]]


def _stored_dates(cur, spec: dict, date_from: date, date_to: date) -> list[date]:
    relation = spec.get("relation")
    column = spec.get("date_column")
    if not relation or not column or not _relation_exists(cur, relation):
        return []
    if not _IDENTIFIER_RE.fullmatch(column):
        raise ValueError("Некорректное поле даты контроля полноты")
    where = f" AND {spec['where']}" if spec.get("where") else ""
    cur.execute(
        f"""
        SELECT DISTINCT {column}::date
        FROM public.{relation}
        WHERE {column}::date BETWEEN %s AND %s{where}
        ORDER BY 1
        """,
        (date_from, date_to),
    )
    return [_as_date(row[0]) for row in cur.fetchall() if row[0]]


def _latest_stored_date(cur, spec: dict, date_to: date) -> date | None:
    relation = spec.get("relation")
    column = spec.get("date_column")
    if not relation or not column or not _relation_exists(cur, relation):
        return None
    if not _IDENTIFIER_RE.fullmatch(column):
        raise ValueError("Некорректное поле даты контроля полноты")
    where = f" AND {spec['where']}" if spec.get("where") else ""
    cur.execute(
        f"SELECT max({column}::date) FROM public.{relation} WHERE {column}::date <= %s{where}",
        (date_to,),
    )
    row = cur.fetchone()
    return _as_date(row[0]) if row and row[0] else None


def _earliest_stored_date(cur, spec: dict, date_to: date) -> date | None:
    relation = spec.get("relation")
    column = spec.get("date_column")
    if not relation or not column or not _relation_exists(cur, relation):
        return None
    if not _IDENTIFIER_RE.fullmatch(column):
        raise ValueError("Некорректное поле даты контроля полноты")
    where = f" AND {spec['where']}" if spec.get("where") else ""
    cur.execute(
        f"SELECT min({column}::date) FROM public.{relation} WHERE {column}::date <= %s{where}",
        (date_to,),
    )
    row = cur.fetchone()
    return _as_date(row[0]) if row and row[0] else None


def _single_day_ranges(values: Iterable[date]) -> list[tuple[date, date]]:
    return merge_date_ranges((value, value) for value in values if value)


def inspect_client_api_completeness(
    connection,
    *,
    history_date_from: str = "",
    today: date | None = None,
) -> dict[str, dict]:
    current_day = today or marketplace_today()
    historical_target = current_day - timedelta(days=1)
    configured_start = _as_date(history_date_from)
    result: dict[str, dict] = {}
    with connection.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15000ms'")
        for step_key, spec in API_COMPLETENESS_SPECS.items():
            target = current_day if spec.get("snapshot") or spec.get("include_current_day") else historical_target
            ranges = []
            if spec.get("run_table"):
                ranges.extend(_run_ranges(cur, spec["run_table"], spec["run_step"]))

            observed_start = min((start for start, _end in ranges), default=None)
            stored_start = None if spec.get("snapshot") else _earliest_stored_date(cur, spec, target)
            baseline = target if spec.get("snapshot") else (configured_start or observed_start or stored_start or target)
            retention_days = int(spec.get("retention_days") or 0)
            if retention_days:
                baseline = max(baseline, target - timedelta(days=retention_days - 1))
            # Глубина, за которую источник физически отдаёт данные. Без этого клампа
            # окна догрузки уходят за горизонт API, этап падает на каждом прогоне
            # и остаётся в плане навсегда.
            source_max_depth_days = int(spec.get("source_max_depth_days") or 0)
            if source_max_depth_days:
                baseline = max(baseline, target - timedelta(days=source_max_depth_days - 1))

            if spec.get("snapshot"):
                latest_stored = _latest_stored_date(cur, spec, target)
                stored_dates = [latest_stored] if latest_stored else []
            else:
                stored_dates = _stored_dates(cur, spec, baseline, target)
            ranges.extend(_single_day_ranges(stored_dates))
            merged = merge_date_ranges(ranges)
            missing = missing_date_ranges(baseline, target, merged)
            coverage_from = min((start for start, _end in merged), default=None)
            coverage_to = max((end for _start, end in merged), default=None)
            result[step_key] = {
                "expected_from": _iso(baseline),
                "expected_to": _iso(target),
                "coverage_from": _iso(coverage_from),
                "coverage_to": _iso(coverage_to),
                "missing_windows": [
                    {"date_from": _iso(start), "date_to": _iso(end)} for start, end in missing
                ],
                "missing_days": sum((end - start).days + 1 for start, end in missing),
                "snapshot": bool(spec.get("snapshot")),
                "retention_days": retention_days,
                "source_max_depth_days": source_max_depth_days,
                "refresh_days": int(spec.get("refresh_days") or 0),
            }
    return result


def fallback_client_api_completeness(*, today: date | None = None, reason: str = "") -> dict[str, dict]:
    current_day = today or marketplace_today()
    result = {}
    for step_key, spec in API_COMPLETENESS_SPECS.items():
        target = current_day if spec.get("snapshot") or spec.get("include_current_day") else current_day - timedelta(days=1)
        value = target.isoformat()
        result[step_key] = {
            "expected_from": value,
            "expected_to": value,
            "coverage_from": "",
            "coverage_to": "",
            "missing_windows": [{"date_from": value, "date_to": value}],
            "missing_days": 1,
            "snapshot": bool(spec.get("snapshot")),
            "retention_days": int(spec.get("retention_days") or 0),
            "source_max_depth_days": int(spec.get("source_max_depth_days") or 0),
            "refresh_days": int(spec.get("refresh_days") or 0),
            "inspection_error": str(reason or "Статус БД временно недоступен")[:240],
        }
    return result
