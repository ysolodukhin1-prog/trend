#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-update a client's WB assortment from the read-only Content API.

The marketplace is never mutated.  API responses are persisted first, then the
legacy assortment tables are enriched additively: missing cards/sizes and empty
fields are filled, while existing non-empty values and rows are preserved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

CLIENT_KEY = os.environ.get("DASHBOARD_CLIENT", "km_trade").strip().lower()
TARGET_DB = os.environ.get("DASHBOARD_DB_NAME", "km_trade_products").strip()
CLIENT_LABEL = os.environ.get("DASHBOARD_CLIENT_LABEL", CLIENT_KEY).strip()
TOKEN_ENV = os.environ.get("WB_API_TOKEN_ENV") or f"WB_API_TOKEN_{CLIENT_KEY.upper()}"
API_HOST = "https://content-api.wildberries.ru"
API_PATH = "/content/v2/get/cards/list"
PAGE_SIZE = 100
REQUEST_INTERVAL_SECONDS = float(os.environ.get("WB_CONTENT_API_INTERVAL_SECONDS", "0.75"))
HTTP_TIMEOUT_SECONDS = 180
MAX_RETRIES = 6
CACHE_FILE_RE = re.compile(r"^wb_cards_(?:(desc)_)?\d+_[^.]+\.json$", re.IGNORECASE)
SOURCE_KEY = "content.cards.active"
RAW_SOURCE = "api://content/v2/get/cards/list"


class WbAssortmentError(RuntimeError):
    pass


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ").strip())


def normalized_name(value: Any) -> str:
    return clean_text(value).rstrip("*").replace("ё", "е").casefold()


def canonical_barcode(value: Any) -> str:
    result = clean_text(value)
    return re.sub(r"\.0+$", "", result)


def parse_timestamp(value: Any) -> datetime | None:
    raw = clean_text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def characteristic_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            part = characteristic_value(item)
            if part and part not in parts:
                parts.append(part)
        return "; ".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return clean_text(value)


def first_photo(card: dict[str, Any]) -> str:
    for photo in card.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        for key in ("big", "c516x688", "square", "c246x328", "tm"):
            candidate = clean_text(photo.get(key))
            if candidate:
                return candidate
    return ""


def token_value(app) -> str:
    values = app.read_app_env_file()
    token = clean_text(os.environ.get(TOKEN_ENV) or values.get(TOKEN_ENV))
    if not token:
        token = clean_text(app.registered_client_credential(CLIENT_KEY, "wb_api_token"))
    if not token:
        raise WbAssortmentError(f"Для {CLIENT_LABEL} не сохранён WB API token")
    return token


def connect_target(app):
    try:
        app.hydrate_registered_clients()
    except Exception:
        pass
    config = dict(app.read_db_config(CLIENT_KEY))
    config["database"] = TARGET_DB
    config["application_name"] = f"{CLIENT_KEY}_wb_assortment_soft_update"
    conn = psycopg2.connect(**config, cursor_factory=RealDictCursor)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database() AS db")
        actual = cur.fetchone()["db"]
    if actual != TARGET_DB:
        conn.close()
        raise WbAssortmentError(f"Ожидалась БД {TARGET_DB}, подключена {actual}")
    return conn


class ContentApiClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.request_count = 0
        self.last_request_at = 0.0

    def _wait(self) -> None:
        remaining = REQUEST_INTERVAL_SECONDS - (time.monotonic() - self.last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _retry_seconds(headers: Any, status: int, attempt: int) -> int:
        for key in ("X-Ratelimit-Retry", "Retry-After"):
            raw = headers.get(key) if headers else None
            try:
                if raw is not None:
                    return max(1, min(300, math.ceil(float(raw))))
            except (TypeError, ValueError):
                pass
        reset = headers.get("X-Ratelimit-Reset") if headers else None
        try:
            if reset is not None:
                return max(1, min(300, math.ceil(float(reset) - time.time())))
        except (TypeError, ValueError):
            pass
        return min(180, 60 if status == 429 else 2**attempt)

    @staticmethod
    def _sleep_with_progress(seconds: int, label: str) -> None:
        remaining = max(0, int(seconds))
        while remaining:
            print(f"ЛИМИТ API: {label} | осталось {remaining}s", flush=True)
            chunk = min(10, remaining)
            time.sleep(chunk)
            remaining -= chunk

    def cards(self, cursor: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        body = {
            "settings": {
                "sort": {"ascending": True},
                "cursor": cursor,
                "filter": {"withPhoto": -1},
            }
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        path = f"{API_PATH}?{urlencode({'locale': 'ru'})}"
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait()
            self.request_count += 1
            request = Request(
                API_HOST + path,
                data=data,
                headers={
                    "Authorization": self.token,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                    raw = response.read()
                    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                self.last_request_at = time.monotonic()
                payload = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(payload, dict):
                    raise WbAssortmentError("WB cards/list вернул не объект JSON")
                return payload, headers
            except HTTPError as exc:
                self.last_request_at = time.monotonic()
                detail = exc.read().decode("utf-8", "replace")[:600]
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= MAX_RETRIES:
                    raise WbAssortmentError(f"WB HTTP {exc.code} cards/list: {detail}") from exc
                wait = self._retry_seconds(exc.headers, exc.code, attempt)
                self._sleep_with_progress(wait, f"WB карточки | HTTP {exc.code} | попытка {attempt}/{MAX_RETRIES}")
            except (TimeoutError, URLError, json.JSONDecodeError) as exc:
                self.last_request_at = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise WbAssortmentError(f"WB cards/list network/JSON error: {exc}") from exc
                wait = min(120, 10 * attempt)
                self._sleep_with_progress(wait, f"WB карточки | сеть | попытка {attempt}/{MAX_RETRIES}")
        raise WbAssortmentError("Исчерпаны повторы WB cards/list")


def relation_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
    return bool(cur.fetchone()["reg"])


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        for required in ("products", "product_attributes", "categories"):
            if not relation_exists(cur, required):
                raise WbAssortmentError(f"В БД {TARGET_DB} отсутствует public.{required}")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.wb_assortment_api_cards (
                nm_id bigint PRIMARY KEY,
                vendor_code text,
                subject_id bigint,
                marketplace_updated_at timestamptz,
                captured_at timestamptz NOT NULL DEFAULT now(),
                payload jsonb NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wb_assortment_api_cards_updated
                ON public.wb_assortment_api_cards(marketplace_updated_at, nm_id);

            CREATE TABLE IF NOT EXISTS public.wb_assortment_api_state (
                source_key text PRIMARY KEY,
                cursor_updated_at timestamptz,
                cursor_nm_id bigint,
                complete_snapshot boolean NOT NULL DEFAULT false,
                cards_total bigint NOT NULL DEFAULT 0,
                last_status text,
                last_error text,
                last_started_at timestamptz,
                last_finished_at timestamptz,
                updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS public.wb_assortment_api_runs (
                run_id bigserial PRIMARY KEY,
                mode text NOT NULL,
                requests_count integer NOT NULL DEFAULT 0,
                api_cards_count integer NOT NULL DEFAULT 0,
                raw_cards_count integer NOT NULL DEFAULT 0,
                inserted_products integer NOT NULL DEFAULT 0,
                enriched_products integer NOT NULL DEFAULT 0,
                filled_barcodes integer NOT NULL DEFAULT 0,
                inserted_attributes bigint NOT NULL DEFAULT 0,
                enriched_attributes bigint NOT NULL DEFAULT 0,
                status text NOT NULL,
                error text,
                started_at timestamptz NOT NULL DEFAULT now(),
                finished_at timestamptz
            )
            """
        )
        if not relation_exists(cur, "attribute_definitions"):
            cur.execute(
                """
                CREATE TABLE public.attribute_definitions (
                    attribute_id serial PRIMARY KEY,
                    attribute_name text NOT NULL UNIQUE,
                    attribute_scope text NOT NULL DEFAULT 'category_specific',
                    data_type text NOT NULL DEFAULT 'text',
                    created_at timestamp DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        for column in (
            "artikul_wb text",
            "artikul_prodavtsa text",
            "barkod text",
            "brend text",
            "kategoriya_prodavtsa text",
            "naimenovanie text",
            "opisanie text",
            "foto text",
            "seller_category_name text",
            "import_file text",
            "imported_at timestamp DEFAULT CURRENT_TIMESTAMP",
            "updated_at timestamp DEFAULT CURRENT_TIMESTAMP",
            "pol text",
            "tsvet text",
            "sostav text",
            "vozrastnye_ogranicheniya text",
            "razmer text",
            "ros_razmer text",
            "kollektsiya text",
            "uhod_za_veschami text",
            "dekorativnye_elementy text",
        ):
            cur.execute(f"ALTER TABLE public.products ADD COLUMN IF NOT EXISTS {column}")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_product_attributes_product_attribute_soft "
            "ON public.product_attributes(product_id, attribute_id)"
        )
    conn.commit()


def current_state(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM public.wb_assortment_api_state WHERE source_key = %s",
            (SOURCE_KEY,),
        )
        return dict(cur.fetchone() or {})


def save_state(
    conn,
    *,
    cursor_updated_at: datetime | None,
    cursor_nm_id: int | None,
    complete_snapshot: bool,
    status: str,
    error: str = "",
    finished: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.wb_assortment_api_state (
                source_key, cursor_updated_at, cursor_nm_id, complete_snapshot,
                cards_total, last_status, last_error, last_started_at, last_finished_at
            )
            VALUES (%s, %s, %s, %s,
                    (SELECT count(*) FROM public.wb_assortment_api_cards),
                    %s, %s, now(), CASE WHEN %s THEN now() ELSE NULL END)
            ON CONFLICT (source_key) DO UPDATE SET
                cursor_updated_at = COALESCE(EXCLUDED.cursor_updated_at, public.wb_assortment_api_state.cursor_updated_at),
                cursor_nm_id = COALESCE(EXCLUDED.cursor_nm_id, public.wb_assortment_api_state.cursor_nm_id),
                complete_snapshot = EXCLUDED.complete_snapshot,
                cards_total = EXCLUDED.cards_total,
                last_status = EXCLUDED.last_status,
                last_error = EXCLUDED.last_error,
                last_started_at = CASE
                    WHEN public.wb_assortment_api_state.last_status IS DISTINCT FROM 'running' THEN now()
                    ELSE public.wb_assortment_api_state.last_started_at
                END,
                last_finished_at = CASE WHEN %s THEN now() ELSE NULL END,
                updated_at = now()
            """,
            (
                SOURCE_KEY,
                cursor_updated_at,
                cursor_nm_id,
                complete_snapshot,
                status,
                error,
                finished,
                finished,
            ),
        )
    conn.commit()


def persist_raw_cards(conn, cards: Iterable[dict[str, Any]]) -> int:
    values = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        try:
            nm_id = int(card.get("nmID") or 0)
        except (TypeError, ValueError):
            continue
        if not nm_id:
            continue
        values.append(
            (
                nm_id,
                clean_text(card.get("vendorCode")) or None,
                int(card.get("subjectID") or 0) or None,
                parse_timestamp(card.get("updatedAt")),
                Json(card),
            )
        )
    for start in range(0, len(values), 500):
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO public.wb_assortment_api_cards (
                    nm_id, vendor_code, subject_id, marketplace_updated_at, payload
                ) VALUES %s
                ON CONFLICT (nm_id) DO UPDATE SET
                    vendor_code = COALESCE(NULLIF(EXCLUDED.vendor_code, ''), public.wb_assortment_api_cards.vendor_code),
                    subject_id = COALESCE(EXCLUDED.subject_id, public.wb_assortment_api_cards.subject_id),
                    marketplace_updated_at = COALESCE(EXCLUDED.marketplace_updated_at, public.wb_assortment_api_cards.marketplace_updated_at),
                    captured_at = now(),
                    payload = EXCLUDED.payload
                WHERE public.wb_assortment_api_cards.marketplace_updated_at IS NULL
                   OR EXCLUDED.marketplace_updated_at IS NULL
                   OR EXCLUDED.marketplace_updated_at >= public.wb_assortment_api_cards.marketplace_updated_at
                """,
                values[start:start + 500],
                page_size=500,
            )
        conn.commit()
    return len(values)


def cache_payload(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    payload = parsed.get("payload", parsed) if isinstance(parsed, dict) else {}
    return payload if isinstance(payload, dict) else {}


def load_seed_cache(directory: Path) -> tuple[list[dict[str, Any]], datetime, int, dict[str, Any]]:
    if not directory.is_dir():
        raise WbAssortmentError(f"Каталог WB cache не найден: {directory}")
    files = sorted(path for path in directory.iterdir() if path.is_file() and CACHE_FILE_RE.match(path.name))
    if not files:
        raise WbAssortmentError(f"В {directory} нет сохранённых wb_cards_*.json")
    by_nm_id: dict[int, dict[str, Any]] = {}
    asc_times: list[datetime] = []
    desc_times: list[datetime] = []
    page_rows = 0
    for index, path in enumerate(files, start=1):
        payload = cache_payload(path)
        cards = [card for card in (payload.get("cards") or []) if isinstance(card, dict)]
        page_rows += len(cards)
        is_desc = bool(CACHE_FILE_RE.match(path.name).group(1))
        for card in cards:
            try:
                nm_id = int(card.get("nmID") or 0)
            except (TypeError, ValueError):
                continue
            updated_at = parse_timestamp(card.get("updatedAt"))
            if updated_at:
                (desc_times if is_desc else asc_times).append(updated_at)
            previous = by_nm_id.get(nm_id)
            previous_at = parse_timestamp(previous.get("updatedAt")) if previous else None
            if previous is None or (updated_at and (previous_at is None or updated_at >= previous_at)):
                by_nm_id[nm_id] = card
        if index == 1 or index % 100 == 0 or index == len(files):
            print(
                f"ПРОГРЕСС: WB сохранённые страницы | {index}/{len(files)} "
                f"({index / len(files) * 100:.1f}%) | rows={page_rows:,} | "
                f"unique_cards={len(by_nm_id):,} | errors=0",
                flush=True,
            )
    if not by_nm_id:
        raise WbAssortmentError("Сохранённые WB страницы не содержат карточек")
    if asc_times and desc_times and min(desc_times) > max(asc_times):
        raise WbAssortmentError("Между прямым и обратным WB cache есть временной разрыв; snapshot не помечен полным")
    if not asc_times or not desc_times:
        raise WbAssortmentError("Для безопасного seed нужны пересекающиеся ascending и descending WB страницы")
    checkpoints = []
    for nm_id, card in by_nm_id.items():
        updated_at = parse_timestamp(card.get("updatedAt"))
        if updated_at:
            checkpoints.append((updated_at, nm_id))
    if not checkpoints:
        raise WbAssortmentError("В WB cache нет updatedAt для checkpoint")
    checkpoint_at, checkpoint_nm_id = max(checkpoints)
    stats = {
        "files": len(files),
        "page_rows": page_rows,
        "unique_cards": len(by_nm_id),
        "duplicates": page_rows - len(by_nm_id),
    }
    return list(by_nm_id.values()), checkpoint_at, checkpoint_nm_id, stats


def fetch_incremental(conn, api: ContentApiClient, state: dict[str, Any], started: float) -> list[dict[str, Any]]:
    cursor: dict[str, Any] = {"limit": PAGE_SIZE}
    checkpoint_at = state.get("cursor_updated_at")
    checkpoint_nm_id = state.get("cursor_nm_id")
    if checkpoint_at and checkpoint_nm_id:
        if isinstance(checkpoint_at, datetime):
            cursor["updatedAt"] = checkpoint_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            cursor["updatedAt"] = clean_text(checkpoint_at)
        cursor["nmID"] = int(checkpoint_nm_id)
    mode = "incremental" if checkpoint_at and checkpoint_nm_id else "full-bootstrap"
    collected: dict[int, dict[str, Any]] = {}
    page = 0
    while True:
        page += 1
        payload, headers = api.cards(cursor)
        cards = [card for card in (payload.get("cards") or []) if isinstance(card, dict)]
        persist_raw_cards(conn, cards)
        for card in cards:
            try:
                collected[int(card.get("nmID") or 0)] = card
            except (TypeError, ValueError):
                continue
        response_cursor = payload.get("cursor") if isinstance(payload.get("cursor"), dict) else {}
        total = int(response_cursor.get("total") or len(cards))
        next_at = parse_timestamp(response_cursor.get("updatedAt"))
        try:
            next_nm_id = int(response_cursor.get("nmID") or 0) or None
        except (TypeError, ValueError):
            next_nm_id = None
        elapsed = time.monotonic() - started
        remaining = headers.get("x-ratelimit-remaining", "—")
        print(
            f"ПРОГРЕСС: WB API | request={api.request_count} | page={page} | mode={mode} | "
            f"batch={len(cards):,} | accumulated={len(collected):,} | limit_remaining={remaining} | "
            f"elapsed={duration(elapsed)} | ETA={'00:00' if total < PAGE_SIZE else '—'}",
            flush=True,
        )
        if cards and next_at and next_nm_id:
            cursor = {
                "limit": PAGE_SIZE,
                "updatedAt": next_at.isoformat().replace("+00:00", "Z"),
                "nmID": next_nm_id,
            }
            save_state(
                conn,
                cursor_updated_at=next_at,
                cursor_nm_id=next_nm_id,
                complete_snapshot=bool(state.get("complete_snapshot")),
                status="running",
            )
        if total < PAGE_SIZE or len(cards) < PAGE_SIZE:
            break
        if not next_at or not next_nm_id:
            raise WbAssortmentError("WB не вернул устойчивый cursor; существующий ассортимент не изменён")
    final_at = parse_timestamp(cursor.get("updatedAt")) or (
        checkpoint_at if isinstance(checkpoint_at, datetime) else parse_timestamp(checkpoint_at)
    )
    final_nm_id = int(cursor.get("nmID") or checkpoint_nm_id or 0) or None
    save_state(
        conn,
        cursor_updated_at=final_at,
        cursor_nm_id=final_nm_id,
        complete_snapshot=True,
        status="fetched",
    )
    return list(collected.values())


IMPORTANT_CHARACTERISTIC_COLUMNS = {
    "пол": "pol",
    "пол ребенка": "pol",
    "цвет": "tsvet",
    "цвет товара": "tsvet",
    "состав": "sostav",
    "состав материала": "sostav",
    "возрастные ограничения": "vozrastnye_ogranicheniya",
    "возрастная группа": "vozrastnye_ogranicheniya",
    "размер": "razmer",
    "размер производителя": "razmer",
    "российский размер": "ros_razmer",
    "коллекция": "kollektsiya",
    "уход за вещами": "uhod_za_veschami",
    "декоративные элементы": "dekorativnye_elementy",
}


def card_rows(cards: Iterable[dict[str, Any]]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    rows: list[tuple[Any, ...]] = []
    sizes: list[tuple[Any, ...]] = []
    characteristics: list[tuple[Any, ...]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        try:
            nm_id = int(card.get("nmID") or 0)
        except (TypeError, ValueError):
            continue
        if not nm_id:
            continue
        important: dict[str, str] = {}
        by_name: dict[str, list[str]] = defaultdict(list)
        for characteristic in card.get("characteristics") or []:
            if not isinstance(characteristic, dict):
                continue
            name = clean_text(characteristic.get("name"))
            value = characteristic_value(characteristic.get("value"))
            if not name or not value:
                continue
            for part in (piece.strip() for piece in value.split(";")):
                if part and part not in by_name[name]:
                    by_name[name].append(part)
            target = IMPORTANT_CHARACTERISTIC_COLUMNS.get(normalized_name(name))
            if target and not important.get(target):
                important[target] = value
        for name, values in by_name.items():
            characteristics.append((nm_id, name, "; ".join(values)))
        rows.append(
            (
                nm_id,
                clean_text(card.get("vendorCode")) or None,
                clean_text(card.get("brand")) or None,
                clean_text(card.get("title")) or None,
                clean_text(card.get("description")) or None,
                clean_text(card.get("subjectName")) or None,
                int(card.get("subjectID") or 0) or None,
                first_photo(card) or None,
                important.get("pol") or None,
                important.get("tsvet") or None,
                important.get("sostav") or None,
                important.get("vozrastnye_ogranicheniya") or None,
                important.get("razmer") or None,
                important.get("ros_razmer") or None,
                important.get("kollektsiya") or None,
                important.get("uhod_za_veschami") or None,
                important.get("dekorativnye_elementy") or None,
            )
        )
        card_sizes = [item for item in (card.get("sizes") or []) if isinstance(item, dict)]
        card_size_rows = []
        for size in card_sizes:
            tech_size = clean_text(size.get("techSize"))
            wb_size = clean_text(size.get("wbSize"))
            for sku in size.get("skus") or []:
                barcode = canonical_barcode(sku)
                if barcode:
                    card_size_rows.append((nm_id, barcode, tech_size or None, wb_size or None))
        if card_size_rows:
            sizes.extend(card_size_rows)
        else:
            sizes.append((nm_id, "", None, None))
    return rows, sizes, characteristics


def load_temp_tables(cur, cards: list[dict[str, Any]]) -> None:
    rows, sizes, characteristics = card_rows(cards)
    cur.execute(
        """
        CREATE TEMP TABLE tmp_wb_api_cards (
            nm_id bigint PRIMARY KEY,
            vendor_code text, brand text, title text, description text,
            subject_name text, subject_id bigint, photo text,
            pol text, tsvet text, sostav text, vozrast text,
            razmer text, ros_razmer text, kollektsiya text, uhod text, dekor text
        ) ON COMMIT DROP;
        CREATE TEMP TABLE tmp_wb_api_sizes (
            nm_id bigint NOT NULL, barcode text NOT NULL,
            tech_size text, wb_size text,
            PRIMARY KEY (nm_id, barcode)
        ) ON COMMIT DROP;
        CREATE TEMP TABLE tmp_wb_api_characteristics (
            nm_id bigint NOT NULL, attribute_name text NOT NULL, value_text text NOT NULL,
            PRIMARY KEY (nm_id, attribute_name)
        ) ON COMMIT DROP;
        """
    )
    if rows:
        execute_values(
            cur,
            "INSERT INTO tmp_wb_api_cards VALUES %s ON CONFLICT (nm_id) DO UPDATE SET title=EXCLUDED.title",
            rows,
            page_size=1000,
        )
    if sizes:
        execute_values(
            cur,
            "INSERT INTO tmp_wb_api_sizes VALUES %s ON CONFLICT (nm_id, barcode) DO NOTHING",
            sizes,
            page_size=5000,
        )
    if characteristics:
        execute_values(
            cur,
            "INSERT INTO tmp_wb_api_characteristics VALUES %s "
            "ON CONFLICT (nm_id, attribute_name) DO UPDATE SET value_text=EXCLUDED.value_text",
            characteristics,
            page_size=5000,
        )


def category_id_has_default(cur) -> bool:
    cur.execute(
        """
        SELECT column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='categories' AND column_name='category_id'
        """
    )
    row = cur.fetchone()
    return bool(row and row["column_default"])


def merge_core(conn, cards: list[dict[str, Any]], started: float) -> dict[str, int]:
    if not cards:
        return {
            "inserted_products": 0,
            "enriched_products": 0,
            "filled_barcodes": 0,
            "inserted_attributes": 0,
            "enriched_attributes": 0,
        }
    stats: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            load_temp_tables(cur, cards)
            if category_id_has_default(cur):
                cur.execute(
                    """
                    INSERT INTO public.categories (category_name)
                    SELECT DISTINCT subject_name FROM tmp_wb_api_cards
                    WHERE nullif(trim(subject_name), '') IS NOT NULL
                    ON CONFLICT (category_name) DO NOTHING
                    """
                )
            else:
                cur.execute(
                    """
                    INSERT INTO public.categories (category_id, category_name)
                    SELECT DISTINCT subject_id, subject_name FROM tmp_wb_api_cards
                    WHERE subject_id IS NOT NULL AND nullif(trim(subject_name), '') IS NOT NULL
                    ON CONFLICT (category_id) DO UPDATE SET
                        category_name = COALESCE(NULLIF(public.categories.category_name, ''), EXCLUDED.category_name)
                    """
                )

            cur.execute(
                """
                UPDATE public.products p
                SET artikul_prodavtsa = COALESCE(NULLIF(trim(p.artikul_prodavtsa), ''), c.vendor_code),
                    brend = COALESCE(NULLIF(trim(p.brend), ''), c.brand),
                    naimenovanie = COALESCE(NULLIF(trim(p.naimenovanie), ''), c.title),
                    opisanie = COALESCE(NULLIF(trim(p.opisanie), ''), c.description),
                    kategoriya_prodavtsa = COALESCE(NULLIF(trim(p.kategoriya_prodavtsa), ''), c.subject_name),
                    seller_category_name = COALESCE(NULLIF(trim(p.seller_category_name), ''), c.subject_name),
                    category_id = COALESCE(p.category_id, cat.category_id),
                    foto = COALESCE(NULLIF(trim(p.foto), ''), c.photo),
                    pol = COALESCE(NULLIF(trim(p.pol), ''), c.pol),
                    tsvet = COALESCE(NULLIF(trim(p.tsvet), ''), c.tsvet),
                    sostav = COALESCE(NULLIF(trim(p.sostav), ''), c.sostav),
                    vozrastnye_ogranicheniya = COALESCE(NULLIF(trim(p.vozrastnye_ogranicheniya), ''), c.vozrast),
                    razmer = COALESCE(NULLIF(trim(p.razmer), ''), c.razmer),
                    ros_razmer = COALESCE(NULLIF(trim(p.ros_razmer), ''), c.ros_razmer),
                    kollektsiya = COALESCE(NULLIF(trim(p.kollektsiya), ''), c.kollektsiya),
                    uhod_za_veschami = COALESCE(NULLIF(trim(p.uhod_za_veschami), ''), c.uhod),
                    dekorativnye_elementy = COALESCE(NULLIF(trim(p.dekorativnye_elementy), ''), c.dekor),
                    updated_at = now()
                FROM tmp_wb_api_cards c
                LEFT JOIN public.categories cat ON cat.category_name = c.subject_name
                WHERE trim(p.artikul_wb) = c.nm_id::text
                  AND (
                    (nullif(trim(p.artikul_prodavtsa), '') IS NULL AND c.vendor_code IS NOT NULL) OR
                    (nullif(trim(p.brend), '') IS NULL AND c.brand IS NOT NULL) OR
                    (nullif(trim(p.naimenovanie), '') IS NULL AND c.title IS NOT NULL) OR
                    (nullif(trim(p.opisanie), '') IS NULL AND c.description IS NOT NULL) OR
                    (nullif(trim(p.seller_category_name), '') IS NULL AND c.subject_name IS NOT NULL) OR
                    (p.category_id IS NULL AND cat.category_id IS NOT NULL) OR
                    (nullif(trim(p.foto), '') IS NULL AND c.photo IS NOT NULL) OR
                    (nullif(trim(p.pol), '') IS NULL AND c.pol IS NOT NULL) OR
                    (nullif(trim(p.tsvet), '') IS NULL AND c.tsvet IS NOT NULL) OR
                    (nullif(trim(p.sostav), '') IS NULL AND c.sostav IS NOT NULL) OR
                    (nullif(trim(p.vozrastnye_ogranicheniya), '') IS NULL AND c.vozrast IS NOT NULL)
                  )
                """
            )
            stats["enriched_products"] = cur.rowcount

            cur.execute(
                """
                CREATE TEMP TABLE tmp_wb_missing_sizes ON COMMIT DROP AS
                SELECT s.*,
                       row_number() OVER (PARTITION BY s.nm_id ORDER BY s.barcode) AS rn
                FROM tmp_wb_api_sizes s
                WHERE nullif(s.barcode, '') IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM public.products p
                    WHERE regexp_replace(trim(p.barkod), '\\.0+$', '') = s.barcode
                  );
                CREATE TEMP TABLE tmp_wb_blank_products ON COMMIT DROP AS
                SELECT p.product_id, trim(p.artikul_wb)::bigint AS nm_id,
                       row_number() OVER (PARTITION BY trim(p.artikul_wb) ORDER BY p.product_id) AS rn
                FROM public.products p
                JOIN (SELECT DISTINCT nm_id FROM tmp_wb_missing_sizes) n
                  ON trim(p.artikul_wb) = n.nm_id::text
                WHERE nullif(trim(p.barkod), '') IS NULL;
                """
            )
            cur.execute(
                """
                UPDATE public.products p
                SET barkod = s.barcode,
                    razmer = COALESCE(NULLIF(trim(p.razmer), ''), s.tech_size, s.wb_size),
                    updated_at = now()
                FROM tmp_wb_blank_products b
                JOIN tmp_wb_missing_sizes s ON s.nm_id = b.nm_id AND s.rn = b.rn
                WHERE p.product_id = b.product_id
                """
            )
            stats["filled_barcodes"] = cur.rowcount

            cur.execute(
                """
                UPDATE public.products p
                SET razmer = COALESCE(NULLIF(trim(p.razmer), ''), s.tech_size, s.wb_size),
                    updated_at = now()
                FROM tmp_wb_api_sizes s
                WHERE nullif(trim(p.razmer), '') IS NULL
                  AND nullif(s.barcode, '') IS NOT NULL
                  AND regexp_replace(trim(p.barkod), '\\.0+$', '') = s.barcode
                  AND COALESCE(s.tech_size, s.wb_size) IS NOT NULL
                """
            )

            cur.execute(
                """
                INSERT INTO public.products (
                    artikul_wb, artikul_prodavtsa, barkod, brend,
                    kategoriya_prodavtsa, naimenovanie, opisanie, foto,
                    category_id, seller_category_name, import_file,
                    imported_at, updated_at, pol, tsvet, sostav,
                    vozrastnye_ogranicheniya, razmer, ros_razmer,
                    kollektsiya, uhod_za_veschami, dekorativnye_elementy
                )
                SELECT c.nm_id::text, c.vendor_code, nullif(s.barcode, ''), c.brand,
                       c.subject_name, c.title, c.description, c.photo,
                       cat.category_id, c.subject_name, %s,
                       now(), now(), c.pol, c.tsvet, c.sostav,
                       c.vozrast, COALESCE(s.tech_size, s.wb_size, c.razmer), c.ros_razmer,
                       c.kollektsiya, c.uhod, c.dekor
                FROM tmp_wb_api_cards c
                JOIN tmp_wb_api_sizes s ON s.nm_id = c.nm_id AND nullif(s.barcode, '') IS NOT NULL
                LEFT JOIN public.categories cat ON cat.category_name = c.subject_name
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.products p
                    WHERE regexp_replace(trim(p.barkod), '\\.0+$', '') = s.barcode
                )
                """,
                (RAW_SOURCE,),
            )
            inserted_with_barcode = cur.rowcount
            cur.execute(
                """
                INSERT INTO public.products (
                    artikul_wb, artikul_prodavtsa, brend, kategoriya_prodavtsa,
                    naimenovanie, opisanie, foto, category_id, seller_category_name,
                    import_file, imported_at, updated_at, pol, tsvet, sostav,
                    vozrastnye_ogranicheniya, razmer, ros_razmer,
                    kollektsiya, uhod_za_veschami, dekorativnye_elementy
                )
                SELECT c.nm_id::text, c.vendor_code, c.brand, c.subject_name,
                       c.title, c.description, c.photo, cat.category_id, c.subject_name,
                       %s, now(), now(), c.pol, c.tsvet, c.sostav,
                       c.vozrast, c.razmer, c.ros_razmer,
                       c.kollektsiya, c.uhod, c.dekor
                FROM tmp_wb_api_cards c
                LEFT JOIN public.categories cat ON cat.category_name = c.subject_name
                WHERE NOT EXISTS (SELECT 1 FROM public.products p WHERE trim(p.artikul_wb) = c.nm_id::text)
                """,
                (RAW_SOURCE,),
            )
            stats["inserted_products"] = inserted_with_barcode + cur.rowcount

            cur.execute(
                """
                INSERT INTO public.attribute_definitions (attribute_name, attribute_scope, data_type)
                SELECT DISTINCT attribute_name, 'category_specific', 'text'
                FROM tmp_wb_api_characteristics
                ON CONFLICT (attribute_name) DO NOTHING
                """
            )
            if relation_exists(cur, "category_attributes"):
                cur.execute(
                    """
                    INSERT INTO public.category_attributes (category_id, attribute_id, attribute_name)
                    SELECT DISTINCT cat.category_id, d.attribute_id, ch.attribute_name
                    FROM tmp_wb_api_characteristics ch
                    JOIN tmp_wb_api_cards c ON c.nm_id = ch.nm_id
                    JOIN public.categories cat ON cat.category_name = c.subject_name
                    JOIN public.attribute_definitions d ON d.attribute_name = ch.attribute_name
                    ON CONFLICT (category_id, attribute_id) DO UPDATE SET
                        attribute_name = COALESCE(NULLIF(public.category_attributes.attribute_name, ''), EXCLUDED.attribute_name)
                    """
                )
            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE pa.product_id IS NULL) AS missing,
                    count(*) FILTER (WHERE pa.product_id IS NOT NULL AND nullif(trim(pa.value_text), '') IS NULL) AS blank
                FROM public.products p
                JOIN tmp_wb_api_characteristics ch ON trim(p.artikul_wb) = ch.nm_id::text
                JOIN public.attribute_definitions d ON d.attribute_name = ch.attribute_name
                LEFT JOIN public.product_attributes pa
                  ON pa.product_id = p.product_id AND pa.attribute_id = d.attribute_id
                """
            )
            attribute_counts = cur.fetchone()
            stats["inserted_attributes"] = int(attribute_counts["missing"] or 0)
            stats["enriched_attributes"] = int(attribute_counts["blank"] or 0)
            cur.execute(
                """
                INSERT INTO public.product_attributes (product_id, attribute_id, value_text)
                SELECT p.product_id, d.attribute_id, ch.value_text
                FROM public.products p
                JOIN tmp_wb_api_characteristics ch ON trim(p.artikul_wb) = ch.nm_id::text
                JOIN public.attribute_definitions d ON d.attribute_name = ch.attribute_name
                ON CONFLICT (product_id, attribute_id) DO UPDATE SET
                    value_text = EXCLUDED.value_text
                WHERE nullif(trim(public.product_attributes.value_text), '') IS NULL
                  AND nullif(trim(EXCLUDED.value_text), '') IS NOT NULL
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(
        f"ПРОГРЕСС: WB merge | cards={len(cards):,} | inserted_products={stats['inserted_products']:,} | "
        f"enriched_products={stats['enriched_products']:,} | filled_barcodes={stats['filled_barcodes']:,} | "
        f"inserted_attributes={stats['inserted_attributes']:,} | enriched_attributes={stats['enriched_attributes']:,} | "
        f"elapsed={duration(time.monotonic() - started)} | step_pct=100.0",
        flush=True,
    )
    return stats


def raw_card_ids(conn) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT nm_id FROM public.wb_assortment_api_cards ORDER BY nm_id")
        return [int(row["nm_id"]) for row in cur.fetchall()]


def merge_core_batches(
    conn,
    nm_ids: Iterable[int] | None,
    started: float,
    *,
    batch_size: int = 5000,
) -> dict[str, int]:
    ids = raw_card_ids(conn) if nm_ids is None else sorted({int(value) for value in nm_ids if int(value)})
    totals = {
        "inserted_products": 0,
        "enriched_products": 0,
        "filled_barcodes": 0,
        "inserted_attributes": 0,
        "enriched_attributes": 0,
    }
    batches = max(1, math.ceil(len(ids) / batch_size))
    for index, start in enumerate(range(0, len(ids), batch_size), start=1):
        batch_ids = ids[start:start + batch_size]
        cards = cached_cards_from_db(conn, batch_ids)
        result = merge_core(conn, cards, started)
        for key in totals:
            totals[key] += int(result.get(key, 0))
        elapsed = time.monotonic() - started
        eta = elapsed / index * max(batches - index, 0)
        print(
            f"ПРОГРЕСС: WB БД | batch={index}/{batches} ({index / batches * 100:.1f}%) | "
            f"cards={min(index * batch_size, len(ids)):,}/{len(ids):,} | "
            f"inserted_products={totals['inserted_products']:,} | "
            f"enriched_products={totals['enriched_products']:,} | "
            f"attributes={totals['inserted_attributes'] + totals['enriched_attributes']:,} | "
            f"elapsed={duration(elapsed)} | ETA={duration(eta)}",
            flush=True,
        )
    if ids:
        with conn.cursor() as cur:
            cur.execute("ANALYZE public.products")
            cur.execute("ANALYZE public.product_attributes")
        conn.commit()
    return totals


def cached_cards_from_db(conn, nm_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if nm_ids is None:
            cur.execute("SELECT payload FROM public.wb_assortment_api_cards ORDER BY nm_id")
        else:
            values = sorted({int(value) for value in nm_ids if int(value)})
            if not values:
                return []
            cur.execute(
                "SELECT payload FROM public.wb_assortment_api_cards WHERE nm_id = ANY(%s) ORDER BY nm_id",
                (values,),
            )
        return [dict(row["payload"]) for row in cur.fetchall() if isinstance(row.get("payload"), dict)]


def rebuild_views() -> None:
    command = [sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "scripts" / "rebuild_wb_assortment_views.py")]
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), env=os.environ.copy(), check=False)
    if result.returncode:
        raise WbAssortmentError(f"Пересборка WB-витрин завершилась с кодом {result.returncode}")


def record_run(conn, mode: str, api: ContentApiClient, api_cards: int, stats: dict[str, int], status: str, error: str = "") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.wb_assortment_api_runs (
                mode, requests_count, api_cards_count, raw_cards_count,
                inserted_products, enriched_products, filled_barcodes,
                inserted_attributes, enriched_attributes, status, error, finished_at
            )
            VALUES (%s, %s, %s, (SELECT count(*) FROM public.wb_assortment_api_cards),
                    %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                mode,
                api.request_count,
                api_cards,
                stats.get("inserted_products", 0),
                stats.get("enriched_products", 0),
                stats.get("filled_barcodes", 0),
                stats.get("inserted_attributes", 0),
                stats.get("enriched_attributes", 0),
                status,
                error or None,
            ),
        )
    conn.commit()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-cache-dir", type=Path, help="Однократно сохранить и слить уже выгруженные WB cards JSON")
    parser.add_argument("--full-scan", action="store_true", help="Сделать полный read-only проход с начала без удаления БД")
    parser.add_argument(
        "--snapshot-output",
        type=Path,
        help="Сохранить карточки текущего API-прохода в отдельный JSON-снимок",
    )
    parser.add_argument("--skip-views", action="store_true", help="Не пересобирать WB assortment materialized views")
    parser.add_argument("--no-api", action="store_true", help="Слить только seed-cache; используется для контролируемого восстановления")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = parse_args(argv)
    started = time.monotonic()
    import app  # noqa: WPS433

    token = "" if args.no_api else token_value(app)
    api = ContentApiClient(token)
    conn = connect_target(app)
    mode = "incremental"
    stats: dict[str, int] = {}
    api_cards: list[dict[str, Any]] = []
    try:
        ensure_schema(conn)
        state = current_state(conn)
        if args.full_scan:
            state = {}
            mode = "full-scan-soft"
        elif args.seed_cache_dir:
            mode = "cache-seed+incremental" if not args.no_api else "cache-seed"
        elif not state.get("cursor_updated_at"):
            mode = "full-bootstrap-soft"
        print(
            f"ПЛАН: WB ассортимент | клиент={CLIENT_KEY} | БД={TARGET_DB} | режим={mode} | "
            f"page_size={PAGE_SIZE} | интервал>={REQUEST_INTERVAL_SECONDS:.2f}s | "
            "API только чтение | БД: insert новых SKU + заполнение пустых полей/характеристик | "
            "удалений=0 | непустые значения не перезаписываются | Retry-After/X-Ratelimit-Retry",
            flush=True,
        )
        merge_ids: set[int] | None = set()
        if args.seed_cache_dir:
            seed_cards, checkpoint_at, checkpoint_nm_id, cache_stats = load_seed_cache(args.seed_cache_dir)
            persist_raw_cards(conn, seed_cards)
            merge_ids.update(int(card["nmID"]) for card in seed_cards if card.get("nmID"))
            save_state(
                conn,
                cursor_updated_at=checkpoint_at,
                cursor_nm_id=checkpoint_nm_id,
                complete_snapshot=True,
                status="seeded",
            )
            state = current_state(conn)
            print(
                f"ПРОГРЕСС: WB cache сохранён | files={cache_stats['files']:,} | "
                f"rows={cache_stats['page_rows']:,} | unique_cards={cache_stats['unique_cards']:,} | "
                f"duplicates={cache_stats['duplicates']:,} | checkpoint={checkpoint_at.isoformat()}#{checkpoint_nm_id}",
                flush=True,
            )
            del seed_cards
        if not args.no_api:
            api_cards = fetch_incremental(conn, api, state, started)
            merge_ids.update(int(card["nmID"]) for card in api_cards if card.get("nmID"))
            if args.snapshot_output:
                args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
                snapshot_tmp = args.snapshot_output.with_suffix(args.snapshot_output.suffix + ".tmp")
                snapshot_payload = {
                    "source": "WB Content API /content/v2/get/cards/list",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "complete_snapshot": bool(args.full_scan or not state.get("cursor_updated_at")),
                    "cards_count": len(api_cards),
                    "cards": api_cards,
                }
                snapshot_tmp.write_text(
                    json.dumps(snapshot_payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                snapshot_tmp.replace(args.snapshot_output)
                print(
                    f"ПРОГРЕСС: WB API snapshot | cards={len(api_cards):,} | "
                    f"complete={snapshot_payload['complete_snapshot']} | path={args.snapshot_output}",
                    flush=True,
                )
        if args.full_scan or (not args.seed_cache_dir and not state.get("complete_snapshot")):
            merge_ids = None
        stats = merge_core_batches(conn, merge_ids, started)
        if not args.skip_views and any(stats.values()):
            print("ПРОГРЕСС: WB витрины | запускаю безопасную пересборку | step_pct=95.0", flush=True)
            rebuild_views()
        final_state = current_state(conn)
        save_state(
            conn,
            cursor_updated_at=final_state.get("cursor_updated_at"),
            cursor_nm_id=final_state.get("cursor_nm_id"),
            complete_snapshot=bool(final_state.get("complete_snapshot")),
            status="completed",
            finished=True,
        )
        record_run(conn, mode, api, len(api_cards), stats, "ok")
    except Exception as exc:
        try:
            final_state = current_state(conn)
            save_state(
                conn,
                cursor_updated_at=final_state.get("cursor_updated_at"),
                cursor_nm_id=final_state.get("cursor_nm_id"),
                complete_snapshot=bool(final_state.get("complete_snapshot")),
                status="failed",
                error=str(exc)[:1200],
                finished=True,
            )
            record_run(conn, mode, api, len(api_cards), stats, "failed", str(exc)[:1200])
        except Exception:
            conn.rollback()
        print(
            f"ИТОГ: WB ассортимент | status=failed | requests={api.request_count} | "
            f"api_cards={len(api_cards):,} | errors=1 | partial=yes | "
            f"downloaded_data_preserved=yes | elapsed={duration(time.monotonic() - started)}",
            flush=True,
        )
        raise
    finally:
        conn.close()
    print(
        f"ИТОГ: WB ассортимент | status=ok | requests={api.request_count} | api_cards={len(api_cards):,} | "
        f"inserted_products={stats.get('inserted_products', 0):,} | "
        f"enriched_products={stats.get('enriched_products', 0):,} | "
        f"filled_barcodes={stats.get('filled_barcodes', 0):,} | "
        f"inserted_attributes={stats.get('inserted_attributes', 0):,} | "
        f"enriched_attributes={stats.get('enriched_attributes', 0):,} | "
        f"deletes=0 | blank_overwrites=0 | errors=0 | partial=no | "
        f"elapsed={duration(time.monotonic() - started)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
