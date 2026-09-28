#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monthly KM Trade Ozon product/category/attribute import.

The Ozon category workbooks are seller templates with useful data in the
"Шаблон" sheet. Some files contain malformed styles, so this importer reads
the XLSX XML directly instead of using openpyxl.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402

SOURCE_DIR = Path(
    os.environ.get(
        "KM_OZON_PRODUCT_CATEGORIES_DIR",
        r"G:\Общие диски\Kokoc Marketplaces\Clients\KM Trade\Аналитика\Дашборды\Data\Ozon\Product_catigories",
    )
)
SOURCE_DIRS = [
    Path(value)
    for value in os.environ.get("OZON_PRODUCT_CATEGORIES_DIRS", "").split(";")
    if value.strip()
] or [SOURCE_DIR]
TARGET_DB = os.environ.get("OZON_PRODUCT_CATEGORIES_DB_NAME") or os.environ.get("KM_DB_NAME", "km_trade_products")

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

COMMON_COLUMN_MAP = {
    "#Хештеги": "heshtegi",
    "Rich-контент JSON": "rich_kontent_json",
    "SKU": "sku",
    "Аннотация": "annotatsiya",
    "Артикул*": "artikul",
    "Артикул": "artikul",
    "Вес в упаковке, г*": "ves_v_upakovke_g",
    "Высота упаковки, мм*": "vysota_upakovki_mm",
    "Длина упаковки, мм*": "dlina_upakovki_mm",
    "Количество заводских упаковок": "kolichestvo_zavodskih_upakovok",
    "Количество товара в УЕИ": "kolichestvo_tovara_v_uei",
    "Минимальное количество оптом": "minimalnoe_kolichestvo_optom",
    "НДС, %*": "nds_pct",
    "Название товара": "nazvanie_tovara",
    "Объединить в похожие товары": "obedinit_v_pohozhie_tovary",
    "Рассрочка": "rassrochka",
    "Ссылка на главное фото*": "ssylka_na_glavnoe_foto",
    "Ссылки на дополнительные фото": "ssylki_na_dopolnitelnye_foto",
    "Страна-изготовитель": "strana_izgotovitel",
    "Тип*": "tip",
    "Тип": "tip",
    "Ускоренный сбор отзывов": "uskorennyy_sbor_otzyvov",
    "Цена до скидки, руб.": "tsena_do_skidki_rub",
    "Цена, руб.*": "tsena_rub",
    "Ширина упаковки, мм*": "shirina_upakovki_mm",
    "Штрихкод (Серийный номер / EAN)": "shtrihkod_seriynyy_nomer_ean",
    "Barcode": "shtrihkod_seriynyy_nomer_ean",
}

PRODUCT_EXPORT_MAP = {
    "Ozon Product ID": "product_id",
    "SKU": "sku",
    "Barcode": "shtrihkod_seriynyy_nomer_ean",
    "Категория": "category_name",
    "Тип": "tip",
}

PRODUCT_COLUMNS = [
    "product_id",
    "heshtegi",
    "rich_kontent_json",
    "sku",
    "annotatsiya",
    "artikul",
    "ves_v_upakovke_g",
    "vysota_upakovki_mm",
    "dlina_upakovki_mm",
    "kolichestvo_zavodskih_upakovok",
    "kolichestvo_tovara_v_uei",
    "minimalnoe_kolichestvo_optom",
    "nds_pct",
    "nazvanie_tovara",
    "obedinit_v_pohozhie_tovary",
    "rassrochka",
    "ssylka_na_glavnoe_foto",
    "ssylki_na_dopolnitelnye_foto",
    "strana_izgotovitel",
    "tip",
    "uskorennyy_sbor_otzyvov",
    "tsena_do_skidki_rub",
    "tsena_rub",
    "shirina_upakovki_mm",
    "shtrihkod_seriynyy_nomer_ean",
    "category_id",
    "category_name",
    "import_file",
    "source_row_num",
]


@dataclass
class WorkbookRows:
    path: Path
    sheet_name: str
    headers: dict[int, str]
    rows: list[tuple[int, dict[int, str]]]


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").strip()
    if text.startswith("'"):
        text = text[1:].strip()
    return re.sub(r"\s+", " ", text)


def strip_required(header: str) -> str:
    return clean_text(header).rstrip("*").strip()


def category_from_filename(path: Path) -> str:
    name = path.stem
    name = re.sub(r"_\d{2}\.\d{2}\.\d{4}$", "", name)
    return clean_text(name)


def stable_int(value: str, bits: int = 31) -> int:
    digest = hashlib.blake2b(clean_text(value).encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    return (number & ((1 << bits) - 1)) or 1


def stable_product_id(*parts: str) -> int:
    key = "|".join(clean_text(part) for part in parts if clean_text(part)) or "km-product"
    return stable_int(key, bits=63)


def col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch.upper()) - 64
    return number


def read_shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    strings: list[str] = []
    with zf.open("xl/sharedStrings.xml") as fh:
        for _, si in ET.iterparse(fh, events=("end",)):
            if si.tag.endswith("}si"):
                strings.append("".join(node.text or "" for node in si.iter() if node.tag.endswith("}t")))
                si.clear()
    return strings


def sheet_targets(zf: ZipFile) -> dict[str, str]:
    workbook = ET.parse(zf.open("xl/workbook.xml")).getroot()
    rels = ET.parse(zf.open("xl/_rels/workbook.xml.rels")).getroot()
    rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    targets: dict[str, str] = {}
    sheets = workbook.find(f"{{{NS_MAIN}}}sheets")
    if sheets is None:
        return targets
    for sheet in sheets:
        rid = sheet.attrib[f"{{{NS_REL}}}id"]
        target = rid_to_target[rid].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        targets[sheet.attrib["name"]] = target
    return targets


def cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return clean_text("".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t")))
    value = cell.find(f"{{{NS_MAIN}}}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return clean_text(shared_strings[index] if index < len(shared_strings) else "")
    return clean_text(value.text)


def read_sheet_rows(zf: ZipFile, sheet_target: str, shared_strings: list[str]) -> list[tuple[int, dict[int, str]]]:
    result: list[tuple[int, dict[int, str]]] = []
    with zf.open(sheet_target) as fh:
        for _, row in ET.iterparse(fh, events=("end",)):
            if not row.tag.endswith("}row"):
                continue
            values: dict[int, str] = {}
            for cell in row:
                if not cell.tag.endswith("}c"):
                    continue
                text = cell_value(cell, shared_strings)
                if text:
                    values[col_index(cell.attrib.get("r", "A"))] = text
            if values:
                result.append((int(row.attrib.get("r", "0")), values))
            row.clear()
    return result


def read_workbook(path: Path) -> WorkbookRows:
    with ZipFile(path) as zf:
        strings = read_shared_strings(zf)
        targets = sheet_targets(zf)
        sheet_name = "Шаблон" if "Шаблон" in targets else next(iter(targets))
        rows = read_sheet_rows(zf, targets[sheet_name], strings)
    headers = next((values for row_num, values in rows if row_num == 2), {})
    data_rows = [
        (row_num, values)
        for row_num, values in rows
        if row_num >= 5 and any(values.get(col) for col in (1, 2, 3, 4, 9, 10))
    ]
    return WorkbookRows(path=path, sheet_name=sheet_name, headers=headers, rows=data_rows)


def read_source_workbooks() -> list[WorkbookRows]:
    workbooks: list[WorkbookRows] = []
    for source_dir in SOURCE_DIRS:
        if not source_dir.exists():
            raise FileNotFoundError(f"Source folder not found: {source_dir}")
        workbooks.extend(read_workbook(path) for path in sorted(source_dir.glob("*.xlsx")))
    return workbooks


def create_database_if_needed():
    base_config = app.read_db_config()
    admin_config = {**base_config, "database": os.environ.get("KM_ADMIN_DB", "postgres")}
    conn = psycopg2.connect(**admin_config)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET_DB,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET_DB)))
    finally:
        conn.close()


def km_db_config():
    return {**app.read_db_config(), "database": TARGET_DB}


def get_conn():
    return psycopg2.connect(**km_db_config(), cursor_factory=RealDictCursor)


def create_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ozon_cat_import_files (
            file_name text PRIMARY KEY,
            file_size bigint,
            file_mtime timestamp,
            row_count integer,
            imported_at timestamp DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ozon_cat_products (
            product_id bigint PRIMARY KEY,
            heshtegi text,
            rich_kontent_json text,
            sku text,
            annotatsiya text,
            artikul text,
            ves_v_upakovke_g text,
            vysota_upakovki_mm text,
            dlina_upakovki_mm text,
            kolichestvo_zavodskih_upakovok text,
            kolichestvo_tovara_v_uei text,
            minimalnoe_kolichestvo_optom text,
            nds_pct text,
            nazvanie_tovara text,
            obedinit_v_pohozhie_tovary text,
            rassrochka text,
            ssylka_na_glavnoe_foto text,
            ssylki_na_dopolnitelnye_foto text,
            strana_izgotovitel text,
            tip text,
            uskorennyy_sbor_otzyvov text,
            tsena_do_skidki_rub text,
            tsena_rub text,
            shirina_upakovki_mm text,
            shtrihkod_seriynyy_nomer_ean text,
            category_id integer,
            category_name text,
            import_file varchar(500),
            source_row_num integer,
            imported_at timestamp DEFAULT now(),
            updated_at timestamp DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ozon_cat_common_attributes (
            attribute_id integer PRIMARY KEY,
            attribute_name text NOT NULL,
            db_column text,
            created_at timestamp DEFAULT now()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ozon_cat_category_attributes (
            id bigserial PRIMARY KEY,
            category_id integer NOT NULL,
            attribute_id integer NOT NULL,
            attribute_name text NOT NULL,
            created_at timestamp DEFAULT now(),
            UNIQUE(category_id, attribute_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.ozon_cat_product_attributes (
            id bigserial PRIMARY KEY,
            product_id bigint NOT NULL,
            attribute_id integer NOT NULL,
            value_text text,
            created_at timestamp DEFAULT now()
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_sku ON public.ozon_cat_products(sku)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_artikul ON public.ozon_cat_products(artikul)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_barcode ON public.ozon_cat_products(shtrihkod_seriynyy_nomer_ean)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_product_attrs_product ON public.ozon_cat_product_attributes(product_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_product_attrs_attr ON public.ozon_cat_product_attributes(attribute_id)")


def truncate_schema(cur):
    cur.execute(
        """
        TRUNCATE public.ozon_cat_product_attributes,
                 public.ozon_cat_category_attributes,
                 public.ozon_cat_common_attributes,
                 public.ozon_cat_products,
                 public.ozon_cat_import_files
        RESTART IDENTITY
        """
    )


def row_by_header(workbook: WorkbookRows, values: dict[int, str]) -> dict[str, str]:
    return {
        workbook.headers[col]: value
        for col, value in values.items()
        if workbook.headers.get(col) and value
    }


def clean_article(value: str) -> str:
    return clean_text(value).upper()


def build_product_export_index(workbooks: list[WorkbookRows]):
    by_article: dict[str, dict[str, str]] = {}
    by_barcode: dict[str, dict[str, str]] = {}
    rows = []
    for workbook in workbooks:
        if not workbook.path.name.lower().startswith("товары"):
            continue
        for source_row, values in workbook.rows:
            record = row_by_header(workbook, values)
            record["_source_file"] = workbook.path.name
            record["_source_row_num"] = str(source_row)
            rows.append(record)
            article = clean_article(record.get("Артикул", ""))
            barcode = clean_text(record.get("Barcode", ""))
            if article:
                by_article[article] = record
            if barcode:
                by_barcode[barcode] = record
    return rows, by_article, by_barcode


def product_id_from_record(record: dict[str, str], fallback: tuple[str, ...]) -> int:
    raw = clean_text(record.get("Ozon Product ID", "") or record.get("product_id", ""))
    digits = re.sub(r"\D", "", raw)
    if digits:
        return int(digits)
    return stable_product_id(*fallback)


def build_rows(workbooks: list[WorkbookRows]):
    export_rows, export_by_article, export_by_barcode = build_product_export_index(workbooks)
    products: dict[int, dict[str, str | int]] = {}
    product_attrs: list[tuple[int, int, str]] = []
    category_attrs: dict[tuple[int, int], tuple[int, int, str]] = {}
    common_attrs: dict[int, tuple[int, str, str]] = {}
    import_files: list[tuple[str, int, datetime, int]] = []

    for header, column_name in COMMON_COLUMN_MAP.items():
        common_attrs[stable_int(header)] = (stable_int(header), strip_required(header), column_name)

    for workbook in workbooks:
        import_files.append(
            (
                workbook.path.name,
                workbook.path.stat().st_size,
                datetime.fromtimestamp(workbook.path.stat().st_mtime),
                len(workbook.rows),
            )
        )
        if workbook.path.name.lower().startswith("товары"):
            continue
        file_category = category_from_filename(workbook.path)
        category_id = stable_int(file_category)
        for source_row, values in workbook.rows:
            record = row_by_header(workbook, values)
            article = clean_text(record.get("Артикул*", ""))
            barcode = clean_text(record.get("Штрихкод (Серийный номер / EAN)", ""))
            export = export_by_article.get(clean_article(article)) or export_by_barcode.get(barcode) or {}
            product_id = product_id_from_record(export, (article, barcode, workbook.path.name, str(source_row)))
            row = {column: "" for column in PRODUCT_COLUMNS}
            row.update(
                {
                    "product_id": product_id,
                    "category_id": category_id,
                    "category_name": file_category,
                    "import_file": workbook.path.name,
                    "source_row_num": source_row,
                }
            )
            for header, value in record.items():
                column_name = COMMON_COLUMN_MAP.get(header)
                if column_name and column_name in row:
                    row[column_name] = value
            if not row["sku"] and export.get("SKU"):
                row["sku"] = clean_text(export["SKU"])
            if not row["tip"] and export.get("Тип"):
                row["tip"] = clean_text(export["Тип"])
            products[product_id] = row

            for header, value in record.items():
                if not value or header in COMMON_COLUMN_MAP or header == "№":
                    continue
                attr_id = stable_int(f"{file_category}:{header}")
                attr_name = strip_required(header)
                category_attrs[(category_id, attr_id)] = (category_id, attr_id, attr_name)
                product_attrs.append((product_id, attr_id, value))

    for record in export_rows:
        product_id = product_id_from_record(record, (record.get("Артикул", ""), record.get("Barcode", ""), record.get("SKU", "")))
        if product_id in products:
            row = products[product_id]
        else:
            category_name = clean_text(record.get("Категория", "") or record.get("Тип", "") or "Без категории")
            row = {column: "" for column in PRODUCT_COLUMNS}
            row.update(
                {
                    "product_id": product_id,
                    "category_id": stable_int(category_name),
                    "category_name": category_name,
                    "import_file": record.get("_source_file", ""),
                    "source_row_num": int(record.get("_source_row_num", "0") or 0),
                }
            )
            products[product_id] = row
        for header, column_name in {**COMMON_COLUMN_MAP, **PRODUCT_EXPORT_MAP}.items():
            if header in record and column_name in row and column_name != "product_id":
                row[column_name] = clean_text(record[header])
        category_name = clean_text(row.get("category_name", "")) or "Без категории"
        category_id = int(row.get("category_id") or stable_int(category_name))
        for header, value in record.items():
            if header.startswith("_") or not value or header in COMMON_COLUMN_MAP or header in PRODUCT_EXPORT_MAP:
                continue
            attr_id = stable_int(f"{category_name}:{header}")
            attr_name = strip_required(header)
            category_attrs[(category_id, attr_id)] = (category_id, attr_id, attr_name)
            product_attrs.append((product_id, attr_id, value))

    return list(products.values()), product_attrs, list(category_attrs.values()), list(common_attrs.values()), import_files


def dedupe_by_key(rows, key_func):
    deduped = {}
    for row in rows:
        deduped[key_func(row)] = row
    return list(deduped.values())


def insert_rows(cur, products, product_attrs, category_attrs, common_attrs, import_files):
    product_values = dedupe_by_key(
        [tuple(row.get(column, "") for column in PRODUCT_COLUMNS) for row in products],
        lambda row: row[0],
    )
    common_attrs = dedupe_by_key(common_attrs, lambda row: row[0])
    category_attrs = dedupe_by_key(category_attrs, lambda row: (row[0], row[1]))
    import_files = dedupe_by_key(import_files, lambda row: row[0])
    execute_values(
        cur,
        sql.SQL(
            """
            INSERT INTO public.ozon_cat_products ({columns})
            VALUES %s
            ON CONFLICT (product_id) DO UPDATE SET
                {updates},
                updated_at = now()
            """
        ).format(
            columns=sql.SQL(", ").join(map(sql.Identifier, PRODUCT_COLUMNS)),
            updates=sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                for column in PRODUCT_COLUMNS
                if column != "product_id"
            ),
        ).as_string(cur),
        product_values,
        page_size=1000,
    )
    execute_values(
        cur,
        """
        INSERT INTO public.ozon_cat_common_attributes (attribute_id, attribute_name, db_column)
        VALUES %s
        ON CONFLICT (attribute_id) DO UPDATE SET
            attribute_name = EXCLUDED.attribute_name,
            db_column = EXCLUDED.db_column
        """,
        common_attrs,
        page_size=1000,
    )
    execute_values(
        cur,
        """
        INSERT INTO public.ozon_cat_category_attributes (category_id, attribute_id, attribute_name)
        VALUES %s
        ON CONFLICT (category_id, attribute_id) DO UPDATE SET
            attribute_name = EXCLUDED.attribute_name
        """,
        category_attrs,
        page_size=1000,
    )
    execute_values(
        cur,
        """
        INSERT INTO public.ozon_cat_product_attributes (product_id, attribute_id, value_text)
        VALUES %s
        """,
        product_attrs,
        page_size=5000,
    )
    execute_values(
        cur,
        """
        INSERT INTO public.ozon_cat_import_files (file_name, file_size, file_mtime, row_count)
        VALUES %s
        ON CONFLICT (file_name) DO UPDATE SET
            file_size = EXCLUDED.file_size,
            file_mtime = EXCLUDED.file_mtime,
            row_count = EXCLUDED.row_count,
            imported_at = now()
        """,
        import_files,
        page_size=1000,
    )
    cur.execute("ANALYZE public.ozon_cat_products")
    cur.execute("ANALYZE public.ozon_cat_product_attributes")
    cur.execute("ANALYZE public.ozon_cat_category_attributes")


def main():
    started = datetime.now()
    print(f"Ozon product categories import started: {started:%Y-%m-%d %H:%M:%S}")
    print("sources=" + "; ".join(str(path) for path in SOURCE_DIRS))
    print(f"database={TARGET_DB}")
    workbooks = read_source_workbooks()
    print(f"files={len(workbooks)}")
    create_database_if_needed()
    products, product_attrs, category_attrs, common_attrs, import_files = build_rows(workbooks)
    with get_conn() as conn, conn.cursor() as cur:
        create_schema(cur)
        truncate_schema(cur)
        insert_rows(cur, products, product_attrs, category_attrs, common_attrs, import_files)
        conn.commit()
    seconds = (datetime.now() - started).total_seconds()
    print(
        "Done ozon_product_categories: "
        f"products={len(products)}, product_attributes={len(product_attrs)}, "
        f"category_attributes={len(category_attrs)}, common_attributes={len(common_attrs)}, "
        f"files={len(import_files)}, seconds={seconds:.1f}, errors=0"
    )


if __name__ == "__main__":
    main()
