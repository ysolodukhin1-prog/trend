#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Read-only source and PostgreSQL readiness audit for KM Trade."""

from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402


DATA_ROOT = Path(
    os.environ.get(
        "KM_DATA_ROOT",
        r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data",
    )
)
TARGET_DB = os.environ.get("KM_DB_NAME", "km_trade_products")
EXPECTED_SOURCES = {
    "ozon_fin": DATA_ROOT / "Ozon" / "Fin",
    "ozon_funnel": DATA_ROOT / "Ozon" / "Funel",
    "ozon_product_adv": DATA_ROOT / "Ozon" / "Prod_adv",
    "ozon_product_categories": DATA_ROOT / "Ozon" / "Product_catigories",
    "ozon_serp": DATA_ROOT / "Ozon" / "Serp",
    "ozon_stock": DATA_ROOT / "Ozon" / "Stock",
}


def progress(current: int, total: int, item: str, started: float) -> None:
    elapsed = time.monotonic() - started
    pct = current * 100 / total if total else 100.0
    remaining = elapsed / current * (total - current) if current else 0.0
    print(
        f"ПРОГРЕСС: {current}/{total} ({pct:.1f}%) | {item} | "
        f"elapsed={elapsed:.1f}s | ETA={remaining:.1f}s",
        flush=True,
    )


def source_audit(started: float) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    total = len(EXPECTED_SOURCES)
    for index, (key, path) in enumerate(EXPECTED_SOURCES.items(), start=1):
        files = sorted(path.rglob("*.xlsx")) if path.exists() else []
        total_bytes = sum(item.stat().st_size for item in files)
        newest = max((item.stat().st_mtime for item in files), default=None)
        result = {
            "key": key,
            "path": str(path),
            "exists": path.exists(),
            "files": len(files),
            "bytes": total_bytes,
            "newest_mtime": newest,
        }
        results.append(result)
        print(
            f"[{index}/{total}] {key}: exists={result['exists']}, "
            f"files={len(files)}, bytes={total_bytes}",
            flush=True,
        )
        progress(index, total, key, started)
    return results


def database_exists(config: dict[str, object]) -> bool:
    with psycopg2.connect(**{**config, "database": "postgres"}) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET_DB,))
            return cur.fetchone() is not None


def relation_counts(cur) -> list[dict[str, object]]:
    cur.execute(
        """
        SELECT
            c.relname AS relation,
            c.relkind,
            CASE
                WHEN c.relkind IN ('r', 'm') THEN pg_total_relation_size(c.oid)
                ELSE 0
            END AS bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'm', 'v')
        ORDER BY c.relkind, c.relname
        """
    )
    rows = cur.fetchall()
    for row in rows:
        if row["relkind"] not in {"r", "m"}:
            row["rows"] = None
            continue
        cur.execute(
            sql.SQL("SELECT count(*) AS row_count FROM public.{}")
            .format(sql.Identifier(row["relation"]))
        )
        row["rows"] = cur.fetchone()["row_count"]
    return rows


def main() -> int:
    started = time.monotonic()
    print(
        "ПЛАН: проверить 6 групп XLSX-источников, наличие отдельной БД, "
        "таблицы/представления и объёмы строк; операция только для чтения.",
        flush=True,
    )
    sources = source_audit(started)
    config = app.read_db_config()
    exists = database_exists(config)
    print(f"DATABASE: name={TARGET_DB}, exists={exists}", flush=True)
    relations: list[dict[str, object]] = []
    if exists:
        with psycopg2.connect(**{**config, "database": TARGET_DB}, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cur:
                relations = relation_counts(cur)
        kinds = Counter(row["relkind"] for row in relations)
        print(
            "OBJECTS: "
            f"tables={kinds.get('r', 0)}, materialized_views={kinds.get('m', 0)}, "
            f"views={kinds.get('v', 0)}",
            flush=True,
        )
        for row in relations:
            print(
                f"RELATION: {row['relation']} | kind={row['relkind']} | "
                f"rows={row['rows']} | bytes={row['bytes']}",
                flush=True,
            )
    missing = [row["key"] for row in sources if not row["exists"] or not row["files"]]
    elapsed = time.monotonic() - started
    print(
        "ИТОГ: "
        f"database_exists={exists}, source_groups={len(sources)}, "
        f"missing_or_empty={len(missing)}, relations={len(relations)}, "
        f"elapsed={elapsed:.1f}s, stopped=false, partial=false",
        flush=True,
    )
    if missing:
        print("MISSING_OR_EMPTY: " + ", ".join(missing), flush=True)
    return 0 if exists and not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
