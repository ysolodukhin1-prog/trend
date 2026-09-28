from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
MIGRATION_PATHS = (
    PROJECT_ROOT / "migrations" / "20260802_add_km_category_market_trends.sql",
    PROJECT_ROOT / "migrations" / "20260802_add_km_sales_planning_daily_and_promotion.sql",
)
TARGET_DB = "km_trade_products"
API_BASE = "https://mpstats.io/api/analytics/v1/oz"
DEFAULT_TOKEN_SOURCE = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Парсеры\Парсер позиций по ключам\Ozon\ozon_parser.py"
)

sys.path.insert(0, str(DASHBOARD_ROOT))
import app  # noqa: E402


def load_token(token_source: Path) -> str:
    token = os.getenv("MPSTATS_TOKEN", "").strip()
    if token:
        return token
    if not token_source.exists():
        raise FileNotFoundError(
            "Токен MPStats не найден: задайте MPSTATS_TOKEN или доступный --token-source"
        )
    source = token_source.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^\s*TOKEN\s*=\s*([\"'])(.+?)\1", source, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"В {token_source} не найдено присваивание TOKEN")
    return match.group(2)


def connect_km():
    config = {**app.read_db_config("km_trade"), "database": TARGET_DB}
    conn = psycopg2.connect(**config, cursor_factory=RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("SELECT current_database() AS name")
        actual = cur.fetchone()["name"]
    if actual != TARGET_DB:
        conn.close()
        raise RuntimeError(f"Защита клиента: ожидалась {TARGET_DB}, открылась {actual}")
    return conn


def apply_migration() -> None:
    with connect_km() as conn, conn.cursor() as cur:
        for migration_path in MIGRATION_PATHS:
            cur.execute(migration_path.read_text(encoding="utf-8"))


def request_json(
    url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    attempts: int = 5,
) -> Any:
    query = urllib.parse.urlencode(params or {})
    full_url = f"{url}?{query}" if query else url
    data = json.dumps(body).encode("utf-8") if body is not None else None
    method = "POST" if body is not None else "GET"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            full_url,
            data=data,
            method=method,
            headers={"X-Mpstats-TOKEN": token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts:
                message = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"MPStats HTTP {exc.code}: {message[:500]}") from exc
            wait_seconds = 45 + attempt * 15
            remaining = wait_seconds
            while remaining > 0:
                print(
                    f"ПРОГРЕСС: rate-limit | попытка {attempt}/{attempts} | "
                    f"повтор через {remaining} сек",
                    flush=True,
                )
                pause = min(10, remaining)
                time.sleep(pause)
                remaining -= pause
        except urllib.error.URLError as exc:
            if attempt == attempts:
                raise RuntimeError(f"MPStats network error: {exc}") from exc
            time.sleep(min(20, 2**attempt))
    raise RuntimeError("MPStats retry loop exhausted")


def candidate_skus(limit_per_category: int) -> dict[str, list[str]]:
    with connect_km() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (sku)
                    sku::text AS sku,
                    coalesce(nullif(kategoriya, ''), 'Без категории') AS category_name,
                    imported_at,
                    id
                FROM public.ozon_products
                WHERE nullif(sku, '') IS NOT NULL
                ORDER BY sku, imported_at DESC, id DESC
            )
            SELECT category_name, sku
            FROM latest
            ORDER BY category_name, sku
            """
        )
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in cur.fetchall():
            category = str(row["category_name"] or "Без категории")
            if category == "Без категории":
                continue
            if len(grouped[category]) < limit_per_category:
                grouped[category].append(str(row["sku"]))
    return dict(grouped)


def fetch_niche_id(sku: str, token: str) -> int | None:
    payload = request_json(f"{API_BASE}/items/{sku}/full", token)
    niche = payload.get("niche") if isinstance(payload, dict) else None
    if not isinstance(niche, dict) or not niche.get("id"):
        return None
    return int(niche["id"])


def fetch_niche_name(niche_id: int, token: str) -> str:
    payload = request_json(f"{API_BASE}/niche/{niche_id}", token)
    return str(payload.get("name") or "").strip() if isinstance(payload, dict) else ""


def fetch_trends(niche_id: int, token: str) -> list[dict[str, Any]]:
    payload = request_json(
        f"{API_BASE}/niche/trends",
        token,
        params={
            "path": niche_id,
            "trends_by": "week",
            "fbs": 0,
            "currency": "RUB",
        },
        body={},
    )
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def fetch_daily_trends(
    niche_id: int,
    token: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    payload = request_json(
        f"{API_BASE}/niche/by_date",
        token,
        params={
            "path": niche_id,
            "groupBy": "day",
            "d1": date_from.isoformat(),
            "d2": date_to.isoformat(),
            "fbs": 0,
            "currency": "RUB",
        },
        body={},
    )
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def as_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def upsert_category_rows(
    category_name: str,
    niche_id: int,
    niche_name: str,
    rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
) -> int:
    fetched_at = datetime.now(timezone.utc)
    values = []
    for row in rows:
        try:
            period_start = date.fromisoformat(str(row.get("date") or "")[:10])
            period_end = date.fromisoformat(str(row.get("end_date") or row.get("date") or "")[:10])
        except ValueError:
            continue
        if period_end < date_from or period_start > date_to:
            continue
        values.append(
            (
                "ozon",
                category_name,
                niche_id,
                niche_name,
                period_start,
                period_end,
                as_number(row.get("sales")),
                as_number(row.get("revenue")),
                as_number(row.get("items")),
                as_number(row.get("items_with_sells")),
                as_number(row.get("brands")),
                as_number(row.get("sellers")),
                "MPStats Analytics API · Ozon niche/trends",
                fetched_at,
            )
        )
    if not values:
        return 0
    with connect_km() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.km_category_market_trends (
                marketplace, category_name, mpstats_niche_id, mpstats_niche_name,
                period_start, period_end, sales_qty, revenue_rub, items_qty,
                items_with_sales_qty, brands_qty, sellers_qty, source_name, fetched_at
            ) VALUES %s
            ON CONFLICT (marketplace, category_name, period_start) DO UPDATE SET
                mpstats_niche_id = EXCLUDED.mpstats_niche_id,
                mpstats_niche_name = EXCLUDED.mpstats_niche_name,
                period_end = EXCLUDED.period_end,
                sales_qty = EXCLUDED.sales_qty,
                revenue_rub = EXCLUDED.revenue_rub,
                items_qty = EXCLUDED.items_qty,
                items_with_sales_qty = EXCLUDED.items_with_sales_qty,
                brands_qty = EXCLUDED.brands_qty,
                sellers_qty = EXCLUDED.sellers_qty,
                source_name = EXCLUDED.source_name,
                fetched_at = EXCLUDED.fetched_at
            """,
            values,
            page_size=500,
        )
    return len(values)


def upsert_daily_rows(
    category_name: str,
    niche_id: int,
    niche_name: str,
    rows: list[dict[str, Any]],
    date_from: date,
    date_to: date,
) -> int:
    fetched_at = datetime.now(timezone.utc)
    values = []
    for row in rows:
        try:
            report_date = date.fromisoformat(str(row.get("period") or "")[:10])
        except ValueError:
            continue
        if report_date < date_from or report_date > date_to:
            continue
        values.append(
            (
                "ozon",
                category_name,
                niche_id,
                niche_name,
                report_date,
                as_number(row.get("sales")),
                as_number(row.get("revenue")),
                as_number(row.get("balance")),
                as_number(row.get("items")),
                as_number(row.get("items_with_sells")),
                as_number(row.get("brands")),
                as_number(row.get("sellers")),
                as_number(row.get("avg_sale_price")),
                "MPStats Analytics API · Ozon niche/by_date",
                fetched_at,
            )
        )
    if not values:
        return 0
    with connect_km() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.km_category_market_daily (
                marketplace, category_name, mpstats_niche_id, mpstats_niche_name,
                report_date, sales_qty, revenue_rub, balance_qty, items_qty,
                items_with_sales_qty, brands_qty, sellers_qty,
                average_sale_price_rub, source_name, fetched_at
            ) VALUES %s
            ON CONFLICT (marketplace, category_name, report_date) DO UPDATE SET
                mpstats_niche_id = EXCLUDED.mpstats_niche_id,
                mpstats_niche_name = EXCLUDED.mpstats_niche_name,
                sales_qty = EXCLUDED.sales_qty,
                revenue_rub = EXCLUDED.revenue_rub,
                balance_qty = EXCLUDED.balance_qty,
                items_qty = EXCLUDED.items_qty,
                items_with_sales_qty = EXCLUDED.items_with_sales_qty,
                brands_qty = EXCLUDED.brands_qty,
                sellers_qty = EXCLUDED.sellers_qty,
                average_sale_price_rub = EXCLUDED.average_sale_price_rub,
                source_name = EXCLUDED.source_name,
                fetched_at = EXCLUDED.fetched_at
            """,
            values,
            page_size=500,
        )
    return len(values)


def refresh_materialized_view() -> dict[str, Any]:
    with connect_km() as conn, conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW public.km_category_market_trends_mv")
        cur.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT category_name) AS categories,
                   min(month_start) AS date_from,
                   max(source_period_to) AS date_to
            FROM public.km_category_market_trends_mv
            WHERE marketplace = 'ozon'
            """
        )
        return dict(cur.fetchone())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Синхронизировать сезонность категорий KM Trade из MPStats Ozon."
    )
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--sku-probes", type=int, default=5)
    parser.add_argument("--daily-days", type=int, default=370)
    parser.add_argument("--request-pause", type=float, default=1.2)
    parser.add_argument("--token-source", type=Path, default=DEFAULT_TOKEN_SOURCE)
    args = parser.parse_args()

    today = date.today()
    date_to = date.fromisoformat(args.date_to) if args.date_to else today - timedelta(days=1)
    date_from = date.fromisoformat(args.date_from) if args.date_from else date_to - timedelta(days=730)
    daily_from = max(date_from, date_to - timedelta(days=max(args.daily_days, 1) - 1))
    token = load_token(args.token_source)
    categories = candidate_skus(max(1, args.sku_probes))
    total_categories = len(categories)
    planned_requests = sum(len(skus) + 3 for skus in categories.values())
    print(
        "ПЛАН: "
        f"категорий {total_categories} | MPStats-запросов до {planned_requests} | "
        f"недельное окно {date_from.isoformat()}..{date_to.isoformat()} | "
        f"дневное окно {daily_from.isoformat()}..{date_to.isoformat()} | "
        f"до {max(1, args.sku_probes)} SKU для выбора доминирующей ниши | "
        f"пауза между запросами {max(args.request_pause, 0):.1f} сек | "
        "после загрузки — REFRESH MATERIALIZED VIEW. Токен в лог не выводится.",
        flush=True,
    )

    started = time.monotonic()
    apply_migration()
    request_no = 0
    imported_weekly_rows = 0
    imported_daily_rows = 0
    errors: list[str] = []
    mappings: dict[str, dict[str, Any]] = {}

    for category_index, (category, skus) in enumerate(categories.items(), start=1):
        niche_votes: Counter[int] = Counter()
        probe_errors = 0
        for sku in skus:
            request_no += 1
            try:
                niche_id = fetch_niche_id(sku, token)
                if niche_id:
                    niche_votes[niche_id] += 1
            except Exception as exc:  # noqa: BLE001 - bounded source probe
                probe_errors += 1
                errors.append(f"{category}: SKU {sku}: {exc}")
            elapsed = time.monotonic() - started
            pct = request_no / max(planned_requests, 1) * 100
            eta = elapsed / max(request_no, 1) * max(planned_requests - request_no, 0)
            print(
                f"ПРОГРЕСС: {request_no}/{planned_requests} ({pct:.1f}%) | "
                f"категория {category_index}/{total_categories}: {category} | SKU {sku} | "
                f"ниш найдено {sum(niche_votes.values())}, ошибок {len(errors)} | "
                f"elapsed {elapsed:.1f}s | ETA {eta:.1f}s",
                flush=True,
            )
            time.sleep(max(args.request_pause, 0))

        if not niche_votes:
            errors.append(f"{category}: MPStats не вернул нишу ни по одному SKU")
            print(
                f"[{category_index}/{total_categories}] {category}: пропуск, ниша не найдена, "
                f"ошибок проб {probe_errors}",
                flush=True,
            )
            continue

        niche_id, support = niche_votes.most_common(1)[0]
        try:
            request_no += 1
            niche_name = fetch_niche_name(niche_id, token)
            elapsed = time.monotonic() - started
            print(
                f"ПРОГРЕСС: {request_no}/{planned_requests} ({request_no / max(planned_requests, 1) * 100:.1f}%) | "
                f"категория {category_index}/{total_categories}: {category} | ниша {niche_id} | "
                f"метаданные ниши получены | elapsed {elapsed:.1f}s",
                flush=True,
            )
            time.sleep(max(args.request_pause, 0))
            request_no += 1
            trend_rows = fetch_trends(niche_id, token)
            weekly_saved = upsert_category_rows(
                category,
                niche_id,
                niche_name,
                trend_rows,
                date_from,
                date_to,
            )
            imported_weekly_rows += weekly_saved
            elapsed = time.monotonic() - started
            print(
                f"ПРОГРЕСС: {request_no}/{planned_requests} ({request_no / max(planned_requests, 1) * 100:.1f}%) | "
                f"категория {category_index}/{total_categories}: {category} | недель сохранено {weekly_saved} | "
                f"накоплено недель {imported_weekly_rows} | elapsed {elapsed:.1f}s",
                flush=True,
            )
            time.sleep(max(args.request_pause, 0))
            request_no += 1
            daily_rows = fetch_daily_trends(niche_id, token, daily_from, date_to)
            daily_saved = upsert_daily_rows(
                category,
                niche_id,
                niche_name,
                daily_rows,
                daily_from,
                date_to,
            )
            imported_daily_rows += daily_saved
            mappings[category] = {
                "niche_id": niche_id,
                "niche_name": niche_name,
                "support": support,
                "probes": len(skus),
                "weekly_rows": weekly_saved,
                "daily_rows": daily_saved,
            }
            print(
                f"[{category_index}/{total_categories}] {category}: ниша {niche_id} "
                f"«{niche_name}», подтверждение {support}/{len(skus)}, "
                f"недель {weekly_saved}, дней {daily_saved}, "
                f"ошибок {probe_errors}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - continue other categories
            errors.append(f"{category}: trends: {exc}")
            print(
                f"[{category_index}/{total_categories}] {category}: ошибка загрузки тренда: {exc}",
                flush=True,
            )

    summary = refresh_materialized_view()
    elapsed = time.monotonic() - started
    print(
        "ИТОГ: "
        f"категорий загружено {len(mappings)}/{total_categories} | "
        f"исходных недель {imported_weekly_rows} | дневных строк {imported_daily_rows} | "
        f"matview строк {summary.get('rows', 0)} | "
        f"ошибок {len(errors)} | период {summary.get('date_from')}..{summary.get('date_to')} | "
        f"output_db={TARGET_DB} | elapsed {elapsed:.1f}s | "
        f"partial={'true' if errors else 'false'}",
        flush=True,
    )
    if errors:
        for error in errors[:20]:
            print(f"ОШИБКА: {error}", flush=True)
    return 1 if not mappings else 0


if __name__ == "__main__":
    raise SystemExit(main())
