#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pivot and import the official wide WB daily stock-history CSV for KM Trade."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import sync_km_wb_extended_api as sync  # noqa: E402


def read_csv_rows(zip_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(zip_path) as bundle:
        csv_names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        print(f"ПЛАН: WB stock history pivot | files={len(csv_names)} | source={zip_path}", flush=True)
        for index, name in enumerate(csv_names, start=1):
            raw = bundle.read(name)
            text = raw.decode("utf-8-sig", "replace")
            delimiter = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
            file_rows = [dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]
            rows.extend(file_rows)
            print(f"[{index}/{len(csv_names)}] {name}: source_rows={len(file_rows)} | errors=0", flush=True)
    return rows


def text_value(row: dict[str, str], *keys: str) -> str | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value).strip()
    return None


def pivot_values(rows: list[dict[str, str]], report_id: uuid.UUID) -> list[tuple]:
    values: list[tuple] = []
    started = time.monotonic()
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        raw_nm = text_value(row, "nmID", "Артикул WB")
        try:
            nm_id = int(raw_nm) if raw_nm else None
        except ValueError:
            nm_id = None
        vendor_code = text_value(row, "VendorCode", "supplierArticle", "Артикул продавца")
        warehouse = text_value(row, "OfficeName", "warehouseName", "Склад")
        chrt_id = text_value(row, "ChrtID", "chrtId")
        key_seed = f"{nm_id}|{chrt_id}|{vendor_code}|{warehouse}"
        entity_key = hashlib.sha256(key_seed.encode("utf-8")).hexdigest()[:32]
        for column_name, raw_qty in row.items():
            try:
                snapshot_date = datetime.strptime(str(column_name).strip(), "%d.%m.%Y").date()
            except ValueError:
                continue
            try:
                quantity = float(str(raw_qty or "0").replace(" ", "").replace(",", "."))
            except ValueError:
                quantity = None
            compact = {
                "nmID": raw_nm,
                "chrtID": chrt_id,
                "vendorCode": vendor_code,
                "officeName": warehouse,
                "name": text_value(row, "Name", "Наименование"),
                "quantity": raw_qty,
                "sourceDateColumn": column_name,
            }
            values.append((snapshot_date, entity_key, nm_id, vendor_code, warehouse, quantity, str(report_id), Json(compact)))
        if index == total or index % 250 == 0:
            pct = index / max(total, 1) * 100
            elapsed = time.monotonic() - started
            print(
                f"ПРОГРЕСС: {index}/{total} ({pct:.1f}%) | daily_rows={len(values)} | "
                f"errors=0 | elapsed={sync.duration(elapsed)}",
                flush=True,
            )
    return values


def main() -> None:
    sync.configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path")
    parser.add_argument("--report-id", required=True)
    args = parser.parse_args()
    zip_path = Path(args.zip_path).resolve()
    report_id = uuid.UUID(args.report_id)
    started = time.monotonic()
    rows = read_csv_rows(zip_path)
    values = pivot_values(rows, report_id)
    with sync.db_connection() as conn, conn.cursor() as cur:
        sync.ensure_schema(cur)
        execute_values(
            cur,
            """
            INSERT INTO public.wb_inventory_history_daily_csv (
                snapshot_date, entity_key, nm_id, vendor_code, warehouse_name,
                quantity, source_report_id, raw_row
            ) VALUES %s
            ON CONFLICT (snapshot_date, entity_key) DO UPDATE SET
                nm_id = EXCLUDED.nm_id,
                vendor_code = EXCLUDED.vendor_code,
                warehouse_name = EXCLUDED.warehouse_name,
                quantity = EXCLUDED.quantity,
                source_report_id = EXCLUDED.source_report_id,
                raw_row = EXCLUDED.raw_row,
                imported_at = now()
            """,
            values,
            page_size=1000,
        )
        published = sync.inventory_history.publish_wb_stock_history_csv(
            conn,
            min(value[0] for value in values),
            max(value[0] for value in values),
        ) if values else 0
        conn.commit()
    print(
        f"ИТОГ: source_rows={len(rows)} | daily_rows={len(values)} | "
        f"dashboard_rows={published} | errors=0 | "
        f"output_db={sync.TARGET_DB} | elapsed={sync.duration(time.monotonic() - started)}",
        flush=True,
    )


if __name__ == "__main__":
    main()

