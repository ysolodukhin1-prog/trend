from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

import openpyxl
import psycopg2
from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))

import app  # noqa: E402
from import_seo_may_2026_tags import ensure_tag_table


WORKBOOK_PATH = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\SEO\GJ лето 2026 МП тотал.xlsx"
)
TAG = "GJ лето 2026"
SHEET_CONFIG = {
    "озон": {
        "marketplace": "ozon",
        "sku_header": "Ozon ID",
        "name_header": "Модель",
    },
    "вб": {
        "marketplace": "wb",
        "sku_header": "Артикул WB",
        "name_header": "Модель GJ",
    },
}


def clean_sku(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def header_map(sheet) -> dict[str, int]:
    return {
        str(cell.value).strip(): cell.column - 1
        for cell in sheet[1]
        if cell.value is not None and str(cell.value).strip()
    }


def extract_rows() -> list[tuple[str, str, str, str, str]]:
    workbook = openpyxl.load_workbook(WORKBOOK_PATH, read_only=True, data_only=True)
    rows_by_key: OrderedDict[tuple[str, str], tuple[str, str, str, str, str]] = OrderedDict()
    for sheet_name, config in SHEET_CONFIG.items():
        sheet = workbook[sheet_name]
        headers = header_map(sheet)
        sku_index = headers[config["sku_header"]]
        name_index = headers.get(config["name_header"])
        marketplace = config["marketplace"]
        for raw_row in sheet.iter_rows(min_row=2, values_only=True):
            sku = clean_sku(raw_row[sku_index] if sku_index < len(raw_row) else "")
            if not sku:
                continue
            product_name = ""
            if name_index is not None and name_index < len(raw_row):
                product_name = str(raw_row[name_index] or "").strip()
            rows_by_key[(marketplace, sku)] = (
                marketplace,
                sku,
                TAG,
                product_name,
                WORKBOOK_PATH.name,
            )
    return list(rows_by_key.values())


def upsert_reference_tag(rows: list[tuple[str, str, str, str, str]]) -> None:
    with psycopg2.connect(**app.read_db_config()) as conn, conn.cursor() as cur:
        ensure_tag_table(cur)
        execute_values(
            cur,
            """
            INSERT INTO public.marketplace_sku_tags
                (marketplace, sku, tag, product_name, source_file)
            VALUES %s
            ON CONFLICT (marketplace, sku, tag) DO UPDATE SET
                product_name = EXCLUDED.product_name,
                source_file = EXCLUDED.source_file,
                tagged_at = now()
            """,
            rows,
        )


def main() -> int:
    rows = extract_rows()
    upsert_reference_tag(rows)
    counts: dict[str, int] = {}
    for marketplace, *_ in rows:
        counts[marketplace] = counts.get(marketplace, 0) + 1
    for marketplace in sorted(counts):
        print(f"{marketplace.upper()}: {counts[marketplace]} unique SKU tagged as {TAG}")
    print(f"Source: {WORKBOOK_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
