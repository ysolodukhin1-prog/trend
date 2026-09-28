#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import normalized inventory snapshots for isolated client databases."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402
import inventory_history  # noqa: E402
from konstex_paths import KONSTEX_WB_STOCK_DIR  # noqa: E402


CLIENT_DATABASES = {
    "konstex": "konstex",
    "km_trade": "km_trade_products",
}


def connect(client: str):
    config = app.read_db_config()
    config["database"] = CLIENT_DATABASES[client]
    return psycopg2.connect(**config)


def payload_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, dict):
        rows = data.get("items") or data.get("rows") or data.get("products") or []
        return [item for item in rows if isinstance(item, dict)]
    return []


def import_konstex_json(conn: Any, source_dir: Path) -> tuple[int, int]:
    files = sorted(source_dir.glob("stock_api_*.json"))
    print(
        f"ПЛАН: Konstex WB | файлов {len(files)} | по одному дневному снимку на файл | "
        "API-паузы нет | upsert по дата x SKU",
        flush=True,
    )
    imported = 0
    errors = 0
    started = time.monotonic()
    for index, path in enumerate(files, start=1):
        match = re.search(r"stock_api_(\d{4}-\d{2}-\d{2})", path.name)
        if not match:
            errors += 1
            print(f"[{index}/{len(files)}] {path.name}: пропуск, дата не найдена", flush=True)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = inventory_history.store_wb_product_snapshot(
                conn,
                date.fromisoformat(match.group(1)),
                payload_items(payload),
                str(path),
            )
            conn.commit()
            imported += rows
            elapsed = time.monotonic() - started
            pct = index / max(len(files), 1) * 100
            eta = elapsed / index * (len(files) - index) if index else 0
            print(
                f"ПРОГРЕСС: {index}/{len(files)} ({pct:.1f}%) | {path.name} | "
                f"строк {imported} | errors={errors} | elapsed={elapsed:.1f}s | ETA={eta:.1f}s",
                flush=True,
            )
            print(f"[{index}/{len(files)}] {path.name}: imported {rows}, errors 0", flush=True)
        except Exception as exc:
            conn.rollback()
            errors += 1
            print(f"[{index}/{len(files)}] {path.name}: ошибка {exc}", flush=True)
    return imported, errors


def normalized_key(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", "", str(value or "").strip().lower())


def first(row: dict[str, Any], *names: str) -> Any:
    keyed = {normalized_key(key): value for key, value in row.items()}
    for name in names:
        key = normalized_key(name)
        if key in keyed and keyed[key] not in (None, ""):
            return keyed[key]
    return None


def number(value: Any) -> float | None:
    if value in (None, "", "-", "—"):
        return None
    try:
        return float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def csv_rows_from_zip(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        for member in members:
            text = decode_csv(archive.read(member))
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
                dialect.delimiter = ";"
            rows.extend(dict(row) for row in csv.DictReader(io.StringIO(text), dialect=dialect))
    return rows


def import_wb_daily_zip(conn: Any, path: Path) -> tuple[int, int]:
    source_rows = csv_rows_from_zip(path)
    print(
        f"ПЛАН: WB STOCK_HISTORY_DAILY_CSV | строк {len(source_rows)} | "
        "группировка дата x SKU x склад | API-паузы нет",
        flush=True,
    )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    date_columns: dict[str, date] = {}
    if source_rows:
        for column in source_rows[0]:
            if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(column)):
                date_columns[str(column)] = datetime.strptime(str(column), "%d.%m.%Y").date()
    if date_columns:
        skipped = 0
        total_cells = len(source_rows) * len(date_columns)
        print(
            f"ФОРМАТ: wide CSV | складских строк {len(source_rows)} | "
            f"дневных колонок {len(date_columns)} | ячеек {total_cells}",
            flush=True,
        )
        for row_index, row in enumerate(source_rows, start=1):
            sku = str(first(row, "NmID", "nmId", "Артикул WB") or "").strip()
            if not sku:
                skipped += len(date_columns)
                continue
            warehouse = str(first(row, "OfficeName", "warehouseName", "Склад", "Название склада") or "")
            for column, snapshot_date in date_columns.items():
                stock = number(row.get(column))
                if stock is None:
                    skipped += 1
                    continue
                key = (snapshot_date.isoformat(), sku, warehouse)
                target = grouped.setdefault(
                    key,
                    {
                        "marketplace": "wb",
                        "snapshot_date": snapshot_date,
                        "sku": sku,
                        "seller_article": first(row, "VendorCode", "Артикул продавца"),
                        "product_name": first(row, "Name", "Название"),
                        "category_name": first(row, "SubjectName", "Предмет"),
                        "warehouse_name": warehouse,
                        "cluster_name": "",
                        "stock_available_qty": 0,
                        "stock_total_qty": 0,
                        "to_customer_qty": 0,
                        "from_customer_qty": 0,
                        "source_type": "wb_stock_history_daily_csv",
                        "source_ref": str(path),
                    },
                )
                target["stock_available_qty"] += stock
                target["stock_total_qty"] += stock
            if row_index == len(source_rows) or row_index % 25 == 0:
                pct = row_index / max(len(source_rows), 1) * 100
                print(
                    f"ПРОГРЕСС: {row_index}/{len(source_rows)} ({pct:.1f}%) | "
                    f"wide rows | агрегатов {len(grouped)} | skipped={skipped}",
                    flush=True,
                )
        loaded = inventory_history.upsert_rows(conn, list(grouped.values()))
        conn.commit()
        return loaded, skipped
    skipped = 0
    for row in source_rows:
        snapshot = str(first(row, "date", "dt", "Дата", "Дата остатка") or "")[:10]
        sku = str(first(row, "nmID", "nmId", "Артикул WB") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot) or not sku:
            skipped += 1
            continue
        warehouse = str(first(row, "warehouseName", "Склад", "Название склада") or "")
        key = (snapshot, sku, warehouse)
        target = grouped.setdefault(
            key,
            {
                "marketplace": "wb",
                "snapshot_date": date.fromisoformat(snapshot),
                "sku": sku,
                "seller_article": first(row, "vendorCode", "Артикул продавца"),
                "product_name": first(row, "name", "Название"),
                "category_name": first(row, "subjectName", "Предмет"),
                "warehouse_name": warehouse,
                "cluster_name": "",
                "stock_available_qty": 0,
                "stock_total_qty": 0,
                "to_customer_qty": 0,
                "from_customer_qty": 0,
                "source_type": "wb_stock_history_daily_csv",
                "source_ref": str(path),
            },
        )
        stock = number(first(row, "stockCount", "quantity", "Остаток", "Остаток, шт")) or 0
        target["stock_available_qty"] += stock
        target["stock_total_qty"] += stock
        target["to_customer_qty"] += number(first(row, "toClientCount", "В пути к клиенту")) or 0
        target["from_customer_qty"] += number(first(row, "fromClientCount", "В пути от клиента")) or 0
    loaded = inventory_history.upsert_rows(conn, list(grouped.values()))
    conn.commit()
    return loaded, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", choices=tuple(CLIENT_DATABASES), required=True)
    parser.add_argument("--wb-zip", type=Path)
    parser.add_argument("--from-current-ozon", action="store_true")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    imported = 0
    errors = 0
    with connect(args.client) as conn:
        if args.wb_zip:
            imported, errors = import_wb_daily_zip(conn, args.wb_zip)
        elif args.from_current_ozon:
            print(
                f"ПЛАН: {args.client} Ozon | текущие нормализованные таблицы -> "
                f"снимок {args.snapshot_date} | один SQL batch",
                flush=True,
            )
            imported = inventory_history.store_ozon_current_tables_snapshot(
                conn,
                args.snapshot_date,
                "database://public.ozon_stock_product_warehouses",
            )
            conn.commit()
        elif args.client == "konstex":
            imported, errors = import_konstex_json(conn, KONSTEX_WB_STOCK_DIR)
        else:
            raise RuntimeError("Для KM Trade укажите --wb-zip или --from-current-ozon")
    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: client={args.client} | imported={imported} | errors/skips={errors} | "
        f"elapsed={elapsed:.1f}s | stopped=no | partial={'yes' if errors else 'no'}",
        flush=True,
    )
    return 1 if errors and not imported else 0


if __name__ == "__main__":
    raise SystemExit(main())

