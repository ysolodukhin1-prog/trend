"""Background orchestration for the Admin client-onboarding wizard."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path


_LOCK = threading.Lock()
_RUNNING: set[str] = set()
_HISTORY_RUNTIME: dict[str, dict] = {}
_HISTORY_PROCESSES: dict[str, subprocess.Popen] = {}
_HISTORY_STOP_EVENTS: dict[str, threading.Event] = {}
_MAX_HISTORY_LOGS = 300


def _app():
    import app

    return app


def _onboarding_client_key(app, value: str) -> str:
    """Keep database-backed Admin clients addressable after registry onboarding."""
    raw = str(value or "").strip().lower()
    if re.fullmatch(r"^[a-z][a-z0-9_]{1,47}$", raw):
        return raw
    return app.normalize_client_key(raw)


def _interrupted_operation_error(operation: str) -> str:
    if operation == "history":
        return "Фоновый процесс не найден после перезапуска сервера. Нажмите «Продолжить», чтобы дозагрузить оставшиеся разделы."
    if operation == "assortment":
        return "Фоновое обновление ассортимента не найдено после перезапуска. Сохранённые API-данны не удалены; запустите soft-update повторно."
    return "Фоновый процесс не найден после перезапуска сервера. Запустите операцию повторно."


def reconcile_orphaned_rows(rows: list[dict]) -> list[dict]:
    """Finalize registry operations whose owning backend process disappeared."""
    app = _app()
    with _LOCK:
        running_here = set(_RUNNING)
    orphaned = []
    for row in rows:
        if row.get("operation_status") != "running" or row.get("key") in running_here:
            continue
        active_ozon_run = _active_ozon_api_run(app, row, _external_ozon_step_keys())
        if active_ozon_run:
            _sync_external_ozon_operation(row, active_ozon_run)
            continue
        orphaned.append(row)
    if not orphaned:
        return rows
    for row in orphaned:
        operation = str(row.get("operation") or "history")
        _set_operation(
            row["key"],
            operation,
            "failed",
            "interrupted",
            int(row.get("operation_progress") or 0),
            "Операция прервана перезапуском backend",
            _interrupted_operation_error(operation),
            history_date_from=row.get("history_date_from") or None,
            history_date_to=row.get("history_date_to") or None,
        )
    from client_registry import list_clients
    with app.client_registry_connection() as conn:
        return list_clients(conn)


def payload(client_key: str) -> dict:
    from client_registry import get_client

    app = _app()
    normalized = _onboarding_client_key(app, client_key)
    with app.client_registry_connection() as conn:
        row = get_client(conn, normalized)
    if not row:
        raise ValueError("Клиент не найден в реестре")
    active_ozon_run = _active_ozon_api_run(app, row, _external_ozon_step_keys())
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(normalized)
        if runtime:
            row = {
                **row,
                "operation_logs": [dict(item) for item in runtime.get("logs", [])],
                "operation_steps": [dict(item) for item in runtime.get("steps", [])],
                "operation_pid": runtime.get("pid"),
                "operation_stop_requested": bool(runtime.get("stop_requested")),
                "operation_request_count": int(runtime.get("request_count") or 0),
                "operation_overwrite": bool(runtime.get("overwrite")),
                "operation_wb_stock_history_date_from": runtime.get("wb_stock_history_date_from") or "",
                "operation_wb_stock_history_date_to": runtime.get("wb_stock_history_date_to") or "",
            }
        is_orphaned = row.get("operation_status") == "running" and normalized not in _RUNNING and not active_ozon_run
    if active_ozon_run and not runtime:
        _sync_external_ozon_operation(row, active_ozon_run)
        with app.client_registry_connection() as conn:
            row = get_client(conn, normalized)
        row = _external_ozon_runtime_payload(app, row, active_ozon_run)
    elif is_orphaned:
        _set_operation(
            normalized,
            str(row.get("operation") or "history"),
            "failed",
            "interrupted",
            int(row.get("operation_progress") or 0),
            "Операция прервана перезапуском backend",
            _interrupted_operation_error(str(row.get("operation") or "history")),
            history_date_from=row.get("history_date_from") or None,
            history_date_to=row.get("history_date_to") or None,
        )
        with app.client_registry_connection() as conn:
            row = get_client(conn, normalized)
    row["history_available_steps"] = {
        marketplace: _history_step_plan(app, row, marketplaces=[marketplace])
        for marketplace in (row.get("marketplaces") or [])
    }
    return {"ok": True, "client": row}


_WB_INSPECTION_SPECS = (
    {
        "key": "wb_catalog",
        "label": "Ассортимент и характеристики",
        "run_table": "extended",
        "run_step": "content",
        "source_keys": ("content.cards",),
        "object": "wb_api_entities · content.cards",
    },
    {
        "key": "wb_orders_sales",
        "label": "Заказы и продажи",
        "run_table": "extended",
        "run_step": "statistics",
        "source_keys": ("statistics.orders", "statistics.sales"),
        "object": "wb_api_entities · statistics.orders + statistics.sales",
        "date_column": "record_date",
    },
    {
        "key": "wb_stock_current",
        "label": "Текущие остатки",
        "run_table": "api",
        "run_step": "stock",
        "relation": "wb_stock_api_current",
        "object": "wb_stock_api_current",
        "date_column": "snapshot_date",
    },
    {
        "key": "wb_stock_history",
        "label": "История остатков",
        "run_table": "extended",
        "run_step": "stock_history",
        "relation": "wb_inventory_history_daily_csv",
        "object": "wb_inventory_history_daily_csv",
        "date_column": "snapshot_date",
    },
    {
        "key": "wb_funnel",
        "label": "Воронка продаж",
        "run_table": "api",
        "run_step": "funnel",
        "relation": "wb_funnel_daily",
        "object": "wb_funnel_daily",
        "date_column": "report_date",
        "limit_note": "WB API: максимум последние 7 дней",
    },
    {
        "key": "wb_advertising",
        "label": "Рекламные кампании",
        "run_table": "extended",
        "run_step": "promotion",
        "source_keys": ("promotion.campaigns", "promotion.fullstats", "promotion.normquery_stats"),
        "object": "wb_api_entities · кампании + общая статистика + поисковые фразы",
        "date_column": "record_date",
    },
    {
        "key": "wb_search",
        "label": "Поисковая аналитика",
        "run_table": "extended",
        "run_step": "analytics",
        "source_keys": ("analytics.search_report", "analytics.search_details"),
        "object": "wb_api_entities · analytics.search_report + analytics.search_details",
        "date_column": "record_date",
        "limit_note": "Последние 7 дней · требуется WB Jam",
    },
    {
        "key": "wb_feedbacks_questions",
        "label": "Отзывы и вопросы",
        "run_table": "extended",
        "run_step": "communication",
        "source_keys": ("communication.feedbacks", "communication.questions"),
        "object": "marketplace_reviews + marketplace_questions",
        "date_column": "record_date",
        "limit_note": "Требуется категория токена «Вопросы и отзывы»",
    },
    {
        "key": "wb_finance",
        "label": "Финансовые отчёты",
        "run_table": "extended",
        "run_step": "finance",
        "source_keys": ("finance.balance", "finance.sales_reports"),
        "object": "wb_api_entities · finance.balance + finance.sales_reports",
        "date_column": "record_date",
    },
    {
        "key": "wb_views",
        "label": "Витрины BI",
        "run_table": "api",
        "run_step": "views",
        "relations": (
            "mv_wb_funnel_daily_by_article_category",
            "mv_product_abc_wb",
            "mv_sku_card_scoring_wb",
        ),
        "object": "3 основные materialized views WB",
    },
)

_LAMODA_INSPECTION_SPECS = (
    {"key": "lamoda_orders", "label": "Заказы и статусы", "description": "Заказы Lamoda за выбранный период.", "dataset": "orders", "relation": "lamoda_v2_entities", "object": "lamoda_v2_entities · orders", "date_column": "event_at"},
    {"key": "lamoda_stock", "label": "Остатки", "description": "Сводные остатки Lamoda.", "dataset": "stock", "relation": "lamoda_v2_entities", "object": "lamoda_v2_entities · stock", "date_column": "snapshot_date"},
    {"key": "lamoda_catalog", "label": "Каталог", "description": "Номенклатуры и статусы товаров.", "dataset": "catalog", "relation": "lamoda_v2_entities", "object": "lamoda_v2_entities · catalog", "date_column": "snapshot_date"},
    {"key": "lamoda_prices", "label": "Цены и скидки", "description": "Текущие цены и скидки из карточек товаров.", "dataset": "prices", "relation": "lamoda_v2_entities", "object": "v_lamoda_prices", "date_column": "snapshot_date"},
    {"key": "lamoda_promotions", "label": "Продвижение и акции", "description": "Акции Lamoda; только чтение, без добавления или удаления товаров.", "dataset": "promotions", "relation": "lamoda_v2_entities", "object": "v_lamoda_promotions", "date_column": "snapshot_date"},
    {"key": "lamoda_fbo_shipments", "label": "Поставки и приёмка FBO", "description": "Поставки на склады Lamoda и их статусы.", "dataset": "fbo_shipments", "relation": "lamoda_v2_entities", "object": "v_lamoda_fbo_shipments", "date_column": "event_at"},
    {"key": "lamoda_fbs_returns", "label": "Возвраты FBS", "description": "Возвратные товары FBS и их статусы.", "dataset": "fbs_returns", "relation": "lamoda_v2_entities", "object": "v_lamoda_fbs_returns", "date_column": "event_at"},
    {"key": "lamoda_finance", "label": "Расходы и финансовые документы", "description": "Нужен отдельный подтверждённый экспорт Lamoda Seller/LAB; расходы не рассчитываются из заказов.", "source_unavailable": True, "relation": "lamoda_finance", "object": "экспорт Lamoda Seller/LAB"},
)


_AVITO_INSPECTION_SPECS = (
    {
        "key": "avito_account",
        "label": "Аккаунт",
        "description": "Карточка кабинета: Account ID и полный набор параметров аккаунта из ответа Avito Ads API.",
        "relation": "avito_ads_account_snapshots",
        "object": "avito_ads_account_snapshots",
        "date_column": "snapshot_date",
    },
    {
        "key": "avito_balance",
        "label": "Баланс",
        "description": "Основной и бонусный баланс рекламного кабинета на дату снимка.",
        "relation": "avito_ads_balances_daily",
        "object": "avito_ads_balances_daily",
        "date_column": "snapshot_date",
    },
    {
        "key": "avito_campaigns",
        "label": "Кампании",
        "description": "ID, название и статус кампании; рекламодатель, договор, тип, модель оплаты, бюджет и время обновления.",
        "relation": "avito_ads_campaigns",
        "object": "avito_ads_campaigns",
    },
    {
        "key": "avito_groups",
        "label": "Группы",
        "description": "ID и название группы, родительская кампания, статус, бюджет и установленная цена.",
        "relation": "avito_ads_groups",
        "object": "avito_ads_groups",
    },
    {
        "key": "avito_creatives",
        "label": "Объявления",
        "description": "ID, название и статус объявления, связи с кампанией и группой, ссылка на предпросмотр.",
        "relation": "avito_ads_creatives",
        "object": "avito_ads_creatives",
    },
    {
        "key": "avito_advertising",
        "label": "Дневная рекламная статистика",
        "description": "По дням и уровням кампания / группа / объявление: показы, клики, CTR, расходы и бонусы, CPM, CPC, просмотры видео 25/50/75/100%, Q25/Q50/Q75 и VTR.",
        "relation": "avito_ads_stats_daily",
        "object": "avito_ads_stats_daily · campaign + group + creative",
        "date_column": "report_date",
        "requested_period": True,
    },
)


def _inspection_value(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _inspection_relation_metrics(cur, relation: str, date_column: str | None = None) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", relation):
        raise ValueError("Некорректное имя объекта инспекции")
    cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
    if cur.fetchone()[0] is None:
        return {"exists": False, "rows": 0, "date_from": None, "date_to": None}
    if date_column:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", date_column):
            raise ValueError("Некорректное поле даты инспекции")
        cur.execute(
            f'SELECT count(*), min("{date_column}"), max("{date_column}") FROM public."{relation}"'
        )
        rows, date_from, date_to = cur.fetchone()
    else:
        cur.execute(f'SELECT count(*) FROM public."{relation}"')
        rows = cur.fetchone()[0]
        date_from = date_to = None
    return {
        "exists": True,
        "rows": int(rows or 0),
        "date_from": _inspection_value(date_from),
        "date_to": _inspection_value(date_to),
    }


def _inspection_source_metrics(cur, source_keys: tuple[str, ...], date_column: str | None = None) -> dict:
    cur.execute("SELECT to_regclass('public.wb_api_entities')")
    if cur.fetchone()[0] is None:
        return {"exists": False, "rows": 0, "date_from": None, "date_to": None}
    if date_column:
        cur.execute(
            """
            SELECT count(*), min(record_date), max(record_date)
            FROM public.wb_api_entities
            WHERE source_key = ANY(%s)
            """,
            (list(source_keys),),
        )
        rows, date_from, date_to = cur.fetchone()
    else:
        cur.execute(
            "SELECT count(*) FROM public.wb_api_entities WHERE source_key = ANY(%s)",
            (list(source_keys),),
        )
        rows = cur.fetchone()[0]
        date_from = date_to = None
    return {
        "exists": True,
        "rows": int(rows or 0),
        "date_from": _inspection_value(date_from),
        "date_to": _inspection_value(date_to),
    }


def _inspection_latest_runs(cur, table_name: str) -> dict[str, dict]:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    if cur.fetchone()[0] is None:
        return {}
    cur.execute(
        f"""
        SELECT DISTINCT ON (step)
               step, date_from, date_to, requests_count, rows_count,
               status, error, started_at, finished_at
        FROM public.{table_name}
        ORDER BY step, started_at DESC, run_id DESC
        """
    )
    return {
        str(row[0]): {
            "date_from": _inspection_value(row[1]),
            "date_to": _inspection_value(row[2]),
            "requests": int(row[3] or 0),
            "rows": int(row[4] or 0),
            "status": str(row[5] or ""),
            "error": str(row[6] or ""),
            "started_at": _inspection_value(row[7]),
            "finished_at": _inspection_value(row[8]),
        }
        for row in cur.fetchall()
    }


def _inspection_latest_lamoda_run(cur) -> dict | None:
    cur.execute("SELECT to_regclass('public.lamoda_api_runs')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute(
        """
        SELECT date_from, date_to, requests_made, rows_loaded,
               status, error, started_at, finished_at
        FROM public.lamoda_api_runs
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "date_from": _inspection_value(row[0]),
        "date_to": _inspection_value(row[1]),
        "requests": int(row[2] or 0),
        "rows": int(row[3] or 0),
        "status": str(row[4] or ""),
        "error": str(row[5] or ""),
        "started_at": _inspection_value(row[6]),
        "finished_at": _inspection_value(row[7]),
    }


def _inspection_latest_avito_run(cur) -> dict | None:
    cur.execute("SELECT to_regclass('public.avito_ads_api_runs')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute(
        """
        SELECT step, date_from, date_to, requests_made, rows_loaded,
               status, COALESCE(details->>'error', ''), started_at, finished_at
        FROM public.avito_ads_api_runs
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "date_from": _inspection_value(row[1]),
        "date_to": _inspection_value(row[2]),
        "requests": int(row[3] or 0),
        "rows": int(row[4] or 0),
        "status": str(row[5] or ""),
        "error": str(row[6] or ""),
        "started_at": _inspection_value(row[7]),
        "finished_at": _inspection_value(row[8]),
    }


def _inspection_avito_coverage(cur, requested_from: date | None, requested_to: date | None) -> dict:
    if not requested_from or not requested_to:
        return {"requested_days": 0, "covered_days": 0, "missing_days": 0, "missing_windows": []}
    from api_completeness import missing_date_ranges

    cur.execute("SELECT to_regclass('public.avito_ads_api_runs')")
    ranges = []
    if cur.fetchone()[0] is not None:
        cur.execute(
            """SELECT date_from, date_to FROM public.avito_ads_api_runs
               WHERE step='advertising' AND status='ok'
                 AND date_to >= %s AND date_from <= %s""",
            (requested_from, requested_to),
        )
        ranges = [(row[0], row[1]) for row in cur.fetchall() if row[0] and row[1]]
    missing = missing_date_ranges(requested_from, requested_to, ranges)
    requested_days = (requested_to - requested_from).days + 1
    missing_days = sum((end - start).days + 1 for start, end in missing)
    return {
        "requested_days": requested_days,
        "covered_days": requested_days - missing_days,
        "missing_days": missing_days,
        "missing_windows": [
            {"date_from": start.isoformat(), "date_to": end.isoformat()}
            for start, end in missing
        ],
    }


def _inspect_avito_history_data(app, client: dict, requested_from: date | None, requested_to: date | None) -> dict:
    try:
        import psycopg2
    except Exception as exc:
        raise RuntimeError("Для инспекции PostgreSQL требуется psycopg2") from exc
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    rows: list[dict] = []
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15000ms'")
        latest_run = _inspection_latest_avito_run(cur)
        coverage = _inspection_avito_coverage(cur, requested_from, requested_to)
        for spec in _AVITO_INSPECTION_SPECS:
            if spec.get("requested_period") and requested_from and requested_to:
                relation = spec["relation"]
                column = spec["date_column"]
                cur.execute("SELECT to_regclass(%s)", (f"public.{relation}",))
                if cur.fetchone()[0] is None:
                    data = {"exists": False, "rows": 0, "date_from": None, "date_to": None}
                else:
                    cur.execute(
                        f"""SELECT count(*), min({column}), max({column})
                            FROM public.{relation} WHERE {column} BETWEEN %s AND %s""",
                        (requested_from, requested_to),
                    )
                    count, stored_from, stored_to = cur.fetchone()
                    data = {
                        "exists": True,
                        "rows": int(count or 0),
                        "date_from": _inspection_value(stored_from),
                        "date_to": _inspection_value(stored_to),
                    }
            else:
                data = _inspection_relation_metrics(cur, spec["relation"], spec.get("date_column"))
            latest_status = str((latest_run or {}).get("status") or "").lower()
            if spec.get("requested_period"):
                if coverage["covered_days"] and not coverage["missing_days"]:
                    status, status_label = "loaded", "Период покрыт"
                elif coverage["covered_days"]:
                    status, status_label = "limited", "Частично"
                elif latest_status in {"failed", "error", "stopped"}:
                    status, status_label = "error", "Ошибка"
                else:
                    status, status_label = "missing", "Нет покрытия"
            elif data["rows"] > 0:
                status, status_label = "loaded", "Загружено"
            elif latest_status in {"failed", "error", "stopped"}:
                status, status_label = "error", "Ошибка"
            elif latest_run:
                status, status_label = "empty", "Пусто"
            else:
                status, status_label = "missing", "Не запускалось"
            row = {
                "key": spec["key"],
                "label": spec["label"],
                "description": spec.get("description") or "",
                "object": spec["object"],
                "status": status,
                "status_label": status_label,
                "rows": data["rows"],
                "date_from": data.get("date_from"),
                "date_to": data.get("date_to"),
                "latest_run": latest_run,
            }
            if spec.get("requested_period"):
                row.update(coverage)
                row["limit_note"] = "Avito API: одно окно запроса не более 100 дней"
            rows.append(row)
    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in ("loaded", "limited", "empty", "missing", "error")
    }
    return {
        "ok": True,
        "client": {"key": client["key"], "label": client["label"], "db_name": client["db_name"]},
        "marketplace": "avito",
        "requested_period": {
            "date_from": _inspection_value(requested_from),
            "date_to": _inspection_value(requested_to),
        },
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {"total": len(rows), **counts},
        "coverage": coverage,
        "rows": rows,
    }


def _inspect_lamoda_history_data(app, client: dict, requested_from: date | None, requested_to: date | None) -> dict:
    import psycopg2
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    rows = []
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15000ms'")
        latest_run = _inspection_latest_lamoda_run(cur)
        failed = str((latest_run or {}).get("status") or "").lower() in {"failed", "error", "stopped"}
        for spec in _LAMODA_INSPECTION_SPECS:
            if spec.get("source_unavailable"):
                data = {"rows": 0, "date_from": None, "date_to": None}
                status = "limited"
            elif spec.get("dataset"):
                cur.execute("SELECT to_regclass('public.lamoda_v2_entities')")
                if cur.fetchone()[0] is None:
                    data = {"rows": 0, "date_from": None, "date_to": None}
                else:
                    date_column = spec.get("date_column") or "snapshot_date"
                    cur.execute(
                        f'SELECT count(*), min("{date_column}"), max("{date_column}") '
                        "FROM public.lamoda_v2_entities WHERE dataset = %s",
                        (spec["dataset"],),
                    )
                    count, first_date, last_date = cur.fetchone()
                    data = {"rows": int(count or 0), "date_from": _inspection_value(first_date), "date_to": _inspection_value(last_date)}
                status = "error" if failed else ("loaded" if data["rows"] else ("empty" if latest_run else "missing"))
            else:
                data = _inspection_relation_metrics(cur, spec["relation"], spec.get("date_column"))
                status = "error" if failed else ("loaded" if data["rows"] else ("empty" if latest_run else "missing"))
            rows.append({**spec, "status": status, "status_label": status.title(), "rows": data["rows"], "date_from": data.get("date_from"), "date_to": data.get("date_to"), "latest_run": latest_run})
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in ("loaded", "limited", "empty", "missing", "error")}
    return {"ok": True, "client": {"key": client["key"], "label": client["label"], "db_name": client["db_name"]}, "marketplace": "lamoda", "requested_period": {"date_from": _inspection_value(requested_from), "date_to": _inspection_value(requested_to)}, "checked_at": datetime.now().isoformat(timespec="seconds"), "summary": {"total": len(rows), **counts}, "rows": rows}


def inspect_history_data(client_key: str, marketplace: str, date_from: str = "", date_to: str = "") -> dict:
    """Read the selected client's database and report what a history run actually stored."""
    from client_registry import get_client

    app = _app()
    normalized = _onboarding_client_key(app, client_key)
    with app.client_registry_connection() as registry_conn:
        client = get_client(registry_conn, normalized)
    if not client or client.get("status") != "active":
        raise ValueError("Клиент не найден в реестре")
    selected_marketplace = str(marketplace or "").strip().lower()
    if selected_marketplace not in {"wb", "avito", "lamoda", "yandex_market"} or selected_marketplace not in set(client.get("marketplaces") or []):
        raise ValueError("Инспекция доступна для подключённых WB, Avito и Яндекса")
    requested_from = date.fromisoformat(date_from) if date_from else None
    requested_to = date.fromisoformat(date_to) if date_to else None
    if requested_from and requested_to and requested_from > requested_to:
        raise ValueError("Дата начала позже даты окончания")
    if selected_marketplace == "avito":
        return _inspect_avito_history_data(app, client, requested_from, requested_to)
    if selected_marketplace == "lamoda":
        return _inspect_lamoda_history_data(app, client, requested_from, requested_to)
    if selected_marketplace == "yandex_market":
        from yandex_market_history import inspect_history
        return inspect_history(app, client, requested_from, requested_to)

    try:
        import psycopg2
    except Exception as exc:
        raise RuntimeError("Для инспекции PostgreSQL требуется psycopg2") from exc
    config = dict(app.read_db_config())
    config["database"] = client.get("db_name")
    rows: list[dict] = []
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15000ms'")
        latest_runs = {
            "api": _inspection_latest_runs(cur, "km_wb_api_runs"),
            "extended": _inspection_latest_runs(cur, "km_wb_extended_api_runs"),
        }
        for spec in _WB_INSPECTION_SPECS:
            if spec.get("relations"):
                metrics = [_inspection_relation_metrics(cur, relation) for relation in spec["relations"]]
                present = sum(1 for item in metrics if item["exists"])
                data = {
                    "exists": present > 0,
                    "rows": sum(item["rows"] for item in metrics),
                    "date_from": None,
                    "date_to": None,
                    "objects_present": present,
                    "objects_total": len(metrics),
                }
            elif spec.get("source_keys"):
                data = _inspection_source_metrics(cur, spec["source_keys"], spec.get("date_column"))
            else:
                data = _inspection_relation_metrics(cur, spec["relation"], spec.get("date_column"))
            latest_run = latest_runs.get(spec["run_table"], {}).get(spec["run_step"])
            latest_status = str((latest_run or {}).get("status") or "").lower()
            if latest_status in {"failed", "error", "stopped"}:
                status = "error"
                status_label = "Ошибка"
            elif latest_status == "blocked":
                status = "limited"
                status_label = "Нет доступа"
            elif data["rows"] > 0:
                status = "limited" if spec.get("limit_note") else "loaded"
                status_label = "Ограничено API" if spec.get("limit_note") else "Загружено"
            elif latest_run:
                status = "empty"
                status_label = "Пусто"
            else:
                status = "missing"
                status_label = "Не запускалось"
            rows.append({
                "key": spec["key"],
                "label": spec["label"],
                "object": spec["object"],
                "status": status,
                "status_label": status_label,
                "rows": data["rows"],
                "date_from": data.get("date_from"),
                "date_to": data.get("date_to"),
                "objects_present": data.get("objects_present"),
                "objects_total": data.get("objects_total"),
                "limit_note": spec.get("limit_note") or "",
                "latest_run": latest_run,
            })
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in ("loaded", "limited", "empty", "missing", "error")}
    return {
        "ok": True,
        "client": {"key": client["key"], "label": client["label"], "db_name": client["db_name"]},
        "marketplace": "wb",
        "requested_period": {
            "date_from": _inspection_value(requested_from),
            "date_to": _inspection_value(requested_to),
        },
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {"total": len(rows), **counts},
        "rows": rows,
    }


def _set_operation(client_key, operation, status, stage, progress, message="", error="", **dates):
    from client_registry import update_client_operation

    app = _app()
    with app.client_registry_connection() as conn:
        update_client_operation(
            conn,
            client_key,
            operation=operation,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            error=error,
            history_date_from=dates.get("history_date_from"),
            history_date_to=dates.get("history_date_to"),
        )


def _runtime_time() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _progress_log_mode(text: str, kind: str) -> str | None:
    """Return the UI log mode for progress lines that should be live-updated."""
    if kind != "output" or not text.startswith("ПРОГРЕСС:"):
        return None
    lowered = text.lower()
    if "плановая пауза api" in lowered or "ожидание лимита api" in lowered:
        return "wait"
    # The child scripts use \r for tqdm-like progress.  Pipes normalize that
    # into lines, so retain one line here until the state itself changes.
    return "running"


def _append_history_log(client_key: str, text: str, kind: str = "output") -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        if not runtime:
            return
        logs = runtime.setdefault("logs", [])
        mode = _progress_log_mode(clean, kind)
        last = logs[-1] if logs else None
        if mode and last and runtime.get("_live_progress_mode") == mode:
            last.update({"time": _runtime_time(), "type": kind, "text": clean[-1200:]})
        else:
            logs.append({"time": _runtime_time(), "type": kind, "text": clean[-1200:]})
        runtime["_live_progress_mode"] = mode
        runtime["logs"] = runtime["logs"][-_MAX_HISTORY_LOGS:]


def _history_step_plan(
    app,
    client: dict,
    selected_steps: list[str] | None = None,
    marketplaces: list[str] | None = None,
) -> list[dict]:
    steps: list[dict] = []
    connected_marketplaces = set(client.get("marketplaces") or [])
    requested_marketplaces = set(marketplaces) if marketplaces is not None else connected_marketplaces
    enabled_marketplaces = connected_marketplaces & requested_marketplaces
    if "ozon" in enabled_marketplaces:
        steps.extend([
            {"key": "ozon_assortment", "label": "Ozon · Ассортимент и характеристики", "script": "sync_ozon_assortment.py", "status": "pending"},
            {
                "key": "ozon_funnel",
                "label": "Ozon · Воронка и продажи",
                "script": "sync_km_ozon_api.py --step funnel",
                "status": "pending",
                "description": "Автовыбор Standard или Advanced по фактическому ответу Seller API",
                "substeps": [
                    "Проверка Premium и доступного контракта",
                    "Standard: заказы и выручка за доступные 3 месяца",
                    "Advanced: показы, сессии, корзины, заказы и доставки по SKU × день",
                    "Checkpoint и автоматическая пересборка витрин BI",
                ],
            },
            {"key": "ozon_stock", "label": "Ozon · Остатки", "script": "sync_km_ozon_api.py --step stock", "status": "pending"},
        ])
        if app.registered_client_credential(client["key"], "ozon_performance_client_id") and app.registered_client_credential(
            client["key"], "ozon_performance_client_secret"
        ):
            steps.append({
                "key": "ozon_advertising",
                "label": "Ozon · Реклама по типам кампаний",
                "script": "sync_km_ozon_api.py --step advertising",
                "status": "pending",
                "description": "Отдельные Performance API выгрузки без подмены campaign-level данных SKU-строками",
                "substeps": [
                    "Список и типы кампаний за период",
                    "CPC / product: товарная статистика и корзины",
                    "CPO / all-SKU promo: отдельный отчёт заказов",
                    "Campaign daily: контрольные итоги и сверка",
                ],
            })
        steps.append({"key": "ozon_finance", "label": "Ozon · Финансы и цены", "script": "sync_km_ozon_finance.py --source api", "status": "pending"})
        steps.append({"key": "ozon_views", "label": "Ozon · Витрины BI", "script": "sync_km_ozon_api.py --step views", "status": "pending"})
    if "wb" in enabled_marketplaces:
        steps.extend([
            {
                "key": "wb_catalog",
                "label": "WB · Ассортимент и характеристики",
                "script": "sync_km_wb_extended_api.py --step content",
                "status": "pending",
                "description": "Карточки товаров, бренды, категории, размеры и характеристики из Content API.",
            },
            {
                "key": "wb_orders_sales",
                "label": "WB · Заказы и продажи",
                "script": "sync_km_wb_extended_api.py --step statistics",
                "status": "pending",
                "description": "Заказы, продажи, возвраты и отмены из Statistics API за доступный WB период.",
            },
            {
                "key": "wb_stock_current",
                "label": "WB · Текущие остатки",
                "script": "sync_km_wb_api.py --step stock",
                "status": "pending",
                "description": "Текущий снимок остатков по товарам и складам.",
            },
            {
                "key": "wb_stock_history",
                "label": "WB · История остатков",
                "script": "sync_km_wb_extended_api.py --step stock_history",
                "status": "pending",
                "description": "Дневная история остатков за выбранный период через Seller Analytics CSV.",
            },
            {
                "key": "wb_funnel",
                "label": "WB · Воронка продаж",
                "script": "sync_km_wb_api.py --step funnel",
                "status": "pending",
                "description": "Показы карточек, корзины, заказы и выкупы; прямой API отдаёт максимум последние 7 дней.",
            },
            {
                "key": "wb_advertising",
                "label": "WB · Рекламные кампании",
                "script": "sync_km_wb_extended_api.py --step promotion",
                "status": "pending",
                "description": "Кампании, общая статистика и дневные поисковые фразы: показы, клики, расходы, корзины, заказы и позиции.",
            },
            {
                "key": "wb_search",
                "label": "WB · Поисковая аналитика",
                "script": "sync_km_wb_extended_api.py --step analytics",
                "status": "pending",
                "description": "Сводная видимость, позиции, переходы и товарная детализация за последние 7 дней; требуется подписка WB Jam.",
                "substeps": [
                    "POST /api/v2/search-report/report · сводный поисковый отчёт",
                    "POST /api/v2/search-report/table/details · детализация по товарам",
                ],
            },
            {
                "key": "wb_feedbacks_questions",
                "label": "WB · Отзывы и вопросы",
                "script": "sync_km_wb_extended_api.py --step communication",
                "status": "pending",
                "description": "Отзывы, ответы продавца и вопросы покупателей за выбранный период; требуется категория токена «Вопросы и отзывы».",
                "substeps": [
                    "GET /api/v1/new-feedbacks-questions · проверка новых событий",
                    "GET /api/v1/feedbacks · обработанные и необработанные отзывы",
                    "GET /api/v1/feedbacks/archive · архив обработанных отзывов",
                    "GET /api/v1/questions · отвеченные и неотвеченные вопросы",
                ],
            },
            {
                "key": "wb_finance",
                "label": "WB · Финансовые отчёты",
                "script": "sync_km_wb_extended_api.py --step finance --finance-period daily",
                "status": "pending",
                "description": "Дневные отчёты для ежедневной догрузки; недельные закрывающие отчёты запускаются отдельно с --finance-period weekly.",
            },
            {
                "key": "wb_views",
                "label": "WB · Витрины BI",
                "script": "sync_km_wb_api.py --step views",
                "status": "pending",
                "description": "Пересборка зависимых таблиц и materialized views после загрузки данных.",
            },
        ])
    if "avito" in enabled_marketplaces:
        if all(app.registered_client_credential(client["key"], key) for key in (
            "avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret",
        )):
            steps.append({
                "key": "avito_advertising",
                "label": "Avito · Рекламная статистика",
                "script": "sync_avito_ads.py",
                "status": "pending",
                "description": "Кампании, группы, объявления, баланс и дневная рекламная статистика за выбранный период.",
                "substeps": [
                    "Аккаунт · GET /ads/v1/account/{accountID}",
                    "Баланс · GET /ads/v1/account/{accountID}/balance",
                    "Кампании · POST /ads/v1/account/{accountID}/campaigns",
                    "Группы · POST /ads/v1/account/{accountID}/groups",
                    "Объявления · POST /ads/v1/account/{accountID}/creatives",
                    "Дневная статистика · POST /campaigns/{campaignID}/stats · окна до 100 дней",
                ],
            })
    if "lamoda" in enabled_marketplaces:
        steps.extend([
            {
                "key": "lamoda_orders",
                "label": "Lamoda · Заказы",
                "script": "sync_lamoda.py --step orders",
                "status": "pending",
                "description": "Исторические заказы и их товарные позиции за выбранный период.",
            },
            {
                "key": "lamoda_stock",
                "label": "Lamoda · Остатки",
                "script": "sync_lamoda.py --step stock",
                "status": "pending",
                "description": "Текущий снимок доступных остатков по товарам и складам Lamoda.",
            },
            {
                "key": "lamoda_catalog",
                "label": "Lamoda · Каталог",
                "script": "sync_lamoda.py --step catalog",
                "status": "pending",
                "description": "Номенклатуры, статусы и идентификаторы товаров Lamoda.",
            },
            {
                "key": "lamoda_prices",
                "label": "Lamoda · Цены и скидки",
                "script": "sync_lamoda.py --step prices",
                "status": "pending",
                "description": "Текущие цены и скидки из карточек товаров Seller API v2.",
            },
            {
                "key": "lamoda_promotions",
                "label": "Lamoda · Продвижение и акции",
                "script": "sync_lamoda.py --step promotions",
                "status": "pending",
                "description": "Список акций и их параметры; загрузчик использует только GET и ничего не меняет в кабинете.",
            },
            {
                "key": "lamoda_fbo_shipments",
                "label": "Lamoda · Поставки и приёмка FBO",
                "script": "sync_lamoda.py --step fbo_shipments",
                "status": "pending",
                "description": "Поставки на склады Lamoda и статусы приёмки.",
            },
            {
                "key": "lamoda_fbs_returns",
                "label": "Lamoda · Возвраты FBS",
                "script": "sync_lamoda.py --step fbs_returns",
                "status": "pending",
                "description": "Возвратные товары FBS и их статусы.",
            },
            {
                "key": "lamoda_finance",
                "label": "Lamoda · Расходы и финансовые документы",
                "script": "sync_lamoda.py --step finance",
                "status": "limited",
                "description": "Нужен отдельный экспорт Lamoda Seller/LAB: в Seller API v2 нет подтверждённого финансового метода. Расходы из заказов не подменяются расчётными значениями.",
            },
        ])
    if "yandex_market" in enabled_marketplaces:
        from yandex_market_history import SOURCES
        steps.extend({"key": key, "label": label, "script": f"sync_yandex_market.py --step {key}",
                      "status": "pending", "description": description}
                     for key, (label, _, description) in SOURCES.items())
    if selected_steps:
        selected = set(selected_steps)
        steps = [step for step in steps if step["key"] in selected]
    return steps


def _assortment_step_plan(client: dict, selected_marketplaces: list[str] | None = None) -> list[dict]:
    connected = set(client.get("marketplaces") or [])
    selected = set(selected_marketplaces or connected)
    steps: list[dict] = []
    if "ozon" in connected and "ozon" in selected:
        steps.append({
            "key": "ozon",
            "label": "Ozon · Ассортимент и характеристики",
            "script": "sync_ozon_assortment_soft.py",
            "status": "pending",
        })
    if "wb" in connected and "wb" in selected:
        steps.append({
            "key": "wb",
            "label": "WB · Ассортимент и характеристики",
            "script": "sync_wb_assortment.py",
            "status": "pending",
        })
    return steps


def _ordered_history_step_keys(available_steps: list[dict], selected_steps: list[str] | None = None) -> list[str]:
    if not selected_steps:
        return [step["key"] for step in available_steps]
    selected = set(selected_steps)
    return [step["key"] for step in available_steps if step["key"] in selected]


def _infer_history_completed_step_count(progress, step_total: int) -> int:
    if step_total <= 0:
        return 0
    try:
        progress_value = int(progress or 0)
    except (TypeError, ValueError):
        return 0
    progress_value = max(0, min(99, progress_value))
    if progress_value <= 5:
        return 0
    completed = int(((progress_value - 5) / 90) * step_total)
    return max(0, min(step_total - 1, completed))


def _history_resume_step_keys(
    client: dict,
    available_steps: list[dict],
    selected_steps: list[str] | None = None,
) -> list[str]:
    ordered = _ordered_history_step_keys(available_steps, selected_steps)
    if not ordered:
        return []
    runtime_steps = client.get("operation_steps") if isinstance(client.get("operation_steps"), list) else []
    if runtime_steps:
        done_keys = {
            step.get("key")
            for step in runtime_steps
            if step.get("status") == "done" and step.get("key") in ordered
        }
        return [key for key in ordered if key not in done_keys]
    completed_count = _infer_history_completed_step_count(client.get("operation_progress"), len(ordered))
    return ordered[completed_count:]


def _external_ozon_step_keys() -> list[str]:
    return [
        "ozon_assortment",
        "ozon_funnel",
        "ozon_stock",
        "ozon_advertising",
        "ozon_finance",
        "ozon_views",
    ]


def _active_avito_api_run(app, client: dict) -> dict | None:
    if "avito" not in set(client.get("marketplaces") or []):
        return None
    try:
        import psycopg2
    except Exception:
        return None
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    try:
        conn = psycopg2.connect(**config)
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.avito_ads_api_runs')")
            if cur.fetchone()[0] is None:
                return None
            cur.execute(
                """
                SELECT id, step, date_from, date_to, rows_loaded, started_at, requests_made
                FROM public.avito_ads_api_runs
                WHERE status = 'running'
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "step": row[1], "date_from": row[2], "date_to": row[3],
                "rows_loaded": row[4], "started_at": row[5], "requests_made": row[6],
            }
    finally:
        conn.close()


def _stop_active_avito_api_runs(app, client: dict, reason: str) -> int:
    if "avito" not in set(client.get("marketplaces") or []):
        return 0
    try:
        import psycopg2
    except Exception:
        return 0
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    conn = psycopg2.connect(**config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.avito_ads_api_runs')")
            if cur.fetchone()[0] is None:
                return 0
            cur.execute(
                """
                UPDATE public.avito_ads_api_runs
                SET status='stopped', finished_at=COALESCE(finished_at, now()),
                    details=details || %s::jsonb
                WHERE status='running'
                """,
                (json.dumps({"stop_reason": str(reason)[:500]}),),
            )
            stopped = cur.rowcount
        conn.commit()
        return stopped
    finally:
        conn.close()


def _unfinished_avito_history_step_keys(app, client: dict) -> set[str]:
    if "avito" not in set(client.get("marketplaces") or []):
        return set()
    try:
        import psycopg2
    except Exception:
        return set()
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    try:
        conn = psycopg2.connect(**config)
    except Exception:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.avito_ads_api_runs')")
            if cur.fetchone()[0] is None:
                return set()
            cur.execute(
                """SELECT status FROM public.avito_ads_api_runs
                   WHERE step='advertising' ORDER BY started_at DESC, id DESC LIMIT 1"""
            )
            row = cur.fetchone()
    except Exception:
        return set()
    finally:
        conn.close()
    return {"avito_advertising"} if row and str(row[0] or "").lower() in {"failed", "stopped", "running"} else set()

def _active_ozon_api_run(app, client: dict, selected_steps: list[str]) -> dict | None:
    if "ozon" not in set(client.get("marketplaces") or []):
        return None
    if not any(str(step or "").startswith("ozon_") for step in selected_steps):
        return None
    try:
        import psycopg2
    except Exception:
        return None
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    try:
        conn = psycopg2.connect(**config)
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.km_ozon_api_runs')")
            if cur.fetchone()[0] is None:
                return None
            cur.execute(
                """
                SELECT id, step, date_from, date_to, rows_loaded, checkpoint_offset, started_at, requests_made
                FROM public.km_ozon_api_runs
                WHERE status = 'running'
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "step": row[1],
                "date_from": row[2],
                "date_to": row[3],
                "rows_loaded": row[4],
                "checkpoint_offset": row[5],
                "started_at": row[6],
                "requests_made": row[7],
            }
    finally:
        conn.close()


def _stop_active_ozon_api_runs(app, client: dict, reason: str) -> int:
    if "ozon" not in set(client.get("marketplaces") or []):
        return 0
    try:
        import psycopg2
    except Exception:
        return 0
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    conn = psycopg2.connect(**config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.km_ozon_api_runs')")
            if cur.fetchone()[0] is None:
                return 0
            cur.execute(
                """
                UPDATE public.km_ozon_api_runs
                SET status = 'stopped', finished_at = COALESCE(finished_at, now()),
                    error = COALESCE(NULLIF(error, ''), %s)
                WHERE status = 'running'
                """,
                (reason,),
            )
            stopped = cur.rowcount
        conn.commit()
        return stopped
    finally:
        conn.close()


def _unfinished_ozon_history_step_keys(app, client: dict) -> set[str]:
    """Keep stopped Ozon API steps in a resumed history queue."""
    if "ozon" not in set(client.get("marketplaces") or []):
        return set()
    try:
        import psycopg2
    except Exception:
        return set()
    config = dict(app.read_db_config(client.get("key")))
    config["database"] = client.get("db_name")
    try:
        conn = psycopg2.connect(**config)
    except Exception:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (step) step, status
                FROM public.km_ozon_api_runs
                WHERE step IN ('funnel', 'stock', 'advertising', 'views')
                ORDER BY step, started_at DESC, id DESC
                """
            )
            latest = cur.fetchall()
    except Exception:
        return set()
    finally:
        conn.close()
    return {
        f"ozon_{step}"
        for step, status in latest
        if str(status or "").lower() in {"failed", "stopped"}
    }


def _external_ozon_progress(client: dict, active_run: dict) -> int:
    current = int(client.get("operation_progress") or 0)
    checkpoint = int(active_run.get("checkpoint_offset") or active_run.get("rows_loaded") or 0)
    if checkpoint > 0:
        current = max(current, 20)
    return max(1, min(95, current or 20))


def _external_ozon_message(active_run: dict) -> str:
    return (
        "Ozon API выгрузка продолжается: "
        f"run id {active_run.get('id')}, шаг {active_run.get('step')}, "
        f"checkpoint {active_run.get('checkpoint_offset') or 0}"
    )


def _sync_external_ozon_operation(client: dict, active_run: dict) -> None:
    _set_operation(
        client["key"],
        "history",
        "running",
        f"external_{active_run.get('step') or 'ozon'}",
        _external_ozon_progress(client, active_run),
        _external_ozon_message(active_run),
        "",
        history_date_from=active_run.get("date_from") or client.get("history_date_from") or None,
        history_date_to=active_run.get("date_to") or client.get("history_date_to") or None,
    )


def _external_ozon_runtime_payload(app, row: dict, active_run: dict) -> dict:
    steps = [dict(step) for step in _history_step_plan(app, row)]
    active_key = f"ozon_{active_run.get('step')}"
    seen_active = False
    for step in steps:
        key = step.get("key")
        if key == active_key:
            step["status"] = "running"
            seen_active = True
        elif key.startswith("ozon_") and not seen_active:
            step["status"] = "done"
        else:
            step["status"] = "pending"
    message = _external_ozon_message(active_run)
    return {
        **row,
        "operation_status": "running",
        "operation_stage": f"external_{active_run.get('step') or 'ozon'}",
        "operation_progress": _external_ozon_progress(row, active_run),
        "operation_message": message,
        "operation_error": "",
        "operation_logs": [{"time": _runtime_time(), "type": "output", "text": message}],
        "operation_steps": steps,
        "operation_pid": None,
        "operation_stop_requested": False,
        "operation_request_count": int(active_run.get("requests_made") or 0),
    }


def _update_history_runtime(client_key: str, message: str) -> None:
    has_zero_errors = bool(re.search(r"(?:errors|ошибок)\s*[=:]\s*0\b", message, re.I))
    kind = "success" if has_zero_errors or re.search(r"заверш[её]н|completed", message, re.I) else "error" if re.search(r"ошиб|error|failed", message, re.I) else "output"
    _append_history_log(client_key, message, kind)
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        if not runtime:
            return
        request_match = re.search(r"запросов\s+(\d+)", message, re.I)
        if request_match:
            runtime["request_count"] = max(int(runtime.get("request_count") or 0), int(request_match.group(1)))
        limited_match = re.search(r"SOURCE_LIMITED:\s*(yandex_[a-z_]+)", message)
        if limited_match:
            runtime.setdefault("limited_steps", set()).add(limited_match.group(1))
            for step in runtime.get("steps", []):
                if step["key"] == limited_match.group(1):
                    step["status"] = "limited"
        match = re.search(r"^ПРОГРЕСС:\s+(\d+)/(\d+).*?\|\s*([^|]+)", message)
        if not match:
            return
        steps = runtime.get("steps", [])
        total = int(match.group(2))
        if total != len(steps):
            return
        index = max(0, int(match.group(1)) - 1)
        step_completed = bool(
            re.search(r"^\[\d+/\d+\].*?:\s*completed\b", message, re.I)
            or re.search(r"\|\s*[^|]*заверш[её]н\s*\|\s*completed=\d+", message, re.I)
        )
        for step_index, step in enumerate(steps):
            if step["key"] in runtime.get("limited_steps", set()):
                step["status"] = "limited"
                continue
            if step_index < index:
                step["status"] = "done"
            elif step_index == index:
                step["status"] = "done" if step_completed else "running"


def _update_assortment_runtime(client_key: str, message: str) -> int:
    """Store one child-process line and derive monotonic overall progress."""
    _update_history_runtime(client_key, message)
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        if not runtime:
            return 0
        steps = runtime.get("steps", [])
        total = max(1, len(steps))
        request_match = re.search(r"(?:requests?|request|запросов)а?х?\s*[=: ]\s*(\d+)", message, re.I)
        if request_match:
            runtime["request_count"] = max(
                int(runtime.get("request_count") or 0),
                int(request_match.group(1)),
            )

        current_index = next(
            (index for index, step in enumerate(steps) if step.get("status") == "running"),
            next((index for index, step in enumerate(steps) if step.get("status") == "pending"), total - 1),
        )
        overall = re.search(r"^ПРОГРЕСС:\s+(\d+)/(\d+)", message)
        completed = re.search(r"^\[(\d+)/(\d+)\].*?completed", message, re.I)
        if overall and int(overall.group(2)) == total:
            current_index = max(0, min(total - 1, int(overall.group(1)) - 1))
            for index, step in enumerate(steps):
                step["status"] = "done" if index < current_index else "running" if index == current_index else "pending"
            candidate = 5 + (current_index / total) * 90
        elif completed and int(completed.group(2)) == total:
            completed_count = max(0, min(total, int(completed.group(1))))
            for index, step in enumerate(steps):
                step["status"] = "done" if index < completed_count else "pending"
            candidate = 5 + (completed_count / total) * 90
        else:
            market_key = "ozon" if re.search(r"\bOzon\b", message, re.I) else "wb" if re.search(r"\bWB\b", message, re.I) else ""
            if market_key:
                matched_index = next((index for index, step in enumerate(steps) if step.get("key") == market_key), None)
                if matched_index is not None:
                    current_index = matched_index
                    for index, step in enumerate(steps):
                        if index < current_index:
                            step["status"] = "done"
                        elif index == current_index and step.get("status") != "done":
                            step["status"] = "running"
            pct_match = re.search(r"step_pct\s*=\s*(\d+(?:\.\d+)?)", message, re.I)
            local_pct = max(0.0, min(100.0, float(pct_match.group(1)))) if pct_match else 0.0
            candidate = 5 + ((current_index + local_pct / 100) / total) * 90
        runtime["progress"] = max(int(runtime.get("progress") or 0), min(95, int(candidate)))
        return int(runtime["progress"])


def _history_progress(client_key: str, value: int | None = None) -> int:
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        if not runtime:
            return 0
        if value is not None:
            runtime["progress"] = max(int(runtime.get("progress") or 0), max(0, min(100, int(value))))
        return int(runtime.get("progress") or 0)


def _history_stop_requested(client_key: str) -> bool:
    with _LOCK:
        event = _HISTORY_STOP_EVENTS.get(client_key)
        return bool(event and event.is_set())


def _set_history_runtime_status(client_key: str, status: str) -> None:
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        if not runtime:
            return
        steps = runtime.get("steps", [])
        if status in {"completed", "partial"}:
            for step in steps:
                step["status"] = "limited" if step["key"] in runtime.get("limited_steps", set()) else "done"
        elif status in {"failed", "stopped"}:
            current = next((step for step in steps if step.get("status") == "running"), None)
            if current:
                current["status"] = status


def _terminate_process_tree(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _register_history_process(client_key: str, process: subprocess.Popen) -> None:
    with _LOCK:
        _HISTORY_PROCESSES[client_key] = process
        runtime = _HISTORY_RUNTIME.get(client_key)
        stop_requested = bool(runtime and runtime.get("stop_requested"))
        if runtime:
            runtime["pid"] = process.pid
    if stop_requested:
        _terminate_process_tree(process)


def _provision_worker(client_key: str) -> None:
    from client_onboarding import provision_client, required_credentials
    from client_registry import get_client, update_client_status

    app = _app()
    last_state = {"stage": "queued", "progress": 1}
    try:
        with app.client_registry_connection() as conn:
            client = get_client(conn, client_key)
        credentials = {
            key: app.registered_client_credential(client_key, key)
            for key in required_credentials(client["marketplaces"])
        }
        for optional in (
            "ozon_performance_client_id", "ozon_performance_client_secret",
            "avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret",
            "lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id",
            "yandex_market_api_key", "yandex_market_business_id", "yandex_market_campaign_id",
        ):
            value = app.registered_client_credential(client_key, optional)
            if value:
                credentials[optional] = value

        def progress(stage, value, message):
            last_state.update(stage=stage, progress=value)
            _set_operation(client_key, "provision", "running", stage, value, message)

        provision_client(client, credentials, app.read_db_config(app.DEFAULT_CLIENT), progress)
        with app.client_registry_connection() as conn:
            update_client_status(conn, client_key, "active")
            client = get_client(conn, client_key)
        app.apply_registered_client(client)
        _set_operation(
            client_key, "provision", "completed", "ready", 100,
            "Клиент подключён и добавлен в селектор",
        )
    except Exception as exc:
        _set_operation(
            client_key, "provision", "failed", last_state["stage"], last_state["progress"],
            "Подключение остановлено", str(exc),
        )
    finally:
        with _LOCK:
            _RUNNING.discard(client_key)


def start_provision(payload_data: dict) -> dict:
    from client_onboarding import (
        DEFAULT_CLIENTS_ROOT,
        default_reports,
        discover_yandex_market_accounts,
        required_credentials,
    )
    from client_registry import (
        get_client,
        normalize_client_payload,
        save_client,
        save_marketplace_accounts,
    )

    app = _app()
    marketplaces = payload_data.get("marketplaces") or [payload_data.get("marketplace")]
    marketplaces = [str(item or "").strip().lower() for item in marketplaces if item]
    supported_marketplaces = {"ozon", "wb", "avito", "lamoda", "yandex_market"}
    if not marketplaces or any(item not in supported_marketplaces for item in marketplaces):
        raise ValueError("Выберите поддерживаемый маркетплейс")
    raw_reports = payload_data.get("reports") if isinstance(payload_data.get("reports"), list) else []
    requested_reports = [str(item or "").strip() for item in raw_reports if str(item or "").strip()]
    draft = dict(payload_data)
    draft["marketplaces"] = marketplaces
    draft["reports"] = requested_reports or default_reports(marketplaces)
    draft["status"] = "paused"
    if not draft.get("root_path"):
        draft["root_path"] = str(
            DEFAULT_CLIENTS_ROOT / str(draft.get("label") or draft.get("key") or "").strip()
        )
    client = normalize_client_payload(draft, {item["id"] for item in app.ADMIN_REPORT_CATALOG})
    with app.client_registry_connection() as conn:
        existing = get_client(conn, client["key"])
    if existing and not requested_reports and existing.get("reports"):
        client["reports"] = list(existing.get("reports") or [])
    if existing and existing.get("status") == "active":
        raise ValueError("Клиент с таким ключом уже подключён")
    if not existing and client["key"] in app.ADMIN_CLIENTS:
        raise ValueError("Клиент с таким ключом уже существует в конфигурации")
    raw = payload_data.get("credentials") if isinstance(payload_data.get("credentials"), dict) else {}
    credentials = {
        key: str(value or "").strip()
        for key, value in raw.items()
        if str(value or "").strip()
    }
    if existing:
        for key in (
            *required_credentials(marketplaces),
            "ozon_performance_client_id", "ozon_performance_client_secret",
            "avito_ads_account_id", "avito_ads_client_id", "avito_ads_client_secret",
            "lamoda_client_id", "lamoda_client_secret", "lamoda_seller_id",
            "yandex_market_api_key", "yandex_market_business_id", "yandex_market_campaign_id",
        ):
            if not credentials.get(key):
                saved_value = app.registered_client_credential(client["key"], key)
                if saved_value:
                    credentials[key] = saved_value
    missing = [key for key in required_credentials(marketplaces) if not credentials.get(key)]
    if missing:
        raise ValueError("Заполните обязательные ключи: " + ", ".join(missing))
    yandex_accounts = []
    if "yandex_market" in marketplaces:
        yandex_accounts = discover_yandex_market_accounts(credentials["yandex_market_api_key"])
    with _LOCK:
        if client["key"] in _RUNNING:
            raise ValueError("Подключение этого клиента уже выполняется")
        _RUNNING.add(client["key"])
    try:
        with app.client_registry_connection() as conn:
            master_key = app.client_credentials_master_key(create=True)
            save_client(conn, client, credentials, master_key)
            if yandex_accounts:
                save_marketplace_accounts(conn, client["key"], "yandex_market", yandex_accounts)
    except Exception:
        with _LOCK:
            _RUNNING.discard(client["key"])
        raise
    app.apply_registered_client(client)
    _set_operation(
        client["key"], "provision", "running", "queued", 1,
        "Задача поставлена в очередь",
    )
    threading.Thread(
        target=_provision_worker,
        args=(client["key"],),
        daemon=True,
        name=f"onboard-{client['key']}",
    ).start()
    return payload(client["key"])


def _history_worker(
    client_key: str,
    date_from: str,
    date_to: str,
    selected_steps: list[str],
    overwrite: bool,
    marketplace: str,
    wb_stock_history_date_from: str,
    wb_stock_history_date_to: str,
    include_inactive_campaigns: bool,
    resume: bool,
) -> None:
    from client_onboarding import HistoryStopped, run_history
    from client_registry import get_client

    app = _app()
    try:
        with app.client_registry_connection() as conn:
            client = get_client(conn, client_key)

        def progress(stage, value, message):
            _history_progress(client_key, value)
            _update_history_runtime(client_key, message)
            try:
                _set_operation(
                    client_key, "history", "running", stage, value, message,
                    history_date_from=date_from, history_date_to=date_to,
                )
            except Exception as exc:
                _append_history_log(client_key, f"ПРЕДУПРЕЖДЕНИЕ: статус БД временно недоступен | {exc}", "warning")

        result = run_history(
            client,
            date_from,
            date_to,
            progress,
            process_hook=lambda process: _register_history_process(client_key, process),
            should_stop=lambda: _history_stop_requested(client_key),
            selected_steps=selected_steps,
            overwrite=overwrite,
            marketplaces=[marketplace],
            wb_stock_history_date_from=wb_stock_history_date_from or None,
            wb_stock_history_date_to=wb_stock_history_date_to or None,
            include_inactive_campaigns=include_inactive_campaigns,
            resume=resume,
        )
        partial = bool((result or {}).get("partial"))
        final_status = "partial" if partial else "completed"
        success_message = "Данные Яндекс Маркета загружены" if marketplace == "yandex_market" else "Выбранные разделы и витрины обновлены"
        final_message = "Загрузка завершена с ограничениями источника" if partial else success_message
        _set_history_runtime_status(client_key, final_status)
        _append_history_log(client_key, f"ИТОГ: {final_message}", "warning" if partial else "success")
        _set_operation(
            client_key, "history", final_status, "history_ready", 100,
            final_message,
            history_date_from=date_from, history_date_to=date_to,
        )
    except HistoryStopped:
        _set_history_runtime_status(client_key, "stopped")
        _append_history_log(client_key, "ИТОГ: остановлено пользователем", "warning")
        _set_operation(
            client_key, "history", "stopped", "stopped", _history_progress(client_key),
            "Историческая загрузка остановлена пользователем", "",
            history_date_from=date_from, history_date_to=date_to,
        )
    except Exception as exc:
        _set_history_runtime_status(client_key, "failed")
        _append_history_log(client_key, f"ИТОГ: ошибка | {exc}", "error")
        _set_operation(
            client_key, "history", "failed", "failed", _history_progress(client_key),
            "Историческая загрузка остановлена", str(exc),
            history_date_from=date_from, history_date_to=date_to,
        )
    finally:
        with _LOCK:
            _RUNNING.discard(client_key)
            _HISTORY_PROCESSES.pop(client_key, None)
            _HISTORY_STOP_EVENTS.pop(client_key, None)


def start_history(payload_data: dict) -> dict:
    from client_onboarding import validate_history_range, validate_wb_stock_history_range
    from client_registry import get_client

    app = _app()
    client_key = _onboarding_client_key(app, payload_data.get("client"))
    date_from = str(payload_data.get("date_from") or "").strip()
    date_to = str(payload_data.get("date_to") or "").strip()
    wb_stock_history_date_from = str(payload_data.get("wb_stock_history_date_from") or "").strip()
    wb_stock_history_date_to = str(payload_data.get("wb_stock_history_date_to") or "").strip()
    marketplace = str(payload_data.get("marketplace") or "").strip().lower()
    resume = bool(payload_data.get("resume"))
    overwrite = bool(payload_data.get("overwrite"))
    include_inactive_campaigns = bool(payload_data.get("include_inactive_campaigns"))
    if resume and overwrite:
        raise ValueError("Перезапись и продолжение незавершённой загрузки несовместимы. Запустите новую загрузку.")
    validate_history_range(date_from, date_to)
    with app.client_registry_connection() as conn:
        client = get_client(conn, client_key)
    if not client or client.get("status") != "active":
        raise ValueError("Сначала завершите подключение клиента")
    if marketplace not in {"ozon", "wb", "avito", "lamoda", "yandex_market"}:
        raise ValueError("Выберите маркетплейс для исторической загрузки")
    if marketplace not in set(client.get("marketplaces") or []):
        raise ValueError("Выбранный маркетплейс не подключён клиенту")
    if marketplace == "yandex_market":
        from yandex_market_history import enabled_stores
        if not app.registered_client_credential(client_key, "yandex_market_api_key") or not enabled_stores(client):
            raise ValueError("Проверьте API-ключ Яндекса и включите магазины для импорта в настройках клиента")
    with _LOCK:
        previous_runtime = _HISTORY_RUNTIME.get(client_key)
        if previous_runtime:
            client = {
                **client,
                "operation_steps": [dict(item) for item in previous_runtime.get("steps", [])],
                "operation_progress": max(
                    int(client.get("operation_progress") or 0),
                    int(previous_runtime.get("progress") or 0),
                ),
            }
    selected_marketplaces = [marketplace]
    available_steps = _history_step_plan(app, client, marketplaces=selected_marketplaces)
    available_keys = {step["key"] for step in available_steps}
    load_all = bool(payload_data.get("load_all"))
    raw_steps = payload_data.get("steps")
    requested_steps = [str(value or "").strip() for value in raw_steps] if isinstance(raw_steps, list) else []
    selected_steps = [] if load_all else list(dict.fromkeys(value for value in requested_steps if value))
    unknown_steps = set(selected_steps) - available_keys
    if unknown_steps:
        raise ValueError("Недоступные шаги: " + ", ".join(sorted(unknown_steps)))
    if resume and marketplace == "yandex_market":
        # Durable per-store windows, not old in-memory step statuses, decide what remains.
        selected_steps = selected_steps or [step["key"] for step in available_steps]
    elif resume:
        selected_steps = _history_resume_step_keys(client, available_steps, selected_steps or None)
        unfinished_steps = _unfinished_ozon_history_step_keys(app, client) if marketplace == "ozon" else set()
        if marketplace == "avito":
            unfinished_steps = _unfinished_avito_history_step_keys(app, client)
        if unfinished_steps:
            selected_steps = _ordered_history_step_keys(
                available_steps,
                [*selected_steps, *unfinished_steps],
            )
    elif not selected_steps:
        selected_steps = [step["key"] for step in available_steps]
    else:
        selected_steps = _ordered_history_step_keys(available_steps, selected_steps)
    steps = _history_step_plan(app, client, selected_steps, selected_marketplaces)
    if not steps:
        raise ValueError("Нет оставшихся разделов для продолжения" if resume else "Выберите хотя бы один шаг загрузки")
    if "wb_stock_history" in selected_steps:
        if not wb_stock_history_date_from or not wb_stock_history_date_to:
            raise ValueError("Для истории остатков WB выберите отдельный период")
        validate_wb_stock_history_range(wb_stock_history_date_from, wb_stock_history_date_to)
    else:
        wb_stock_history_date_from = ""
        wb_stock_history_date_to = ""
    active_ozon_run = _active_ozon_api_run(app, client, selected_steps)
    if active_ozon_run and client.get("operation_status") in {"failed", "stopped"}:
        _stop_active_ozon_api_runs(
            app,
            client,
            "Закрыто перед продолжением остановленной исторической загрузки",
        )
        active_ozon_run = _active_ozon_api_run(app, client, selected_steps)
    if active_ozon_run:
        raise ValueError(
            "Для клиента уже выполняется Ozon API выгрузка "
            f"(run id {active_ozon_run['id']}, шаг {active_ozon_run['step']}, "
            f"checkpoint {active_ozon_run['checkpoint_offset']}). "
            "Дождитесь завершения или остановите текущий процесс."
        )
    active_avito_run = _active_avito_api_run(app, client) if marketplace == "avito" else None
    if active_avito_run and client.get("operation_status") in {"failed", "stopped"}:
        _stop_active_avito_api_runs(
            app,
            client,
            "Закрыто перед продолжением остановленной исторической загрузки",
        )
        active_avito_run = _active_avito_api_run(app, client)
    if active_avito_run:
        raise ValueError(
            "Для клиента уже выполняется Avito Ads выгрузка "
            f"(run id {active_avito_run['id']}, период "
            f"{active_avito_run['date_from']} — {active_avito_run['date_to']}). "
            "Дождитесь завершения или остановите текущий процесс."
        )
    with _LOCK:
        if client_key in _RUNNING:
            raise ValueError("Для клиента уже выполняется операция")
        _RUNNING.add(client_key)
        _HISTORY_STOP_EVENTS[client_key] = threading.Event()
        action_label = "ПРОДОЛЖИТЬ" if resume else "ПЛАН"
        action_text = (
            f"{action_label}: период {date_from} — {date_to} | "
            f"{'оставшихся разделов' if resume else 'скриптов'} {len(steps)}"
            f"{' | история остатков WB ' + wb_stock_history_date_from + ' — ' + wb_stock_history_date_to if wb_stock_history_date_from else ''}"
            f"{' | ПЕРЕЗАПИСЬ API-ДАННЫХ' if overwrite else ''}"
            f"{' | ПОИСКОВЫЕ ФРАЗЫ СТАРЫХ РК' if include_inactive_campaigns else ''}"
        )
        _HISTORY_RUNTIME[client_key] = {
            "logs": [{"time": _runtime_time(), "type": "info", "text": action_text}],
            "steps": steps,
            "pid": None,
            "stop_requested": False,
            "request_count": 0,
            "progress": 1,
            "resume": resume,
            "overwrite": overwrite,
            "include_inactive_campaigns": include_inactive_campaigns,
            "wb_stock_history_date_from": wb_stock_history_date_from,
            "wb_stock_history_date_to": wb_stock_history_date_to,
        }
    _set_operation(
        client_key, "history", "running", "queued", 1,
        "Продолжение исторической загрузки поставлено в очередь" if resume else "Историческая загрузка поставлена в очередь",
        history_date_from=date_from, history_date_to=date_to,
    )
    threading.Thread(
        target=_history_worker,
        args=(
            client_key,
            date_from,
            date_to,
            selected_steps,
            overwrite,
            marketplace,
            wb_stock_history_date_from,
            wb_stock_history_date_to,
            include_inactive_campaigns,
            resume,
        ),
        daemon=True,
        name=f"history-{client_key}",
    ).start()
    return payload(client_key)


def stop_history(payload_data: dict) -> dict:
    app = _app()
    client_key = _onboarding_client_key(app, payload_data.get("client"))
    from client_registry import get_client

    with app.client_registry_connection() as conn:
        client = get_client(conn, client_key)
    with _LOCK:
        if client_key not in _RUNNING:
            raise ValueError("Для клиента нет активной операции")
        runtime = _HISTORY_RUNTIME.get(client_key)
        event = _HISTORY_STOP_EVENTS.get(client_key)
        process = _HISTORY_PROCESSES.get(client_key)
        if not runtime or not event:
            raise ValueError("Активная операция не поддерживает остановку")
        runtime["stop_requested"] = True
        event.set()
    _append_history_log(client_key, "КОМАНДА: запрошена остановка полного дерева процессов", "warning")
    _set_operation(client_key, "history", "running", "stopping", _history_progress(client_key), "Останавливаю скрипты…")
    _terminate_process_tree(process)
    if client:
        _stop_active_ozon_api_runs(
            app,
            client,
            "Остановлено пользователем из исторической загрузки",
        )
        _stop_active_avito_api_runs(
            app,
            client,
            "Остановлено пользователем из исторической загрузки",
        )
    return payload(client_key)


def _assortment_worker(client_key: str, marketplaces: list[str]) -> None:
    from client_registry import get_client

    app = _app()
    process: subprocess.Popen | None = None
    try:
        with app.client_registry_connection() as conn:
            client = get_client(conn, client_key)
        if not client:
            raise ValueError("Клиент не найден в реестре")
        project_root = Path(__file__).resolve().parents[1]
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-u",
            str(project_root / "scripts" / "sync_client_assortment.py"),
            "--client-key",
            client_key,
            "--database-name",
            client["db_name"],
            "--marketplaces",
            ",".join(marketplaces),
        ]
        env = {
            **os.environ,
            "DASHBOARD_CLIENT": client_key,
            "DASHBOARD_CLIENT_LABEL": client["label"],
            "DASHBOARD_DB_NAME": client["db_name"],
        }
        process = subprocess.Popen(
            command,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _register_history_process(client_key, process)
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            progress = _update_assortment_runtime(client_key, line)
            with _LOCK:
                runtime = _HISTORY_RUNTIME.get(client_key) or {}
                running_step = next(
                    (step.get("key") for step in runtime.get("steps", []) if step.get("status") == "running"),
                    "assortment",
                )
            _set_operation(
                client_key,
                "assortment",
                "running",
                f"assortment_{running_step}",
                progress,
                line[-600:],
            )
        return_code = process.wait()
        if _history_stop_requested(client_key):
            _set_history_runtime_status(client_key, "stopped")
            _append_history_log(client_key, "ИТОГ: soft-update остановлен пользователем; уже сохранённые raw-данны остались в БД", "warning")
            _set_operation(
                client_key,
                "assortment",
                "stopped",
                "stopped",
                _history_progress(client_key),
                "Обновление ассортимента остановлено",
            )
        elif return_code:
            raise RuntimeError(f"Скрипт soft-update завершился с кодом {return_code}")
        else:
            _set_history_runtime_status(client_key, "completed")
            _append_history_log(client_key, "ИТОГ: ассортимент и характеристики мягко обновлены; Seller API writes=0", "success")
            _set_operation(
                client_key,
                "assortment",
                "completed",
                "assortment_ready",
                100,
                "Ассортимент и характеристики обновлены без удалений",
            )
    except Exception as exc:
        if _history_stop_requested(client_key):
            _set_history_runtime_status(client_key, "stopped")
            _set_operation(
                client_key,
                "assortment",
                "stopped",
                "stopped",
                _history_progress(client_key),
                "Обновление ассортимента остановлено",
            )
        else:
            _set_history_runtime_status(client_key, "failed")
            _append_history_log(client_key, f"ИТОГ: ошибка soft-update | {exc}", "error")
            _set_operation(
                client_key,
                "assortment",
                "failed",
                "failed",
                _history_progress(client_key),
                "Обновление ассортимента остановлено",
                str(exc)[:1200],
            )
    finally:
        with _LOCK:
            _RUNNING.discard(client_key)
            _HISTORY_PROCESSES.pop(client_key, None)
            _HISTORY_STOP_EVENTS.pop(client_key, None)


def start_assortment(payload_data: dict) -> dict:
    from client_registry import get_client

    app = _app()
    requested_client = str(payload_data.get("client") or "").strip().lower()
    if not requested_client:
        raise ValueError("Не указан ключ клиента")
    client_key = app.normalize_client_key(requested_client)
    if client_key != requested_client:
        raise ValueError("Клиент недоступен в текущем контексте")
    with app.client_registry_connection() as conn:
        client = get_client(conn, client_key)
    if not client or client.get("status") != "active":
        raise ValueError("Клиент не подключён")
    connected = set(client.get("marketplaces") or [])
    raw_marketplaces = payload_data.get("marketplaces")
    requested = [str(value or "").strip().lower() for value in raw_marketplaces] if isinstance(raw_marketplaces, list) else []
    marketplaces = [value for value in ("ozon", "wb") if value in connected and (not requested or value in requested)]
    if not marketplaces:
        raise ValueError("Выберите подключённый маркетплейс")
    unknown = set(requested) - {"ozon", "wb"}
    if unknown:
        raise ValueError("Недоступные маркетплейсы: " + ", ".join(sorted(unknown)))
    missing: list[str] = []
    if "wb" in marketplaces and not app.registered_client_credential(client_key, "wb_api_token"):
        missing.append("WB API token")
    if "ozon" in marketplaces:
        if not app.registered_client_credential(client_key, "ozon_client_id"):
            missing.append("Ozon Client-Id")
        if not app.registered_client_credential(client_key, "ozon_api_key"):
            missing.append("Ozon Api-Key")
    if missing:
        raise ValueError("Не сохранены ключи: " + ", ".join(missing))
    steps = _assortment_step_plan(client, marketplaces)
    with _LOCK:
        if client_key in _RUNNING:
            raise ValueError("Для клиента уже выполняется операция")
        _RUNNING.add(client_key)
        _HISTORY_STOP_EVENTS[client_key] = threading.Event()
        _HISTORY_RUNTIME[client_key] = {
            "operation": "assortment",
            "logs": [{
                "time": _runtime_time(),
                "type": "info",
                "text": (
                    f"ПЛАН: soft-update {', '.join(value.upper() for value in marketplaces)} | "
                    "Seller API только чтение | в БД добавляются новые SKU и заполняются пустые поля"
                ),
            }],
            "steps": steps,
            "pid": None,
            "stop_requested": False,
            "request_count": 0,
            "progress": 1,
            "overwrite": overwrite,
        }
    try:
        _set_operation(
            client_key,
            "assortment",
            "running",
            "queued",
            1,
            "Мягкое обновление ассортимента поставлено в очередь",
        )
    except Exception:
        with _LOCK:
            _RUNNING.discard(client_key)
            _HISTORY_STOP_EVENTS.pop(client_key, None)
            _HISTORY_RUNTIME.pop(client_key, None)
        raise
    threading.Thread(
        target=_assortment_worker,
        args=(client_key, marketplaces),
        daemon=True,
        name=f"assortment-{client_key}",
    ).start()
    return payload(client_key)


def stop_assortment(payload_data: dict) -> dict:
    app = _app()
    requested_client = str(payload_data.get("client") or "").strip().lower()
    if not requested_client:
        raise ValueError("Не указан ключ клиента")
    client_key = app.normalize_client_key(requested_client)
    if client_key != requested_client:
        raise ValueError("Клиент недоступен в текущем контексте")
    with _LOCK:
        runtime = _HISTORY_RUNTIME.get(client_key)
        event = _HISTORY_STOP_EVENTS.get(client_key)
        process = _HISTORY_PROCESSES.get(client_key)
        if client_key not in _RUNNING or not runtime or runtime.get("operation") != "assortment" or not event:
            raise ValueError("Для клиента нет активного soft-update")
        runtime["stop_requested"] = True
        event.set()
    _append_history_log(client_key, "КОМАНДА: остановить soft-update и всё дерево дочерних процессов", "warning")
    _set_operation(
        client_key,
        "assortment",
        "running",
        "stopping",
        _history_progress(client_key),
        "Останавливаю soft-update…",
    )
    _terminate_process_tree(process)
    return payload(client_key)




