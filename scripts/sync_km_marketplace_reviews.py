#!/usr/bin/env python3
"""Synchronize one client's marketplace reviews from WB and Ozon Seller APIs."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import execute_values
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "20260814_marketplace_reviews.sql"
WB_FEEDBACKS_URL = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks"
WB_ARCHIVE_URL = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks/archive"
OZON_REVIEW_URL = "https://api-seller.ozon.ru/v2/review/list"

sys.path.insert(0, str(DASHBOARD_ROOT))
import app as dashboard_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Синхронизация отзывов клиента из API кабинетов WB/Ozon.")
    parser.add_argument(
        "--client-key",
        default=os.environ.get("DASHBOARD_CLIENT", "km_trade"),
        help="Ключ клиента из реестра кабинетов (по умолчанию DASHBOARD_CLIENT).",
    )
    parser.add_argument(
        "--database-name",
        default=os.environ.get("DASHBOARD_DB_NAME", ""),
        help="Имя изолированной БД клиента (по умолчанию DASHBOARD_DB_NAME).",
    )
    parser.add_argument("--marketplace", choices=("all", "wb", "ozon"), default="all")
    parser.add_argument("--wb-page-size", type=int, default=5000)
    parser.add_argument("--ozon-page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=0, help="0 — все страницы; положительное значение — безопасный тестовый лимит.")
    parser.add_argument("--request-delay", type=float, default=0.36)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--rate-limit-delay", type=float, default=60.0)
    return parser.parse_args()


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()


def wb_token(client_key: str) -> str:
    env_name = dashboard_app.wb_api_token_env(client_key)
    env_values = dashboard_app.read_app_env_file()
    token = (
        # A per-client encrypted credential is authoritative.  The legacy
        # shared WB_API_TOKEN is only a fallback for clients that have not
        # been migrated to the registry yet.
        dashboard_app.registered_client_credential(client_key, "wb_api_token")
        or os.environ.get(env_name)
        or env_values.get(env_name)
        or ""
    )
    token = str(token).strip()
    if not token:
        raise RuntimeError(f"WB token is not configured in {env_name} or the client registry")
    return token


def ozon_credentials(client_key: str) -> tuple[str, str]:
    client_id, api_key = dashboard_app.ozon_seo_credentials_value(client_key)
    if not client_id or not api_key:
        raise RuntimeError(f"Ozon Seller credentials for {client_key} are not configured")
    return str(client_id).strip(), str(api_key).strip()


def wait_with_progress(seconds: float, reason: str) -> None:
    remaining = max(1, int(round(seconds)))
    while remaining > 0:
        print(f"ЛИМИТ: {reason} | ожидание {remaining} сек.", flush=True)
        step = min(10, remaining)
        time.sleep(step)
        remaining -= step


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int,
    rate_limit_delay: float,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    for attempt in range(1, max(1, retries) + 1):
        response = session.request(method, url, params=params, json=body, timeout=90)
        if response.status_code == 429:
            if attempt >= retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After") or ""
            try:
                requested = float(retry_after)
            except ValueError:
                requested = rate_limit_delay
            wait_with_progress(min(max(rate_limit_delay, requested), 300), f"429 {url.split('/')[2]}")
            continue
        if 500 <= response.status_code < 600 and attempt < retries:
            wait_with_progress(min(2 ** attempt, 30), f"HTTP {response.status_code} {url.split('/')[2]}")
            continue
        if response.status_code == 403:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = str(payload.get("detail") or payload.get("message") or "access denied")
            raise PermissionError(f"{url.split('/')[2]}: {detail}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected {url.split('/')[2]} response type: {type(payload).__name__}")
        return payload
    raise RuntimeError(f"Request retries exhausted: {url.split('/')[2]}")


def fetch_wb_reviews(args: argparse.Namespace, client_key: str, progress) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"Authorization": wb_token(client_key), "Accept": "application/json"})
    page_size = min(5000, max(1, args.wb_page_size))
    all_items: dict[str, dict[str, Any]] = {}
    page_number = 0
    streams = [
        ("неотвеченные", WB_FEEDBACKS_URL, {"isAnswered": "false", "order": "dateDesc"}),
        ("отвеченные", WB_FEEDBACKS_URL, {"isAnswered": "true", "order": "dateDesc"}),
        ("архив", WB_ARCHIVE_URL, {"order": "dateDesc"}),
    ]
    for stream_name, url, base_params in streams:
        skip = 0
        while True:
            if args.max_pages and page_number >= args.max_pages:
                return list(all_items.values())
            params = {**base_params, "take": page_size, "skip": skip}
            payload = request_json(
                session,
                "GET",
                url,
                retries=args.retries,
                rate_limit_delay=args.rate_limit_delay,
                params=params,
            )
            data = payload.get("data") or {}
            rows = data.get("feedbacks") or [] if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            page_number += 1
            for row in rows:
                if not isinstance(row, dict):
                    continue
                source_id = str(row.get("id") or "").strip()
                if source_id:
                    all_items[source_id] = row
            progress("WB", page_number, f"{stream_name}, skip={skip}", len(rows), len(all_items))
            if len(rows) < page_size:
                break
            skip += len(rows)
            time.sleep(max(0, args.request_delay))
    return list(all_items.values())


def fetch_ozon_reviews(args: argparse.Namespace, client_key: str, progress) -> list[dict[str, Any]]:
    client_id, api_key = ozon_credentials(client_key)
    session = requests.Session()
    session.headers.update(
        {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    )
    page_size = min(100, max(20, args.ozon_page_size))
    last_id = ""
    page_number = 0
    all_items: dict[str, dict[str, Any]] = {}
    while True:
        if args.max_pages and page_number >= args.max_pages:
            break
        body: dict[str, Any] = {"limit": page_size, "sort_dir": "DESC"}
        if last_id:
            body["last_id"] = last_id
        payload = request_json(
            session,
            "POST",
            OZON_REVIEW_URL,
            retries=args.retries,
            rate_limit_delay=args.rate_limit_delay,
            body=body,
        )
        rows = payload.get("reviews") or (payload.get("result") or {}).get("reviews") or []
        if not isinstance(rows, list):
            rows = []
        page_number += 1
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("id") or "").strip()
            if source_id:
                all_items[source_id] = row
        progress("OZON", page_number, f"cursor={last_id[:12] or 'start'}", len(rows), len(all_items))
        next_id = str(payload.get("last_id") or (payload.get("result") or {}).get("last_id") or "")
        has_next = bool(payload.get("has_next") if "has_next" in payload else (payload.get("result") or {}).get("has_next"))
        if not rows or not has_next or not next_id or next_id == last_id:
            break
        last_id = next_id
        time.sleep(max(0, args.request_delay))
    return list(all_items.values())


def load_product_lookup(conn, marketplace: str) -> dict[str, dict[str, str]]:
    if marketplace == "wb":
        query = """
            SELECT artikul_wb::text AS product_id, COALESCE(artikul_prodavtsa, '') AS seller_article,
                   COALESCE(naimenovanie, '') AS product_name,
                   COALESCE(seller_category_name, kategoriya_prodavtsa, '') AS category_name,
                   COALESCE(brend, '') AS brand
            FROM public.products WHERE NULLIF(BTRIM(artikul_wb), '') IS NOT NULL
        """
    else:
        query = """
            SELECT sku::text AS product_id, COALESCE(artikul, '') AS seller_article,
                   COALESCE(nazvanie_tovara, '') AS product_name,
                   COALESCE(kategoriya, '') AS category_name, COALESCE(brend, '') AS brand
            FROM public.ozon_products WHERE NULLIF(BTRIM(sku), '') IS NOT NULL
        """
    with conn.cursor() as cur:
        cur.execute(query)
        return {str(row["product_id"]): dict(row) for row in cur.fetchall()}


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def parse_rating(value: Any) -> float | None:
    try:
        rating = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return rating if 0 <= rating <= 5 else None


def wb_product(raw: dict[str, Any], lookup: dict[str, dict[str, str]]) -> tuple[str, dict[str, str]]:
    details = raw.get("productDetails") or {}
    product_id = str(details.get("nmId") or raw.get("nmId") or "").strip()
    base = dict(lookup.get(product_id) or {})
    base.update(
        {
            "seller_article": str(details.get("supplierArticle") or base.get("seller_article") or "").strip(),
            "product_name": str(details.get("productName") or base.get("product_name") or "").strip(),
            "category_name": str(details.get("subjectName") or base.get("category_name") or "").strip(),
            "brand": str(details.get("brandName") or base.get("brand") or "").strip(),
        }
    )
    return product_id, base


def normalized_row(marketplace: str, raw: dict[str, Any], lookup: dict[str, dict[str, str]], synced_at: datetime) -> tuple[Any, ...]:
    source_id = str(raw.get("id") or "").strip()
    if marketplace == "wb":
        product_id, product = wb_product(raw, lookup)
        answer = raw.get("answer")
        answer_text = str(answer.get("text") if isinstance(answer, dict) else answer or "").strip()
        answered = bool(answer_text)
        photos = raw.get("photoLinks") or []
        has_photo = bool(photos) if isinstance(photos, list) else None
        status = str(raw.get("orderStatus") or "").strip()
        review_date = parse_date(raw.get("createdDate") or raw.get("date"))
        rating = parse_rating(raw.get("productValuation") or raw.get("valuation"))
        text = str(raw.get("text") or "").strip()
        pros = str(raw.get("pros") or "").strip()
        cons = str(raw.get("cons") or "").strip()
        likes = None
        answer_available = True
    else:
        product_id = str(raw.get("sku") or "").strip()
        product = dict(lookup.get(product_id) or {})
        answer_status = str(raw.get("status") or "").strip().upper()
        answer_available = bool(answer_status)
        answered = answer_status == "PROCESSED" if answer_available else None
        answer_text = ""
        has_photo = bool(int(raw.get("photos_amount") or 0))
        status = str(raw.get("order_status") or "").strip()
        review_date = parse_date(raw.get("published_at"))
        rating = parse_rating(raw.get("rating"))
        text = str(raw.get("text") or "").strip()
        pros = ""
        cons = ""
        likes = None
    if not source_id:
        source_id = hashlib.sha256(
            "|".join([marketplace, product_id, str(review_date or ""), str(rating or ""), text]).encode("utf-8")
        ).hexdigest()
    review_key = f"{marketplace}:{source_id}"
    return (
        review_key, source_id, marketplace, product_id,
        product.get("seller_article", ""), product.get("product_name", ""),
        product.get("category_name", ""), product.get("brand", ""),
        review_date, rating, text, pros, cons, answer_text, answer_available,
        answered, has_photo, likes, status, f"{marketplace}_seller_api", synced_at,
    )


INSERT_SQL = """
    INSERT INTO public.marketplace_reviews (
        review_key, source_review_id, marketplace, product_id, seller_article,
        product_name, category_name, brand, review_date, rating, review_text,
        pros, cons, answer_text, answer_available, answered, has_photo, likes,
        order_status, source, source_synced_at
    ) VALUES %s
"""


def replace_marketplace(conn, marketplace: str, raw_rows: Iterable[dict[str, Any]]) -> int:
    lookup = load_product_lookup(conn, marketplace)
    synced_at = datetime.now(timezone.utc)
    rows = [normalized_row(marketplace, raw, lookup, synced_at) for raw in raw_rows]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.marketplace_reviews WHERE marketplace = %s", (marketplace,))
        if rows:
            execute_values(cur, INSERT_SQL, rows, page_size=1000)
    conn.commit()
    return len(rows)


def create_run(conn, marketplaces: list[str]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.marketplace_review_sync_runs (marketplaces, products_total) VALUES (%s, %s) RETURNING run_id",
            (marketplaces, len(marketplaces)),
        )
        run_id = int(cur.fetchone()[0])
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, processed: int, loaded: int, failed: int, errors: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.marketplace_review_sync_runs
            SET finished_at = now(), status = %s, products_processed = %s,
                reviews_loaded = %s, products_failed = %s, error_summary = NULLIF(%s, '')
            WHERE run_id = %s
            """,
            (status, processed, loaded, failed, "; ".join(errors)[:2000], run_id),
        )
    conn.commit()


def main() -> int:
    args = parse_args()
    # This script is also launched as a fresh child process by the common daily
    # pipeline.  At that point app.py contains only its static clients until
    # the registry is hydrated; otherwise a registered client such as TOPTOP
    # or LERA NENA is silently normalised to the default Gloria profile.
    hydrate = getattr(dashboard_app, "hydrate_registered_clients", None)
    if callable(hydrate):
        hydrate()
    client_key = dashboard_app.normalize_client_key(args.client_key)
    marketplaces = ["wb", "ozon"] if args.marketplace == "all" else [args.marketplace]
    started = time.monotonic()
    print(
        "ПЛАН: "
        f"клиент={client_key} | площадки={','.join(marketplaces)} | "
        f"WB страницы до {min(5000, max(1, args.wb_page_size))} отзывов | "
        f"Ozon страницы до {min(100, max(20, args.ozon_page_size))} отзывов | "
        f"API read-only | пауза={args.request_delay:.2f}с | 429 ожидание от {args.rate_limit_delay:.0f}с",
        flush=True,
    )
    config = dashboard_app.read_db_config(client_key)
    if args.database_name:
        config["database"] = args.database_name
    loaded = 0
    processed = 0
    failed = 0
    errors: list[str] = []
    request_count = 0

    def progress(marketplace: str, page: int, window: str, batch_size: int, accumulated: int) -> None:
        nonlocal request_count
        request_count += 1
        elapsed = time.monotonic() - started
        print(
            f"ПРОГРЕСС: запрос={request_count} | {marketplace} страница={page} ({window}) | "
            f"пачка={batch_size}, накоплено={accumulated} | elapsed={elapsed:.1f}с | ETA=по курсору API",
            flush=True,
        )

    with psycopg2.connect(**config) as conn:
        ensure_schema(conn)
        run_id = create_run(conn, marketplaces)
        for marketplace in marketplaces:
            try:
                raw_rows = (
                    fetch_wb_reviews(args, client_key, progress)
                    if marketplace == "wb"
                    else fetch_ozon_reviews(args, client_key, progress)
                )
                count = replace_marketplace(conn, marketplace, raw_rows)
                loaded += count
                processed += 1
                print(f"[{processed}/{len(marketplaces)}] {marketplace.upper()}: импортировано {count}, ошибок 0", flush=True)
            except Exception as exc:
                conn.rollback()
                failed += 1
                processed += 1
                errors.append(f"{marketplace}:{type(exc).__name__}:{exc}")
                if isinstance(exc, PermissionError):
                    print(
                        f"ОГРАНИЧЕНИЕ: {marketplace.upper()} отзывы | {str(exc)[:500]}",
                        flush=True,
                    )
                print(f"[{processed}/{len(marketplaces)}] {marketplace.upper()}: импортировано 0, ошибка {type(exc).__name__}", flush=True)
        status = "success" if not failed else "blocked" if failed == len(marketplaces) else "partial"
        finish_run(conn, run_id, status, processed, loaded, failed, errors)

    elapsed = time.monotonic() - started
    print(
        f"ИТОГО: площадок={len(marketplaces)} | отзывов={loaded} | ошибок={failed} | "
        f"запросов={request_count} | время={elapsed:.1f}с | статус={status}",
        flush=True,
    )
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
