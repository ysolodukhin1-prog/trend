#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soft-update Ozon assortment from read-only Seller API endpoints.

Every downloaded page is persisted before the legacy assortment is touched.
The merge adds missing products/attributes and fills empty fields only.  It never
deletes products and never replaces a non-empty legacy value with an API blank.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from psycopg2 import sql
import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import sync_ozon_assortment as base  # noqa: E402


CLIENT_KEY = base.CLIENT_KEY
TARGET_DB = base.TARGET_DB
CLIENT_LABEL = base.CLIENT_LABEL
PAGE_SIZE = base.PAGE_SIZE
RAW_SOURCE = "api://seller/v4/product/info/attributes"
READ_ONLY_PATHS = {
    "/v4/product/info/attributes",
    "/v1/description-category/tree",
    "/v1/description-category/attribute",
}


class ReadOnlySellerApiClient(base.SellerApiClient):
    """Reject every Seller API call outside the explicit read-only allowlist."""

    def post(self, path: str, payload: dict[str, Any], *, label: str) -> dict[str, Any]:
        if path not in READ_ONLY_PATHS:
            raise base.AssortmentPipelineError(f"Seller API endpoint запрещён soft-update режимом: {path}")
        return super().post(path, payload, label=label)


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def ensure_soft_schema(conn) -> None:
    with conn.cursor() as cur:
        for table in (
            "ozon_cat_products",
            "ozon_cat_product_attributes",
            "ozon_cat_attribute_definitions",
            "ozon_cat_common_attributes",
            "ozon_cat_category_attributes",
            "ozon_cat_categories",
        ):
            cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{table}",))
            if not cur.fetchone()["reg"]:
                raise base.AssortmentPipelineError(f"В БД {TARGET_DB} отсутствует public.{table}")
        cur.execute(
            """
            ALTER TABLE public.ozon_cat_products
                ADD COLUMN IF NOT EXISTS ozon_product_id bigint,
                ADD COLUMN IF NOT EXISTS description_category_id bigint,
                ADD COLUMN IF NOT EXISTS type_id bigint,
                ADD COLUMN IF NOT EXISTS barcodes_json jsonb,
                ADD COLUMN IF NOT EXISTS images_json jsonb,
                ADD COLUMN IF NOT EXISTS model_info_json jsonb,
                ADD COLUMN IF NOT EXISTS attributes_json jsonb,
                ADD COLUMN IF NOT EXISTS complex_attributes_json jsonb,
                ADD COLUMN IF NOT EXISTS raw_json jsonb,
                ADD COLUMN IF NOT EXISTS api_updated_at timestamptz
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_cards (
                ozon_product_id bigint PRIMARY KEY,
                offer_id text,
                sku text,
                description_category_id bigint,
                type_id bigint,
                captured_at timestamptz NOT NULL DEFAULT now(),
                payload jsonb NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ozon_assortment_api_cards_offer
                ON public.ozon_assortment_api_cards(offer_id);
            CREATE INDEX IF NOT EXISTS idx_ozon_assortment_api_cards_sku
                ON public.ozon_assortment_api_cards(sku);

            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_attribute_definitions (
                description_category_id bigint NOT NULL,
                type_id bigint NOT NULL,
                attribute_id bigint NOT NULL,
                attribute_name text NOT NULL,
                captured_at timestamptz NOT NULL DEFAULT now(),
                payload jsonb NOT NULL,
                PRIMARY KEY (description_category_id, type_id, attribute_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ozon_assortment_api_defs_pair
                ON public.ozon_assortment_api_attribute_definitions(description_category_id, type_id);

            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_unavailable_pairs (
                description_category_id bigint NOT NULL,
                type_id bigint NOT NULL,
                reason text NOT NULL,
                is_unavailable boolean NOT NULL DEFAULT true,
                captured_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (description_category_id, type_id)
            );
            ALTER TABLE public.ozon_assortment_api_unavailable_pairs
                ADD COLUMN IF NOT EXISTS is_unavailable boolean NOT NULL DEFAULT true;

            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_attribute_map (
                description_category_id bigint NOT NULL,
                type_id bigint NOT NULL,
                api_attribute_id bigint NOT NULL,
                canonical_attribute_id integer NOT NULL
                    REFERENCES public.ozon_cat_attribute_definitions(attribute_id),
                attribute_name text NOT NULL,
                captured_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (description_category_id, type_id, api_attribute_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ozon_assortment_api_attribute_map_canonical
                ON public.ozon_assortment_api_attribute_map(canonical_attribute_id);

            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_runs (
                run_id bigserial PRIMARY KEY,
                expected_products bigint,
                downloaded_products bigint NOT NULL DEFAULT 0,
                requests_count integer NOT NULL DEFAULT 0,
                inserted_products integer NOT NULL DEFAULT 0,
                enriched_products integer NOT NULL DEFAULT 0,
                inserted_attributes bigint NOT NULL DEFAULT 0,
                enriched_attributes bigint NOT NULL DEFAULT 0,
                status text NOT NULL,
                error text,
                started_at timestamptz NOT NULL DEFAULT now(),
                finished_at timestamptz
            );
            CREATE TABLE IF NOT EXISTS public.ozon_assortment_api_run_items (
                run_id bigint NOT NULL REFERENCES public.ozon_assortment_api_runs(run_id) ON DELETE CASCADE,
                ozon_product_id bigint NOT NULL,
                PRIMARY KEY (run_id, ozon_product_id)
            );
            ALTER TABLE public.ozon_assortment_api_runs
                ADD COLUMN IF NOT EXISTS last_id text,
                ADD COLUMN IF NOT EXISTS page_count integer NOT NULL DEFAULT 0;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ozon_cat_products_api_product_id
                ON public.ozon_cat_products(ozon_product_id)
                WHERE ozon_product_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_artikul_trim_soft
                ON public.ozon_cat_products((btrim(artikul)));
            CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_sku_trim_soft
                ON public.ozon_cat_products((btrim(sku)));
            CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_barcode_canonical_soft
                ON public.ozon_cat_products((regexp_replace(btrim(shtrihkod_seriynyy_nomer_ean), '\\.0+$', '')));
            """
        )
    conn.commit()


def _insert_run(conn) -> int:
    """RealDictCursor-safe run creation."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.ozon_assortment_api_runs(status) VALUES ('running') RETURNING run_id"
        )
        row = cur.fetchone()
        run_id = int(row["run_id"] if isinstance(row, dict) else row[0])
    conn.commit()
    return run_id


def _cursor_after_product_id(product_id: int) -> str:
    """Rebuild the documented Ozon ASC cursor shape for an old checkpoint."""
    raw = json.dumps([int(product_id), int(product_id)], separators=(",", ":")).encode("ascii")
    return base64.b64encode(raw).decode("ascii")


def resume_incomplete_run(conn, run_id: int) -> tuple[int, int, str, int, int]:
    """Resume an explicitly selected failed raw snapshot without rereading its saved pages."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.run_id, r.expected_products, r.downloaded_products, r.requests_count,
                   r.last_id, r.page_count, r.status,
                   count(i.ozon_product_id) AS item_count,
                   max(i.ozon_product_id) AS max_product_id
            FROM public.ozon_assortment_api_runs r
            LEFT JOIN public.ozon_assortment_api_run_items i ON i.run_id=r.run_id
            WHERE r.run_id=%s
            GROUP BY r.run_id
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise base.AssortmentPipelineError(f"Ozon run {run_id} не найден")
        expected = int(row["expected_products"] or 0)
        downloaded = int(row["downloaded_products"] or 0)
        item_count = int(row["item_count"] or 0)
        if row["status"] != "failed" or not expected or not 0 < downloaded < expected:
            raise base.AssortmentPipelineError(
                f"Ozon run {run_id} нельзя продолжить: status={row['status']}, downloaded={downloaded}, expected={expected}"
            )
        if item_count != downloaded:
            raise base.AssortmentPipelineError(
                f"Ozon run {run_id}: checkpoint={downloaded}, фактически сохранено={item_count}; продолжение запрещено"
            )
        last_id = base.text(row["last_id"])
        page_count = int(row["page_count"] or 0)
        if not last_id:
            if downloaded % PAGE_SIZE or not row["max_product_id"]:
                raise base.AssortmentPipelineError(
                    f"Ozon run {run_id}: старый checkpoint нельзя безопасно восстановить"
                )
            last_id = _cursor_after_product_id(int(row["max_product_id"]))
            page_count = downloaded // PAGE_SIZE
        cur.execute(
            """
            UPDATE public.ozon_assortment_api_runs
            SET status='running', error=NULL, finished_at=NULL, last_id=%s, page_count=%s
            WHERE run_id=%s
            """,
            (last_id, page_count, run_id),
        )
    conn.commit()
    return downloaded, expected, last_id, page_count, int(row["requests_count"] or 0)


def latest_retriable_incomplete_run(conn) -> int | None:
    """Return a recent network-failed snapshot that is safe to resume."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.run_id
            FROM public.ozon_assortment_api_runs r
            WHERE r.status='failed'
              AND r.started_at >= now() - interval '24 hours'
              AND r.expected_products > 0
              AND r.downloaded_products > 0
              AND r.downloaded_products < r.expected_products
              AND r.error ~* '(IncompleteRead|RemoteDisconnected|timed out|TimeoutError|ConnectionReset|HTTP (429|5[0-9]{2})|urlopen)'
              AND (SELECT count(*) FROM public.ozon_assortment_api_run_items i WHERE i.run_id=r.run_id)
                    = r.downloaded_products
            ORDER BY r.run_id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return int(row["run_id"]) if row else None


def latest_reusable_complete_failed_run(conn) -> int | None:
    """Return a recent complete failed snapshot only when no newer successful run exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.run_id
            FROM public.ozon_assortment_api_runs r
            WHERE r.status='failed'
              AND r.started_at >= now() - interval '24 hours'
              AND r.expected_products > 0
              AND r.downloaded_products = r.expected_products
              AND (SELECT count(*) FROM public.ozon_assortment_api_run_items i WHERE i.run_id=r.run_id)
                    = r.expected_products
              AND NOT EXISTS (
                  SELECT 1 FROM public.ozon_assortment_api_runs newer
                  WHERE newer.run_id > r.run_id AND newer.status='ok'
              )
            ORDER BY r.run_id DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
    return int(row["run_id"]) if row else None


def clone_latest_complete_run(conn, source_run_id: int | None = None) -> tuple[int, int]:
    """Create a merge run from the latest complete persisted API snapshot."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.run_id, r.expected_products,
                   (SELECT count(*) FROM public.ozon_assortment_api_run_items i WHERE i.run_id=r.run_id) AS item_count
            FROM public.ozon_assortment_api_runs r
            WHERE r.expected_products IS NOT NULL
              AND r.expected_products > 0
              AND r.downloaded_products = r.expected_products
              AND (%s IS NULL OR r.run_id=%s)
            ORDER BY r.run_id DESC
            LIMIT 1
            """,
            (source_run_id, source_run_id),
        )
        source = cur.fetchone()
        if not source or int(source["item_count"] or 0) != int(source["expected_products"] or 0):
            raise base.AssortmentPipelineError("Нет полного сохранённого Ozon snapshot для повторного merge")
        expected = int(source["expected_products"])
        run_id = _insert_run(conn)
        cur.execute(
            """
            INSERT INTO public.ozon_assortment_api_run_items(run_id, ozon_product_id)
            SELECT %s, ozon_product_id
            FROM public.ozon_assortment_api_run_items
            WHERE run_id=%s
            ON CONFLICT DO NOTHING
            """,
            (run_id, int(source["run_id"])),
        )
        cur.execute(
            """
            UPDATE public.ozon_assortment_api_runs
            SET expected_products=%s, downloaded_products=%s
            WHERE run_id=%s
            """,
            (expected, expected, run_id),
        )
    conn.commit()
    return run_id, expected


def connect_target():
    try:
        base.app.hydrate_registered_clients()
    except Exception:
        pass
    config = dict(base.app.read_db_config(CLIENT_KEY))
    config["database"] = TARGET_DB
    config["application_name"] = f"{CLIENT_KEY}_ozon_assortment_soft_update"
    conn = psycopg2.connect(**config, cursor_factory=RealDictCursor)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database() AS db")
        actual = cur.fetchone()["db"]
    if actual != TARGET_DB:
        conn.close()
        raise base.AssortmentPipelineError(f"Ожидалась БД {TARGET_DB}, подключена {actual}")
    return conn


def persist_page(conn, run_id: int, items: list[dict[str, Any]]) -> int:
    card_values = []
    item_values = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        product_id = int(item["id"])
        card_values.append(
            (
                product_id,
                base.text(item.get("offer_id")) or None,
                base.text(item.get("sku")) or None,
                int(item.get("description_category_id") or 0) or None,
                int(item.get("type_id") or 0) or None,
                Json(item),
            )
        )
        item_values.append((run_id, product_id))
    with conn.cursor() as cur:
        if card_values:
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_assortment_api_cards (
                    ozon_product_id, offer_id, sku, description_category_id, type_id, payload
                ) VALUES %s
                ON CONFLICT (ozon_product_id) DO UPDATE SET
                    offer_id = COALESCE(NULLIF(EXCLUDED.offer_id, ''), public.ozon_assortment_api_cards.offer_id),
                    sku = COALESCE(NULLIF(EXCLUDED.sku, ''), public.ozon_assortment_api_cards.sku),
                    description_category_id = COALESCE(EXCLUDED.description_category_id, public.ozon_assortment_api_cards.description_category_id),
                    type_id = COALESCE(EXCLUDED.type_id, public.ozon_assortment_api_cards.type_id),
                    captured_at = now(), payload = EXCLUDED.payload
                """,
                card_values,
                page_size=1000,
            )
            execute_values(
                cur,
                "INSERT INTO public.ozon_assortment_api_run_items(run_id, ozon_product_id) VALUES %s "
                "ON CONFLICT (run_id, ozon_product_id) DO NOTHING",
                item_values,
                page_size=1000,
            )
    conn.commit()
    return len(card_values)


def fetch_to_raw_cache(
    api: base.SellerApiClient,
    conn,
    run_id: int,
    started: float,
    *,
    initial_last_id: str = "",
    initial_loaded: int = 0,
    initial_expected: int = 0,
    initial_page: int = 0,
) -> tuple[int, int]:
    last_id = initial_last_id
    loaded = initial_loaded
    expected = initial_expected
    page = initial_page
    while True:
        page += 1
        payload = api.post(
            "/v4/product/info/attributes",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PAGE_SIZE, "sort_dir": "ASC"},
            label="ассортимент (мягкое обновление)",
        )
        items = [item for item in (payload.get("result") or []) if isinstance(item, dict)]
        payload_total = int(payload.get("total") or loaded + len(items))
        if initial_expected and payload_total != initial_expected:
            raise base.AssortmentPipelineError(
                f"Ozon catalog изменился во время resume: было {initial_expected:,}, стало {payload_total:,}; core не изменён"
            )
        expected = payload_total
        persist_page(conn, run_id, items)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM public.ozon_assortment_api_run_items WHERE run_id=%s",
                (run_id,),
            )
            current_loaded = int(cur.fetchone()["n"])
        persisted = current_loaded - loaded
        if persisted != len(items):
            raise base.AssortmentPipelineError(
                f"Ozon cursor пересёк уже сохранённые карточки: batch={len(items):,}, новых={persisted:,}; core не изменён"
            )
        loaded = current_loaded
        total_pages = max(1, math.ceil(expected / PAGE_SIZE))
        elapsed = time.monotonic() - started
        eta = elapsed / page * max(total_pages - page, 0)
        new_last_id = base.text(payload.get("last_id"))
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.ozon_assortment_api_runs
                SET expected_products=%s, downloaded_products=%s, requests_count=%s,
                    last_id=%s, page_count=%s
                WHERE run_id=%s
                """,
                (expected, loaded, api.request_count, new_last_id or last_id, page, run_id),
            )
        conn.commit()
        print(
            f"ПРОГРЕСС: Ozon API | request={api.request_count} | page={page}/{total_pages} | "
            f"batch={len(items):,} | persisted={persisted:,} | accumulated={loaded:,}/{expected:,} | "
            f"elapsed={base.duration(elapsed)} | ETA={base.duration(eta)} | step_pct={min(100, loaded / max(expected, 1) * 100):.1f}",
            flush=True,
        )
        if not items or loaded >= expected:
            break
        if not new_last_id or new_last_id == last_id:
            raise base.AssortmentPipelineError("Ozon вернул неустойчивый last_id; core-ассортимент не изменён")
        last_id = new_last_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM public.ozon_assortment_api_run_items WHERE run_id=%s",
            (run_id,),
        )
        unique_loaded = int(cur.fetchone()["n"])
    if not unique_loaded or (expected and unique_loaded != expected):
        raise base.AssortmentPipelineError(
            f"Ozon snapshot неполный: сохранено {unique_loaded:,} из ожидаемых {expected:,}; core не изменён"
        )
    return unique_loaded, expected


def run_pairs(conn, run_id: int) -> list[tuple[int, int]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT c.description_category_id, c.type_id
            FROM public.ozon_assortment_api_run_items r
            JOIN public.ozon_assortment_api_cards c ON c.ozon_product_id = r.ozon_product_id
            WHERE r.run_id=%s AND c.description_category_id IS NOT NULL AND c.type_id IS NOT NULL
            ORDER BY 1, 2
            """,
            (run_id,),
        )
        return [(int(row["description_category_id"]), int(row["type_id"])) for row in cur.fetchall()]


def cached_definition_pairs(conn) -> set[tuple[int, int]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT description_category_id, type_id FROM public.ozon_assortment_api_attribute_definitions"
        )
        return {(int(row["description_category_id"]), int(row["type_id"])) for row in cur.fetchall()}


def unavailable_definition_pairs(conn) -> set[tuple[int, int]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT description_category_id, type_id
            FROM public.ozon_assortment_api_unavailable_pairs
            WHERE is_unavailable
            """
        )
        return {(int(row["description_category_id"]), int(row["type_id"])) for row in cur.fetchall()}


def fetch_missing_definitions(
    api: base.SellerApiClient,
    conn,
    pairs: list[tuple[int, int]],
    started: float,
    *,
    refresh: bool,
) -> int:
    existing = set() if refresh else cached_definition_pairs(conn)
    unavailable = set() if refresh else unavailable_definition_pairs(conn)
    missing = [pair for pair in pairs if pair not in existing and pair not in unavailable]
    for index, (category_id, type_id) in enumerate(missing, start=1):
        try:
            payload = api.post(
                "/v1/description-category/attribute",
                {"description_category_id": category_id, "type_id": type_id, "language": "DEFAULT"},
                label=f"характеристики {category_id}/{type_id}",
            )
        except base.AssortmentPipelineError as exc:
            detail = str(exc)
            if "HTTP 400" not in detail or "category with level_3_id=" not in detail or "is not found" not in detail:
                raise
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.ozon_assortment_api_unavailable_pairs(
                        description_category_id, type_id, reason, is_unavailable
                    ) VALUES (%s, %s, %s, true)
                    ON CONFLICT (description_category_id, type_id) DO UPDATE SET
                        reason=EXCLUDED.reason, is_unavailable=true, captured_at=now()
                    """,
                    (category_id, type_id, detail[:1200]),
                )
            conn.commit()
            unavailable.add((category_id, type_id))
            print(
                f"ПРОГРЕСС: Ozon справочники | {index}/{len(missing)} | pair={category_id}/{type_id} | "
                "status=unavailable_in_ozon | raw_preserved=yes | normalized_attributes=skipped",
                flush=True,
            )
            continue
        definitions = [item for item in (payload.get("result") or []) if isinstance(item, dict) and item.get("id") is not None]
        values = [
            (category_id, type_id, int(item["id"]), base.text(item.get("name")) or str(item["id"]), Json(item))
            for item in definitions
        ]
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.ozon_assortment_api_unavailable_pairs
                SET is_unavailable=false, reason='resolved by Ozon dictionary', captured_at=now()
                WHERE description_category_id=%s AND type_id=%s AND is_unavailable
                """,
                (category_id, type_id),
            )
            if values:
                execute_values(
                    cur,
                    """
                    INSERT INTO public.ozon_assortment_api_attribute_definitions (
                        description_category_id, type_id, attribute_id, attribute_name, payload
                    ) VALUES %s
                    ON CONFLICT (description_category_id, type_id, attribute_id) DO UPDATE SET
                        attribute_name=EXCLUDED.attribute_name, captured_at=now(), payload=EXCLUDED.payload
                    """,
                    values,
                    page_size=1000,
                )
        conn.commit()
        elapsed = time.monotonic() - started
        eta = elapsed / index * max(len(missing) - index, 0)
        print(
            f"ПРОГРЕСС: Ozon справочники | {index}/{len(missing)} | pair={category_id}/{type_id} | "
            f"attributes={len(values):,} | requests={api.request_count} | elapsed={base.duration(elapsed)} | "
            f"ETA={base.duration(eta)} | step_pct={index / max(len(missing), 1) * 100:.1f}",
            flush=True,
        )
    if not missing:
        print(
            f"ПРОГРЕСС: Ozon справочники | cache_hit={len(pairs):,}/{len(pairs):,} | requests_added=0 | step_pct=100.0",
            flush=True,
        )
    return len(missing)


def load_definitions(conn, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    pair_set = set(pairs)
    result: dict[tuple[int, int], list[dict[str, Any]]] = {pair: [] for pair in pairs}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT description_category_id, type_id, payload
            FROM public.ozon_assortment_api_attribute_definitions
            ORDER BY description_category_id, type_id, attribute_id
            """
        )
        for row in cur.fetchall():
            pair = (int(row["description_category_id"]), int(row["type_id"]))
            if pair in pair_set and isinstance(row.get("payload"), dict):
                result[pair].append(dict(row["payload"]))
    return result


def load_run_batch(conn, run_id: int, offset: int, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.payload
            FROM public.ozon_assortment_api_run_items r
            JOIN public.ozon_assortment_api_cards c ON c.ozon_product_id = r.ozon_product_id
            WHERE r.run_id=%s
            ORDER BY r.ozon_product_id
            OFFSET %s LIMIT %s
            """,
            (run_id, offset, limit),
        )
        return [dict(row["payload"]) for row in cur.fetchall() if isinstance(row.get("payload"), dict)]


def category_attribute_rows(
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
    categories: dict[int, str],
    attribute_map: dict[tuple[int, int, int], int],
) -> list[tuple[str, int, str]]:
    rows: dict[tuple[str, int], str] = {}
    for pair, items in definitions.items():
        category_name = categories.get(pair[0]) or f"Ozon category {pair[0]}"
        for item in items:
            if item.get("id") is None:
                continue
            name = base.text(item.get("name")) or str(item["id"])
            if base.normalize_name(name) in base.COMMON_FIELD_BY_ATTRIBUTE:
                continue
            canonical_id = attribute_map.get((pair[0], pair[1], int(item["id"])))
            if canonical_id is not None:
                rows[(category_name, canonical_id)] = name
    return [(category_name, attr_id, name) for (category_name, attr_id), name in sorted(rows.items())]


def ensure_categories_and_attributes(
    conn,
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
    categories: dict[int, str],
) -> dict[tuple[int, int, int], int]:
    specs: list[tuple[tuple[int, int], int, str, str]] = []
    for pair, items in definitions.items():
        for item in items:
            if item.get("id") is None:
                continue
            api_attribute_id = int(item["id"])
            attribute_name = base.text(item.get("name")) or str(api_attribute_id)
            scope = (
                "common"
                if base.normalize_name(attribute_name) in base.COMMON_FIELD_BY_ATTRIBUTE
                else "category_specific"
            )
            specs.append((pair, api_attribute_id, attribute_name, scope))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT attribute_id, attribute_name FROM public.ozon_cat_attribute_definitions"
        )
        existing = cur.fetchall()
        name_to_id = {base.text(row["attribute_name"]): int(row["attribute_id"]) for row in existing}
        used_ids = {int(row["attribute_id"]) for row in existing}
        next_synthetic_id = min([0, *[value for value in used_ids if value < 0]]) - 1
        pending: dict[str, tuple[int, str]] = {}
        for _pair, api_attribute_id, attribute_name, scope in sorted(specs):
            if attribute_name in name_to_id or attribute_name in pending:
                continue
            if -(2**31) <= api_attribute_id <= 2**31 - 1 and api_attribute_id not in used_ids:
                canonical_id = api_attribute_id
            else:
                while next_synthetic_id in used_ids:
                    next_synthetic_id -= 1
                canonical_id = next_synthetic_id
                next_synthetic_id -= 1
            used_ids.add(canonical_id)
            pending[attribute_name] = (canonical_id, scope)
        if pending:
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_cat_attribute_definitions(
                    attribute_id, attribute_name, attribute_scope, data_type
                ) VALUES %s
                ON CONFLICT DO NOTHING
                """,
                [
                    (canonical_id, name, scope, "text")
                    for name, (canonical_id, scope) in sorted(pending.items())
                ],
                page_size=5000,
            )
        cur.execute(
            "SELECT attribute_id, attribute_name FROM public.ozon_cat_attribute_definitions"
        )
        name_to_id = {base.text(row["attribute_name"]): int(row["attribute_id"]) for row in cur.fetchall()}
        missing_names = {name for _pair, _api_id, name, _scope in specs if name not in name_to_id}
        if missing_names:
            raise base.AssortmentPipelineError(
                f"Не удалось canonicalize {len(missing_names)} Ozon attribute names; core не изменён"
            )
        attribute_map = {
            (pair[0], pair[1], api_attribute_id): name_to_id[attribute_name]
            for pair, api_attribute_id, attribute_name, _scope in specs
        }
        if attribute_map:
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_assortment_api_attribute_map(
                    description_category_id, type_id, api_attribute_id,
                    canonical_attribute_id, attribute_name
                ) VALUES %s
                ON CONFLICT (description_category_id, type_id, api_attribute_id) DO UPDATE SET
                    canonical_attribute_id=EXCLUDED.canonical_attribute_id,
                    attribute_name=EXCLUDED.attribute_name,
                    captured_at=now()
                """,
                [
                    (pair[0], pair[1], api_id, canonical_id, name)
                    for pair, api_id, name, _scope in specs
                    for canonical_id in [attribute_map[(pair[0], pair[1], api_id)]]
                ],
                page_size=5000,
            )
        common_values = sorted(
            {
                (attribute_map[(pair[0], pair[1], api_id)], name)
                for pair, api_id, name, scope in specs
                if scope == "common"
            }
        )
        if common_values:
            execute_values(
                cur,
                "INSERT INTO public.ozon_cat_common_attributes(attribute_id, attribute_name) VALUES %s "
                "ON CONFLICT DO NOTHING",
                common_values,
                page_size=1000,
            )
        rows = category_attribute_rows(definitions, categories, attribute_map)
        category_names = sorted({name for name, _attr_id, _attr_name in rows} | set(categories.values()))
        if category_names:
            execute_values(
                cur,
                "INSERT INTO public.ozon_cat_categories(category_name) VALUES %s "
                "ON CONFLICT (category_name) DO NOTHING",
                [(name,) for name in category_names if name],
                page_size=1000,
            )
        if rows:
            execute_values(
                cur,
                """
                INSERT INTO public.ozon_cat_category_attributes(category_id, attribute_id, attribute_name)
                SELECT c.category_id, v.attribute_id, v.attribute_name
                FROM (VALUES %s) AS v(category_name, attribute_id, attribute_name)
                JOIN public.ozon_cat_categories c ON c.category_name=v.category_name
                ON CONFLICT (category_id, attribute_id) DO UPDATE SET
                    attribute_name=COALESCE(NULLIF(public.ozon_cat_category_attributes.attribute_name, ''), EXCLUDED.attribute_name)
                """,
                rows,
                page_size=5000,
                template="(%s,%s,%s)",
            )
    conn.commit()
    return attribute_map


def create_stage_tables(cur) -> tuple[list[str], list[str]]:
    common_columns = [column for _name, column in base.COMMON_COLUMNS]
    product_columns = ["api_product_id", *common_columns]
    extra_columns = [
        "description_category_id", "category_name", "type_id", "barcodes_json", "images_json",
        "model_info_json", "attributes_json", "complex_attributes_json", "raw_json",
    ]
    product_columns.extend(extra_columns)
    definitions = [sql.SQL("api_product_id bigint PRIMARY KEY")]
    definitions.extend(sql.SQL("{} text").format(sql.Identifier(column)) for column in common_columns)
    definitions.extend(
        [
            sql.SQL("description_category_id bigint"),
            sql.SQL("category_name text"),
            sql.SQL("type_id bigint"),
            sql.SQL("barcodes_json jsonb"),
            sql.SQL("images_json jsonb"),
            sql.SQL("model_info_json jsonb"),
            sql.SQL("attributes_json jsonb"),
            sql.SQL("complex_attributes_json jsonb"),
            sql.SQL("raw_json jsonb"),
        ]
    )
    cur.execute(
        sql.SQL("CREATE TEMP TABLE tmp_ozon_api_products ({}) ON COMMIT DROP").format(sql.SQL(", ").join(definitions))
    )
    cur.execute(
        """
        CREATE TEMP TABLE tmp_ozon_api_attributes (
            api_product_id bigint NOT NULL,
            attribute_id bigint NOT NULL,
            value_text text NOT NULL,
            PRIMARY KEY(api_product_id, attribute_id)
        ) ON COMMIT DROP
        """
    )
    return product_columns, common_columns


def soft_product_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Project the replace-import row into the smaller soft-update staging schema."""
    common_count = len(base.COMMON_COLUMNS)
    common_values = row[1:1 + common_count]
    tail = row[1 + common_count:]
    if len(tail) < 15:
        raise base.AssortmentPipelineError("Неожиданная схема product row в Ozon soft-update")
    return (
        row[0],
        *common_values,
        tail[7],   # description_category_id
        tail[1],   # category_name
        tail[8],   # type_id
        tail[9],   # barcodes_json
        tail[10],  # images_json
        tail[11],  # model_info_json
        tail[12],  # attributes_json
        tail[13],  # complex_attributes_json
        tail[14],  # raw_json
    )


def stage_batch(
    cur,
    products: list[dict[str, Any]],
    categories: dict[int, str],
    types: dict[tuple[int, int], str],
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
    unavailable_pairs: set[tuple[int, int]],
    attribute_map: dict[tuple[int, int, int], int],
) -> tuple[list[str], list[str]]:
    product_columns, common_columns = create_stage_tables(cur)
    product_rows, attribute_rows, _common_rows, _category_rows = base.prepare_rows(
        products, {}, categories, types, definitions
    )
    product_pairs = {
        int(item["id"]): (
            int(item.get("description_category_id") or 0),
            int(item.get("type_id") or 0),
        )
        for item in products
        if item.get("id") is not None
    }
    canonical_attribute_rows = []
    for product_id, api_attribute_id, value_text in attribute_rows:
        pair = product_pairs.get(int(product_id))
        if not pair or pair in unavailable_pairs:
            continue
        canonical_id = attribute_map.get((pair[0], pair[1], int(api_attribute_id)))
        if canonical_id is not None:
            canonical_attribute_rows.append((product_id, canonical_id, value_text))
    attribute_rows = canonical_attribute_rows
    values = [soft_product_row(row) for row in product_rows]
    if values:
        execute_values(
            cur,
            sql.SQL("INSERT INTO tmp_ozon_api_products ({}) VALUES %s").format(
                sql.SQL(", ").join(map(sql.Identifier, product_columns))
            ).as_string(cur),
            values,
            page_size=1000,
        )
    if attribute_rows:
        execute_values(
            cur,
            "INSERT INTO tmp_ozon_api_attributes VALUES %s ON CONFLICT DO NOTHING",
            attribute_rows,
            page_size=10000,
        )
    return product_columns, common_columns


def merge_batch(
    conn,
    products: list[dict[str, Any]],
    categories: dict[int, str],
    types: dict[tuple[int, int], str],
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
    unavailable_pairs: set[tuple[int, int]],
    attribute_map: dict[tuple[int, int, int], int],
) -> dict[str, int]:
    stats = {"inserted_products": 0, "enriched_products": 0, "inserted_attributes": 0, "enriched_attributes": 0}
    try:
        with conn.cursor() as cur:
            product_columns, common_columns = stage_batch(
                cur, products, categories, types, definitions, unavailable_pairs, attribute_map
            )
            cur.execute(
                """
                CREATE TEMP TABLE tmp_ozon_api_match (
                    api_product_id bigint PRIMARY KEY,
                    core_product_id bigint
                ) ON COMMIT DROP;
                INSERT INTO tmp_ozon_api_match(api_product_id)
                SELECT api_product_id FROM tmp_ozon_api_products;
                ANALYZE tmp_ozon_api_products;
                ANALYZE tmp_ozon_api_match;
                """
            )
            cur.execute(
                """
                UPDATE tmp_ozon_api_match m
                SET core_product_id=p.product_id
                FROM public.ozon_cat_products p
                WHERE m.core_product_id IS NULL
                  AND p.ozon_product_id=m.api_product_id
                """
            )
            for stage_sql in (
                """
                WITH candidates AS (
                    SELECT DISTINCT ON (s.api_product_id) s.api_product_id, p.product_id
                    FROM tmp_ozon_api_products s
                    JOIN tmp_ozon_api_match m ON m.api_product_id=s.api_product_id AND m.core_product_id IS NULL
                    JOIN public.ozon_cat_products p ON btrim(p.artikul)=btrim(s.artikul)
                    WHERE nullif(btrim(s.artikul), '') IS NOT NULL
                    ORDER BY s.api_product_id, p.product_id
                )
                UPDATE tmp_ozon_api_match m SET core_product_id=c.product_id
                FROM candidates c
                WHERE m.api_product_id=c.api_product_id AND m.core_product_id IS NULL
                """,
                """
                WITH candidates AS (
                    SELECT DISTINCT ON (s.api_product_id) s.api_product_id, p.product_id
                    FROM tmp_ozon_api_products s
                    JOIN tmp_ozon_api_match m ON m.api_product_id=s.api_product_id AND m.core_product_id IS NULL
                    JOIN public.ozon_cat_products p ON btrim(p.sku)=btrim(s.sku)
                    WHERE nullif(btrim(s.sku), '') IS NOT NULL
                    ORDER BY s.api_product_id, p.product_id
                )
                UPDATE tmp_ozon_api_match m SET core_product_id=c.product_id
                FROM candidates c
                WHERE m.api_product_id=c.api_product_id AND m.core_product_id IS NULL
                """,
                """
                WITH candidates AS (
                    SELECT DISTINCT ON (s.api_product_id) s.api_product_id, p.product_id
                    FROM tmp_ozon_api_products s
                    JOIN tmp_ozon_api_match m ON m.api_product_id=s.api_product_id AND m.core_product_id IS NULL
                    JOIN public.ozon_cat_products p
                      ON regexp_replace(btrim(p.shtrihkod_seriynyy_nomer_ean), '\\.0+$', '') =
                         regexp_replace(btrim(s.shtrihkod_seriynyy_nomer_ean), '\\.0+$', '')
                    WHERE nullif(btrim(s.shtrihkod_seriynyy_nomer_ean), '') IS NOT NULL
                    ORDER BY s.api_product_id, p.product_id
                )
                UPDATE tmp_ozon_api_match m SET core_product_id=c.product_id
                FROM candidates c
                WHERE m.api_product_id=c.api_product_id AND m.core_product_id IS NULL
                """,
            ):
                cur.execute(stage_sql)
            missing_conditions = [
                sql.SQL("(nullif(trim(p.{}), '') IS NULL AND nullif(trim(s.{}), '') IS NOT NULL)").format(
                    sql.Identifier(column), sql.Identifier(column)
                )
                for column in common_columns
            ]
            missing_conditions.extend(
                [
                    sql.SQL("(p.category_id IS NULL AND cat.category_id IS NOT NULL)"),
                    sql.SQL("(nullif(trim(p.category_name), '') IS NULL AND nullif(trim(s.category_name), '') IS NOT NULL)"),
                    sql.SQL("(p.ozon_product_id IS NULL AND s.api_product_id IS NOT NULL)"),
                    sql.SQL("(p.description_category_id IS NULL AND s.description_category_id IS NOT NULL)"),
                    sql.SQL("(p.type_id IS NULL AND s.type_id IS NOT NULL)"),
                    sql.SQL("(COALESCE(p.barcodes_json, '[]'::jsonb) = '[]'::jsonb AND COALESCE(s.barcodes_json, '[]'::jsonb) <> '[]'::jsonb)"),
                    sql.SQL("(COALESCE(p.images_json, '[]'::jsonb) = '[]'::jsonb AND COALESCE(s.images_json, '[]'::jsonb) <> '[]'::jsonb)"),
                    sql.SQL("(COALESCE(p.model_info_json, '{}'::jsonb) = '{}'::jsonb AND COALESCE(s.model_info_json, '{}'::jsonb) <> '{}'::jsonb)"),
                    sql.SQL("(COALESCE(p.attributes_json, '[]'::jsonb) = '[]'::jsonb AND COALESCE(s.attributes_json, '[]'::jsonb) <> '[]'::jsonb)"),
                    sql.SQL("(COALESCE(p.complex_attributes_json, '[]'::jsonb) = '[]'::jsonb AND COALESCE(s.complex_attributes_json, '[]'::jsonb) <> '[]'::jsonb)"),
                    sql.SQL("(COALESCE(p.raw_json, '{}'::jsonb) = '{}'::jsonb AND COALESCE(s.raw_json, '{}'::jsonb) <> '{}'::jsonb)"),
                ]
            )
            cur.execute(
                sql.SQL(
                    "SELECT count(*) AS n FROM tmp_ozon_api_match m "
                    "JOIN public.ozon_cat_products p ON p.product_id=m.core_product_id "
                    "JOIN tmp_ozon_api_products s ON s.api_product_id=m.api_product_id "
                    "LEFT JOIN public.ozon_cat_categories cat ON cat.category_name=s.category_name "
                    "WHERE {}"
                ).format(sql.SQL(" OR ").join(missing_conditions))
            )
            stats["enriched_products"] = int(cur.fetchone()["n"] or 0)

            update_assignments = [
                sql.SQL("{col}=COALESCE(NULLIF(trim(p.{col}), ''), s.{col})").format(col=sql.Identifier(column))
                for column in common_columns
            ]
            update_assignments.extend(
                [
                    sql.SQL("category_id=COALESCE(p.category_id, cat.category_id)"),
                    sql.SQL("category_name=COALESCE(NULLIF(trim(p.category_name), ''), s.category_name)"),
                    sql.SQL("ozon_product_id=COALESCE(p.ozon_product_id, s.api_product_id)"),
                    sql.SQL("description_category_id=COALESCE(p.description_category_id, s.description_category_id)"),
                    sql.SQL("type_id=COALESCE(p.type_id, s.type_id)"),
                    sql.SQL("barcodes_json=COALESCE(NULLIF(p.barcodes_json, '[]'::jsonb), NULLIF(s.barcodes_json, '[]'::jsonb), p.barcodes_json, s.barcodes_json)"),
                    sql.SQL("images_json=COALESCE(NULLIF(p.images_json, '[]'::jsonb), NULLIF(s.images_json, '[]'::jsonb), p.images_json, s.images_json)"),
                    sql.SQL("model_info_json=COALESCE(NULLIF(p.model_info_json, '{}'::jsonb), NULLIF(s.model_info_json, '{}'::jsonb), p.model_info_json, s.model_info_json)"),
                    sql.SQL("attributes_json=COALESCE(NULLIF(p.attributes_json, '[]'::jsonb), NULLIF(s.attributes_json, '[]'::jsonb), p.attributes_json, s.attributes_json)"),
                    sql.SQL("complex_attributes_json=COALESCE(NULLIF(p.complex_attributes_json, '[]'::jsonb), NULLIF(s.complex_attributes_json, '[]'::jsonb), p.complex_attributes_json, s.complex_attributes_json)"),
                    sql.SQL("raw_json=COALESCE(NULLIF(p.raw_json, '{}'::jsonb), NULLIF(s.raw_json, '{}'::jsonb), p.raw_json, s.raw_json)"),
                    sql.SQL("api_updated_at=now()"),
                    sql.SQL("updated_at=CASE WHEN ({}) THEN now() ELSE p.updated_at END").format(
                        sql.SQL(" OR ").join(missing_conditions)
                    ),
                ]
            )
            cur.execute(
                sql.SQL(
                    "UPDATE public.ozon_cat_products p SET {} "
                    "FROM tmp_ozon_api_match m "
                    "JOIN tmp_ozon_api_products s ON s.api_product_id=m.api_product_id "
                    "LEFT JOIN public.ozon_cat_categories cat ON cat.category_name=s.category_name "
                    "WHERE p.product_id=m.core_product_id"
                ).format(sql.SQL(", ").join(update_assignments))
            )

            core_insert_columns = [*common_columns, "category_id", "category_name", "import_file", "source_row_num", "imported_at", "updated_at",
                                   "ozon_product_id", "description_category_id", "type_id", "barcodes_json", "images_json", "model_info_json",
                                   "attributes_json", "complex_attributes_json", "raw_json", "api_updated_at"]
            select_values = [sql.SQL("s.{}").format(sql.Identifier(column)) for column in common_columns]
            select_values.extend(
                [
                    sql.SQL("cat.category_id"), sql.SQL("s.category_name"), sql.Literal(RAW_SOURCE), sql.SQL("NULL"),
                    sql.SQL("now()"), sql.SQL("now()"), sql.SQL("s.api_product_id"), sql.SQL("s.description_category_id"),
                    sql.SQL("s.type_id"), sql.SQL("s.barcodes_json"), sql.SQL("s.images_json"), sql.SQL("s.model_info_json"),
                    sql.SQL("s.attributes_json"), sql.SQL("s.complex_attributes_json"), sql.SQL("s.raw_json"), sql.SQL("now()"),
                ]
            )
            cur.execute(
                sql.SQL(
                    "INSERT INTO public.ozon_cat_products ({}) "
                    "SELECT {} FROM tmp_ozon_api_products s "
                    "JOIN tmp_ozon_api_match m ON m.api_product_id=s.api_product_id "
                    "LEFT JOIN public.ozon_cat_categories cat ON cat.category_name=s.category_name "
                    "WHERE m.core_product_id IS NULL"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, core_insert_columns)),
                    sql.SQL(", ").join(select_values),
                )
            )
            stats["inserted_products"] = cur.rowcount

            cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE pa.product_id IS NULL) AS missing,
                    count(*) FILTER (WHERE pa.product_id IS NOT NULL AND nullif(trim(pa.value_text), '') IS NULL) AS blank
                FROM tmp_ozon_api_attributes a
                JOIN public.ozon_cat_products p ON p.ozon_product_id=a.api_product_id
                LEFT JOIN public.ozon_cat_product_attributes pa
                  ON pa.product_id=p.product_id AND pa.attribute_id=a.attribute_id
                """
            )
            counts = cur.fetchone()
            stats["inserted_attributes"] = int(counts["missing"] or 0)
            stats["enriched_attributes"] = int(counts["blank"] or 0)
            cur.execute(
                """
                INSERT INTO public.ozon_cat_product_attributes(product_id, attribute_id, value_text)
                SELECT p.product_id, a.attribute_id, a.value_text
                FROM tmp_ozon_api_attributes a
                JOIN public.ozon_cat_products p ON p.ozon_product_id=a.api_product_id
                ON CONFLICT (product_id, attribute_id) DO UPDATE SET value_text=EXCLUDED.value_text
                WHERE nullif(trim(public.ozon_cat_product_attributes.value_text), '') IS NULL
                  AND nullif(trim(EXCLUDED.value_text), '') IS NOT NULL
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return stats


def merge_run(
    conn,
    run_id: int,
    total: int,
    categories: dict[int, str],
    types: dict[tuple[int, int], str],
    definitions: dict[tuple[int, int], list[dict[str, Any]]],
    unavailable_pairs: set[tuple[int, int]],
    attribute_map: dict[tuple[int, int, int], int],
    started: float,
    *,
    batch_size: int = 1000,
) -> dict[str, int]:
    totals = {"inserted_products": 0, "enriched_products": 0, "inserted_attributes": 0, "enriched_attributes": 0}
    batches = max(1, math.ceil(total / batch_size))
    for index, offset in enumerate(range(0, total, batch_size), start=1):
        products = load_run_batch(conn, run_id, offset, batch_size)
        result = merge_batch(
            conn, products, categories, types, definitions, unavailable_pairs, attribute_map
        )
        for key in totals:
            totals[key] += int(result.get(key, 0))
        elapsed = time.monotonic() - started
        eta = elapsed / index * max(batches - index, 0)
        print(
            f"ПРОГРЕСС: Ozon БД | batch={index}/{batches} ({index / batches * 100:.1f}%) | "
            f"products={min(index * batch_size, total):,}/{total:,} | inserted={totals['inserted_products']:,} | "
            f"enriched={totals['enriched_products']:,} | attributes={totals['inserted_attributes'] + totals['enriched_attributes']:,} | "
            f"elapsed={base.duration(elapsed)} | ETA={base.duration(eta)}",
            flush=True,
        )
    with conn.cursor() as cur:
        cur.execute("ANALYZE public.ozon_cat_products")
        cur.execute("ANALYZE public.ozon_cat_product_attributes")
    conn.commit()
    return totals


def finish_run(conn, run_id: int, api: base.SellerApiClient, stats: dict[str, int], status: str, error: str = "") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.ozon_assortment_api_runs
            SET requests_count=%s, inserted_products=%s, enriched_products=%s,
                inserted_attributes=%s, enriched_attributes=%s,
                status=%s, error=%s, finished_at=now()
            WHERE run_id=%s
            """,
            (
                api.request_count,
                stats.get("inserted_products", 0),
                stats.get("enriched_products", 0),
                stats.get("inserted_attributes", 0),
                stats.get("enriched_attributes", 0),
                status,
                error or None,
                run_id,
            ),
        )
    conn.commit()


def rebuild_views() -> None:
    command = [sys.executable, "-X", "utf8", "-u", str(PROJECT_ROOT / "scripts" / "rebuild_ozon_sku_scoring_view.py")]
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), env=os.environ.copy(), check=False)
    if result.returncode:
        raise base.AssortmentPipelineError(f"Пересборка Ozon-витрин завершилась с кодом {result.returncode}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-dictionaries", action="store_true", help="Обновить все category/type dictionaries")
    parser.add_argument("--skip-views", action="store_true", help="Не пересобирать Ozon assortment views")
    parser.add_argument(
        "--reuse-latest-cache",
        action="store_true",
        help="Повторить merge из последнего полного raw-snapshot без повторной выгрузки товарных страниц",
    )
    parser.add_argument(
        "--resume-run-id",
        type=int,
        help="Продолжить конкретный failed raw-snapshot с последнего сохранённого Ozon cursor",
    )
    parser.add_argument(
        "--resume-latest-incomplete",
        action="store_true",
        help="Автоматически продолжить последний сетевой failed snapshot не старше 24 часов",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    args = parse_args(argv)
    started = time.monotonic()
    conn = connect_target()
    api = ReadOnlySellerApiClient()
    run_id = 0
    stats: dict[str, int] = {}
    downloaded = 0
    try:
        ensure_soft_schema(conn)
        if args.reuse_latest_cache and (args.resume_run_id or args.resume_latest_incomplete):
            raise base.AssortmentPipelineError("Нельзя одновременно использовать --reuse-latest-cache и --resume-run-id")
        resume_state: tuple[int, int, str, int, int] | None = None
        resume_run_id = args.resume_run_id
        reuse_cached_snapshot = bool(args.reuse_latest_cache)
        reusable_complete_run_id: int | None = None
        if not resume_run_id and args.resume_latest_incomplete:
            resume_run_id = latest_retriable_incomplete_run(conn)
            if not resume_run_id:
                reusable_complete_run_id = latest_reusable_complete_failed_run(conn)
        if args.reuse_latest_cache:
            run_id, downloaded = clone_latest_complete_run(conn)
        elif reusable_complete_run_id:
            run_id, downloaded = clone_latest_complete_run(conn, reusable_complete_run_id)
            reuse_cached_snapshot = True
        elif resume_run_id:
            run_id = int(resume_run_id)
            resume_state = resume_incomplete_run(conn, run_id)
            downloaded = resume_state[0]
            api.request_count = resume_state[4]
        else:
            run_id = _insert_run(conn)
        print(
            f"ПЛАН: Ozon ассортимент | клиент={CLIENT_KEY} | БД={TARGET_DB} | page_size={PAGE_SIZE} | "
            f"интервал>={base.REQUEST_INTERVAL_SECONDS:.2f}s | API только чтение | цены не запрашиваются | "
            "страницы сначала сохраняются в raw-cache БД | затем insert новых SKU + заполнение пустых полей/характеристик | "
            "удалений=0 | непустые значения не перезаписываются | 429=Retry-After",
            flush=True,
        )
        if reuse_cached_snapshot:
            expected = downloaded
            print(
                f"ПРОГРЕСС: Ozon raw-cache | reuse={downloaded:,} | product_requests=0 | step_pct=100.0",
                flush=True,
            )
        elif resume_state:
            initial_loaded, initial_expected, initial_last_id, initial_page, _ = resume_state
            print(
                f"ПРОГРЕСС: Ozon raw-cache | resume_run={run_id} | saved={initial_loaded:,}/{initial_expected:,} | "
                f"next_page={initial_page + 1} | product_requests_before={api.request_count}",
                flush=True,
            )
            downloaded, expected = fetch_to_raw_cache(
                api,
                conn,
                run_id,
                started,
                initial_last_id=initial_last_id,
                initial_loaded=initial_loaded,
                initial_expected=initial_expected,
                initial_page=initial_page,
            )
        else:
            downloaded, expected = fetch_to_raw_cache(api, conn, run_id, started)
        categories, types = base.fetch_category_tree(api)
        pairs = run_pairs(conn, run_id)
        for category_id, _type_id in pairs:
            categories.setdefault(category_id, f"Ozon category {category_id}")
        fetch_missing_definitions(api, conn, pairs, started, refresh=args.refresh_dictionaries)
        definitions = load_definitions(conn, pairs)
        unavailable_pairs = unavailable_definition_pairs(conn) & set(pairs)
        missing_pairs = [pair for pair in pairs if not definitions.get(pair) and pair not in unavailable_pairs]
        if missing_pairs:
            raise base.AssortmentPipelineError(
                f"Нет определения характеристик для {len(missing_pairs)} category/type; core не изменён"
            )
        if unavailable_pairs:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS n
                    FROM public.ozon_assortment_api_run_items r
                    JOIN public.ozon_assortment_api_cards c ON c.ozon_product_id=r.ozon_product_id
                    WHERE r.run_id=%s
                      AND (c.description_category_id, c.type_id) IN (
                          SELECT description_category_id, type_id
                          FROM public.ozon_assortment_api_unavailable_pairs
                          WHERE is_unavailable
                      )
                    """,
                    (run_id,),
                )
                unavailable_products = int(cur.fetchone()["n"] or 0)
            print(
                f"ПРОГРЕСС: Ozon справочники | unavailable_pairs={len(unavailable_pairs):,} | "
                f"products_raw_only={unavailable_products:,} | core_products_preserved=yes",
                flush=True,
            )
        attribute_map = ensure_categories_and_attributes(conn, definitions, categories)
        print(
            f"ПРОГРЕСС: Ozon canonical attributes | api_mappings={len(attribute_map):,} | "
            f"synthetic_ids={len({value for value in attribute_map.values() if value < 0}):,}",
            flush=True,
        )
        stats = merge_run(
            conn,
            run_id,
            expected,
            categories,
            types,
            definitions,
            unavailable_pairs,
            attribute_map,
            started,
        )
        if not args.skip_views and any(stats.values()):
            print("ПРОГРЕСС: Ozon витрины | запускаю безопасную пересборку | step_pct=95.0", flush=True)
            rebuild_views()
        finish_run(conn, run_id, api, stats, "ok")
    except Exception as exc:
        if run_id:
            try:
                finish_run(conn, run_id, api, stats, "failed", str(exc)[:1200])
            except Exception:
                conn.rollback()
        print(
            f"ИТОГ: Ozon ассортимент | status=failed | downloaded={downloaded:,} | requests={api.request_count} | "
            f"errors=1 | partial=yes | downloaded_data_preserved=yes | elapsed={base.duration(time.monotonic() - started)}",
            flush=True,
        )
        raise
    finally:
        conn.close()
    print(
        f"ИТОГ: Ozon ассортимент | status=ok | downloaded={downloaded:,} | requests={api.request_count} | "
        f"inserted_products={stats.get('inserted_products', 0):,} | enriched_products={stats.get('enriched_products', 0):,} | "
        f"inserted_attributes={stats.get('inserted_attributes', 0):,} | "
        f"enriched_attributes={stats.get('enriched_attributes', 0):,} | deletes=0 | blank_overwrites=0 | "
        f"errors=0 | partial=no | elapsed={base.duration(time.monotonic() - started)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
