#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Import Ozon funnel XLSX reports into PostgreSQL.

The source files have broken Ozon Excel styles and two-level headers:
rows 10-11 are headers, row 13 is totals, row 14+ is product data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import logging
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402
from build_dashboard_aggregates import rebuild_rollup  # noqa: E402
from build_ozon_abc_materialized_views import (  # noqa: E402
    create_or_refresh_ozon_abc_materialized_views,
    drop_ozon_abc_materialized_views,
)


DEFAULT_SOURCE_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Дашборды\Data\Ozon\Funel"
)
LOG_PATH = Path(
    os.environ.get("OZON_FUNNEL_LOG_PATH")
    or (PROJECT_ROOT / "ozon_funnel_import_runtime.log")
)

HEADER_ROW_TOP = 9
HEADER_ROW_SUB = 10
DATA_START_ROW = 13
MAX_SOURCE_COLS = 80

logger = logging.getLogger("ozon_funnel_import")
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OZON_ADV_VIEW = "public.mv_ozon_adv_daily_by_article_category"


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    db_name: str
    kind: str


COLUMNS = [
    ColumnSpec("Товары", "product_name", "text"),
    ColumnSpec("Категория 1 уровня", "category_level_1", "text"),
    ColumnSpec("Категория 2 уровня", "category_level_2", "text"),
    ColumnSpec("Категория 3 уровня", "category_level_3", "text"),
    ColumnSpec("Бренд", "brand", "text"),
    ColumnSpec("Модель", "model", "text"),
    ColumnSpec("Схема работы", "work_schema", "text"),
    ColumnSpec("SKU", "sku", "text"),
    ColumnSpec("Артикул", "barcode", "text"),
    ColumnSpec("Продажи ABC-анализ по сумме заказов", "abc_orders_amount", "text"),
    ColumnSpec("Продажи ABC-анализ по количеству заказов", "abc_orders_qty", "text"),
    ColumnSpec("Продажи Заказано на сумму", "ordered_amount_rub", "numeric"),
    ColumnSpec("Продажи Динамика", "ordered_amount_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Позиция в поиске и каталоге", "search_catalog_position", "numeric"),
    ColumnSpec("Воронка продаж Динамика позиции", "search_catalog_position_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Показы всего", "impressions_total", "int"),
    ColumnSpec("Воронка продаж Динамика показов всего", "impressions_total_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Показы в поиске и каталоге", "impressions_search_catalog", "int"),
    ColumnSpec("Воронка продаж Динамика показов в поиске и каталоге", "impressions_search_catalog_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Посещения карточки товара", "card_visits", "int"),
    ColumnSpec("Воронка продаж Динамика посещений карточки", "card_visits_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Добавления в корзину всего", "cart_adds", "int"),
    ColumnSpec("Воронка продаж Динамика добавлений в корзину", "cart_adds_dynamic", "numeric"),
    ColumnSpec("Воронка продаж Заказано товаров", "ordered_units", "int"),
    ColumnSpec("Воронка продаж Динамика заказанных товаров", "ordered_units_dynamic", "numeric"),
]

DB_COLUMNS = [col.db_name for col in COLUMNS]
EXPECTED_SOURCE_HEADERS = [col.source_name for col in COLUMNS]
OPTIONAL_SOURCE_HEADERS = {
    "Продажи Динамика",
    "Воронка продаж Позиция в поиске и каталоге",
    "Воронка продаж Динамика позиции",
    "Воронка продаж Динамика показов всего",
    "Воронка продаж Динамика показов в поиске и каталоге",
    "Воронка продаж Динамика посещений карточки",
    "Воронка продаж Динамика добавлений в корзину",
    "Воронка продаж Динамика заказанных товаров",
}


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


def progress_line(current: int, total: int, item: str, rows_done: int, errors: int, started_at: float) -> str:
    pct = (current / total * 100) if total else 100.0
    elapsed = time.perf_counter() - started_at
    eta = 0.0
    if current > 0 and total > current:
        eta = elapsed / current * (total - current)
    return (
        f"ПРОГРЕСС: {current}/{total} ({pct:.1f}%) | {item} | "
        f"строки {rows_done:,} / ? | ошибки {errors} | "
        f"прошло {format_duration(elapsed)} | ETA {format_duration(eta)}"
    )


def print_progress(current: int, total: int, item: str, rows_done: int, errors: int, started_at: float) -> None:
    print("\r" + progress_line(current, total, item, rows_done, errors, started_at), end="", flush=True)


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            strings: list[str] = []
            for event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag == XML_NS + "si":
                    strings.append("".join(t.text or "" for t in elem.iter(XML_NS + "t")))
                    elem.clear()
            return strings
    except KeyError:
        return []


def first_sheet_name(zf: zipfile.ZipFile) -> str:
    sheets = sorted(
        name
        for name in zf.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if not sheets:
        raise ValueError("No worksheet XML found")
    return sheets[0]


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def cell_text(cell: ET.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text = "".join(t.text or "" for t in cell.iter(XML_NS + "t"))
        return text if text != "" else None

    value = cell.find(XML_NS + "v")
    if value is None or value.text is None:
        return None

    raw = value.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    return raw


def read_xlsx_rows_xml(
    file_path: Path,
    *,
    min_row: int = 1,
    max_row: int | None = None,
    max_col: int = 25,
) -> dict[int, list[str | None]]:
    rows: dict[int, list[str | None]] = {}
    with zipfile.ZipFile(file_path, "r") as zf:
        shared_strings = read_shared_strings(zf)
        sheet_name = first_sheet_name(zf)
        with zf.open(sheet_name) as fh:
            for event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != XML_NS + "row":
                    continue
                row_num = int(elem.attrib.get("r", "0"))
                if row_num < min_row:
                    elem.clear()
                    continue
                if max_row is not None and row_num > max_row:
                    elem.clear()
                    break

                values: list[str | None] = [None] * max_col
                for cell in elem.findall(XML_NS + "c"):
                    ref = cell.attrib.get("r", "")
                    col_idx = column_index(ref)
                    if 0 <= col_idx < max_col:
                        values[col_idx] = cell_text(cell, shared_strings)
                rows[row_num] = values
                elem.clear()
    return rows


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "–", "-"}:
        return None
    return text


def regclass_exists(cur: Any, object_name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists_flag", (object_name,))
    row = cur.fetchone()
    return bool(row[0]) if row else False


def rebuild_dependent_ozon_adv_view(cur: Any) -> None:
    adv_scripts_dir = PROJECT_ROOT / "Скрипты"
    if str(adv_scripts_dir) not in sys.path:
        sys.path.insert(0, str(adv_scripts_dir))
    import import_ozon_adv_daily_reports as ozon_adv_importer  # noqa: E402

    ozon_adv_importer.create_indexes(cur)
    ozon_adv_importer.create_materialized_view(cur)


def to_numeric(value: Any) -> float | None:
    text = clean_text(value)
    if text is None:
        return None
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_numeric(value)
    if number is None:
        return None
    return int(round(number))


def parse_period(value: Any) -> tuple[date, date]:
    text = clean_text(value) or ""
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*[–-]\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not match:
        raise ValueError(f"Cannot parse report period from {text!r}")
    period_from = datetime.strptime(match.group(1), "%d.%m.%Y").date()
    period_to = datetime.strptime(match.group(2), "%d.%m.%Y").date()
    return period_from, period_to


def extract_seller_article(product_name: str | None) -> str | None:
    if not product_name:
        return None
    match = re.match(r"^\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_-]{2,})\b", product_name)
    return match.group(1).upper() if match else None


def row_key(report_date: date, file_path: Path, source_row_num: int) -> str:
    parts = [
        report_date.isoformat(),
        str(file_path),
        str(source_row_num),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def build_headers(file_path: Path) -> list[str]:
    rows = read_xlsx_rows_xml(file_path, min_row=10, max_row=11, max_col=MAX_SOURCE_COLS)
    return build_headers_from_rows(rows)


def build_headers_from_rows(rows: dict[int, list[str | None]]) -> list[str]:
    top = [value or "" for value in rows.get(10, [])]
    sub = [value or "" for value in rows.get(11, [])]
    headers: list[str] = []
    group = ""
    last_metric = ""

    for top_value, sub_value in zip(top, sub):
        top_value = top_value.strip().replace("\n", " ")
        sub_value = sub_value.strip().replace("\n", " ")
        if top_value:
            group = top_value
        if not group:
            headers.append(sub_value)
            continue
        if sub_value and sub_value != group:
            if sub_value == "Динамика" and last_metric:
                header = f"{group} Динамика {last_metric}"
            else:
                header = f"{group} {sub_value}"
                last_metric = sub_value
            headers.append(header)
        else:
            headers.append(group)
            last_metric = group

    shifted_funnel_start = next(
        (index for index, header in enumerate(headers) if header == "Продажи Позиция в поиске и каталоге"),
        None,
    )
    if shifted_funnel_start is not None:
        shifted_names = [
            "Продажи Динамика",
            "Воронка продаж Позиция в поиске и каталоге",
            "Воронка продаж Динамика позиции",
            "Воронка продаж Показы всего",
            "Воронка продаж Динамика показов всего",
            "Воронка продаж Показы в поиске и каталоге",
            "Воронка продаж Динамика показов в поиске и каталоге",
            "Воронка продаж Посещения карточки товара",
            "Воронка продаж Динамика посещений карточки",
            "Воронка продаж Добавления в корзину всего",
            "Воронка продаж Динамика добавлений в корзину",
            "Воронка продаж Заказано товаров",
            "Воронка продаж Динамика заказанных товаров",
        ]
        for offset, header in enumerate(shifted_names):
            index = shifted_funnel_start + offset
            if index < len(headers):
                headers[index] = header

    replacements = {
        "Продажи Заказано на сумму (по цене реализации)": "Продажи Заказано на сумму",
        "Продажи Динамика Заказано на сумму": "Продажи Динамика",
        "Воронка продаж Динамика Позиция в поиске и каталоге": "Воронка продаж Динамика позиции",
        "Воронка продаж Динамика Показы всего": "Воронка продаж Динамика показов всего",
        "Воронка продаж Динамика Показы в поиске и каталоге": "Воронка продаж Динамика показов в поиске и каталоге",
        "Воронка продаж Динамика Посещения карточки товара": "Воронка продаж Динамика посещений карточки",
        "Воронка продаж Добавления из карточки в корзину": "Воронка продаж Добавления в корзину всего",
        "Воронка продаж Динамика Добавления из карточки в корзину": "Воронка продаж Динамика добавлений в корзину",
        "Воронка продаж Динамика Добавления в корзину всего": "Воронка продаж Динамика добавлений в корзину",
        "Воронка продаж Динамика Заказано товаров": "Воронка продаж Динамика заказанных товаров",
    }
    normalized_headers = []
    for header in headers:
        header = replacements.get(header, header)
        header = re.sub(
            r"\s+\d{2}\.\d{2}\.\d{4}\s*[\-–—]\s*\d{2}\.\d{2}\.\d{4}$",
            "",
            header,
        )
        normalized_headers.append(header)
    return normalized_headers


def read_rows(file_path: Path) -> tuple[date, date, list[dict[str, Any]]]:
    source_rows = read_xlsx_rows_xml(file_path, min_row=1, max_col=MAX_SOURCE_COLS)
    period_from, period_to = parse_period(source_rows.get(1, [None])[0])
    report_date = period_from
    headers = build_headers_from_rows(source_rows)
    header_index = {}
    for index, header in enumerate(headers):
        if header and header not in header_index:
            header_index[header] = index

    missing_headers = [
        spec.source_name
        for spec in COLUMNS
        if spec.source_name not in header_index and spec.source_name not in OPTIONAL_SOURCE_HEADERS
    ]
    if missing_headers:
        raise ValueError(
            "Missing expected headers: "
            + repr(missing_headers)
            + "; actual headers: "
            + repr([header for header in headers if header])
        )

    rows: list[dict[str, Any]] = []
    for source_index, values in source_rows.items():
        if source_index <= DATA_START_ROW:
            continue
        if not any(clean_text(value) is not None for value in values):
            continue
        record = {}
        for spec in COLUMNS:
            source_col = header_index.get(spec.source_name)
            record[spec.db_name] = values[source_col] if source_col is not None and source_col < len(values) else None
        product_name = clean_text(record.get("product_name"))
        if not product_name or product_name == "Итого и среднее":
            continue
        row: dict[str, Any] = {
            "report_date": report_date,
            "period_from": period_from,
            "period_to": period_to,
            "source_file": str(file_path),
            "source_sheet": "Sheet1",
            "source_row_num": source_index,
        }
        for spec in COLUMNS:
            value = record.get(spec.db_name)
            if spec.kind == "text":
                row[spec.db_name] = clean_text(value)
            elif spec.kind == "int":
                row[spec.db_name] = to_int(value)
            else:
                row[spec.db_name] = to_numeric(value)
        row["seller_article"] = extract_seller_article(row.get("product_name"))
        row["row_key"] = row_key(report_date, file_path, source_index)
        rows.append(row)

    return period_from, period_to, rows


class OzonFunnelImporter:
    def __init__(
        self,
        source_dir: Path,
        dry_run: bool = False,
        max_files: int | None = None,
        only_file: Path | None = None,
        force_reimport: bool = False,
        skip_import: bool = False,
        skip_views: bool = False,
        skip_dependent_views: bool = False,
    ) -> None:
        self.source_dir = source_dir
        self.dry_run = dry_run
        self.max_files = max_files
        self.only_file = only_file
        self.force_reimport = force_reimport
        self.skip_import = skip_import
        self.skip_views = skip_views
        self.skip_dependent_views = skip_dependent_views
        self.conn = None
        self.cur = None

    def connect(self) -> None:
        self.conn = psycopg2.connect(**app.read_db_config())
        self.conn.autocommit = False
        self.cur = self.conn.cursor()

    def close(self) -> None:
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        assert self.cur is not None
        self.cur.execute(query, params)

    def create_schema(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_funnel_daily (
                row_key text PRIMARY KEY,
                id bigserial,
                report_date date NOT NULL,
                period_from date,
                period_to date,
                product_name text,
                seller_article text,
                category_level_1 text,
                category_level_2 text,
                category_level_3 text,
                brand text,
                model text,
                work_schema text,
                sku text,
                barcode text,
                abc_orders_amount text,
                abc_orders_qty text,
                ordered_amount_rub numeric,
                ordered_amount_dynamic numeric,
                search_catalog_position numeric,
                search_catalog_position_dynamic numeric,
                impressions_total bigint,
                impressions_total_dynamic numeric,
                impressions_search_catalog bigint,
                impressions_search_catalog_dynamic numeric,
                impressions_card bigint,
                card_visits bigint,
                card_visits_dynamic numeric,
                cart_adds bigint,
                cart_adds_dynamic numeric,
                cart_adds_search_catalog bigint,
                cart_adds_card bigint,
                sessions_total bigint,
                sessions_search_catalog bigint,
                sessions_card bigint,
                returned_units bigint,
                cancelled_units bigint,
                delivered_units bigint,
                funnel_contract text,
                ordered_units bigint,
                ordered_units_dynamic numeric,
                source_file text,
                source_sheet text,
                source_row_num integer,
                imported_at timestamp without time zone DEFAULT now()
            )
            """
        )
        funnel_api_columns = {
            "impressions_card": "bigint",
            "cart_adds_search_catalog": "bigint",
            "cart_adds_card": "bigint",
            "sessions_total": "bigint",
            "sessions_search_catalog": "bigint",
            "sessions_card": "bigint",
            "returned_units": "bigint",
            "cancelled_units": "bigint",
            "delivered_units": "bigint",
            "funnel_contract": "text",
        }
        for column_name, column_type in funnel_api_columns.items():
            self.execute(
                f"ALTER TABLE public.ozon_funnel_daily "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_funnel_import_files (
                source_file text PRIMARY KEY,
                period_from date,
                period_to date,
                rows_imported integer,
                file_size_bytes bigint,
                file_mtime timestamp without time zone,
                imported_at timestamp without time zone DEFAULT now(),
                status text,
                error text
            )
            """
        )
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_report_date ON public.ozon_funnel_daily(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_sku ON public.ozon_funnel_daily(sku)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_barcode ON public.ozon_funnel_daily(barcode)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_seller_article ON public.ozon_funnel_daily(seller_article)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_category3 ON public.ozon_funnel_daily(category_level_3)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_date_sku ON public.ozon_funnel_daily(report_date, sku)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_funnel_daily_source_file ON public.ozon_funnel_daily(source_file)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_sku ON public.ozon_cat_products(sku)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_artikul_upper ON public.ozon_cat_products(upper(artikul))",
            "CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_barcode ON public.ozon_cat_products(shtrihkod_seriynyy_nomer_ean)",
        ]
        for index_sql in indexes:
            self.execute(index_sql)
        self.conn.commit()

    def imported_file_state(self) -> dict[str, tuple[int | None, datetime | None, str | None, int | None]]:
        assert self.cur is not None
        self.execute(
            """
            SELECT source_file, file_size_bytes, file_mtime, status, rows_imported
            FROM public.ozon_funnel_import_files
            """
        )
        return {
            source_file: (file_size_bytes, file_mtime, status, rows_imported)
            for source_file, file_size_bytes, file_mtime, status, rows_imported in self.cur.fetchall()
        }

    def should_skip_file(
        self,
        file_path: Path,
        imported: dict[str, tuple[int | None, datetime | None, str | None, int | None]],
    ) -> bool:
        if self.force_reimport or self.dry_run:
            return False
        state = imported.get(str(file_path))
        if not state:
            return False
        file_size, file_mtime, status, rows_imported = state
        if status != "ok" or int(file_size or -1) != file_path.stat().st_size:
            return False
        if not rows_imported:
            return False
        if not file_mtime:
            return False
        return abs(file_mtime.timestamp() - file_path.stat().st_mtime) < 2

    def import_file(self, file_path: Path) -> int:
        assert self.cur is not None
        stat = file_path.stat()
        try:
            period_from, period_to, rows = read_rows(file_path)
            if self.dry_run:
                print(f"{file_path.name}: {len(rows):,} rows | {period_from} - {period_to}")
                return len(rows)
            if not rows:
                self.execute(
                    """
                    INSERT INTO public.ozon_funnel_import_files (
                        source_file, period_from, period_to, rows_imported,
                        file_size_bytes, file_mtime, imported_at, status, error
                    )
                    VALUES (%s, %s, %s, 0, %s, %s, now(), 'empty', %s)
                    ON CONFLICT (source_file) DO UPDATE SET
                        period_from = EXCLUDED.period_from,
                        period_to = EXCLUDED.period_to,
                        rows_imported = 0,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        file_mtime = EXCLUDED.file_mtime,
                        imported_at = now(),
                        status = 'empty',
                        error = EXCLUDED.error
                    """,
                    (
                        str(file_path),
                        period_from,
                        period_to,
                        stat.st_size,
                        datetime.fromtimestamp(stat.st_mtime),
                        "Parsed 0 data rows; file will be retried on the next import run.",
                    ),
                )
                self.conn.commit()
                return 0

            columns = [
                "row_key",
                "report_date",
                "period_from",
                "period_to",
                "product_name",
                "seller_article",
                "category_level_1",
                "category_level_2",
                "category_level_3",
                "brand",
                "model",
                "work_schema",
                "sku",
                "barcode",
                "abc_orders_amount",
                "abc_orders_qty",
                "ordered_amount_rub",
                "ordered_amount_dynamic",
                "search_catalog_position",
                "search_catalog_position_dynamic",
                "impressions_total",
                "impressions_total_dynamic",
                "impressions_search_catalog",
                "impressions_search_catalog_dynamic",
                "card_visits",
                "card_visits_dynamic",
                "cart_adds",
                "cart_adds_dynamic",
                "ordered_units",
                "ordered_units_dynamic",
                "source_file",
                "source_sheet",
                "source_row_num",
            ]
            values = [tuple(row.get(column) for column in columns) for row in rows]
            self.execute("DELETE FROM public.ozon_funnel_daily WHERE source_file = %s", (str(file_path),))
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf, lineterminator="\n")
            writer.writerows(values)
            csv_buf.seek(0)
            self.cur.copy_expert(
                f"""
                COPY public.ozon_funnel_daily ({", ".join(columns)})
                FROM STDIN WITH (FORMAT csv)
                """,
                csv_buf,
            )
            self.execute(
                """
                INSERT INTO public.ozon_funnel_import_files (
                    source_file, period_from, period_to, rows_imported,
                    file_size_bytes, file_mtime, imported_at, status, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    period_from = EXCLUDED.period_from,
                    period_to = EXCLUDED.period_to,
                    rows_imported = EXCLUDED.rows_imported,
                    file_size_bytes = EXCLUDED.file_size_bytes,
                    file_mtime = EXCLUDED.file_mtime,
                    imported_at = now(),
                    status = 'ok',
                    error = NULL
                """,
                (
                    str(file_path),
                    period_from,
                    period_to,
                    len(rows),
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime),
                ),
            )
            self.conn.commit()
            return len(rows)
        except Exception as exc:
            self.conn.rollback()
            logger.exception("Failed to import %s", file_path)
            if not self.dry_run:
                self.execute(
                    """
                    INSERT INTO public.ozon_funnel_import_files (
                        source_file, rows_imported, file_size_bytes, file_mtime,
                        imported_at, status, error
                    )
                    VALUES (%s, 0, %s, %s, now(), 'error', %s)
                    ON CONFLICT (source_file) DO UPDATE SET
                        rows_imported = 0,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        file_mtime = EXCLUDED.file_mtime,
                        imported_at = now(),
                        status = 'error',
                        error = EXCLUDED.error
                    """,
                    (
                        str(file_path),
                        stat.st_size,
                        datetime.fromtimestamp(stat.st_mtime),
                        str(exc)[:2000],
                    ),
                )
                self.conn.commit()
            print(f"ERROR {file_path}: {exc}")
            return 0

    def rebuild_view(self) -> None:
        has_ozon_adv_view = regclass_exists(self.cur, OZON_ADV_VIEW)
        if has_ozon_adv_view:
            self.execute(f"DROP MATERIALIZED VIEW IF EXISTS {OZON_ADV_VIEW}")
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_daily_summary_rollup")
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_daily_product_rollup")
        drop_ozon_abc_materialized_views(self.cur)
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_product_options")
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_filter_options")
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_funnel_daily_by_article_category")
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_ozon_funnel_daily_by_article_category AS
            SELECT
                f.report_date,
                f.period_from,
                f.period_to,
                f.seller_article,
                f.sku,
                f.barcode,
                COALESCE(sku_cp.product_id, barcode_cp.product_id, article_cp.product_id) AS product_id,
                COALESCE(sku_cp.artikul, barcode_cp.artikul, article_cp.artikul, f.seller_article) AS product_artikul,
                COALESCE(sku_cp.nazvanie_tovara, barcode_cp.nazvanie_tovara, article_cp.nazvanie_tovara, f.product_name) AS product_name,
                COALESCE(sku_cp.category_id, barcode_cp.category_id, article_cp.category_id) AS category_id,
                COALESCE(sku_cp.category_name, barcode_cp.category_name, article_cp.category_name, f.category_level_3, f.category_level_2, f.category_level_1) AS category_name,
                CASE
                    WHEN sku_cp.product_id IS NOT NULL THEN 'sku'
                    WHEN barcode_cp.product_id IS NOT NULL THEN 'barcode'
                    WHEN article_cp.product_id IS NOT NULL THEN 'seller_article'
                    ELSE 'source'
                END AS match_type,
                f.category_level_1,
                f.category_level_2,
                f.category_level_3,
                f.brand,
                f.model,
                f.work_schema,
                f.abc_orders_amount,
                f.abc_orders_qty,
                f.ordered_amount_rub,
                f.ordered_amount_dynamic,
                f.search_catalog_position,
                f.search_catalog_position_dynamic,
                f.impressions_total,
                f.impressions_total_dynamic,
                f.impressions_search_catalog,
                f.impressions_search_catalog_dynamic,
                f.impressions_card,
                f.card_visits,
                f.card_visits_dynamic,
                f.cart_adds,
                f.cart_adds_dynamic,
                f.cart_adds_search_catalog,
                f.cart_adds_card,
                f.sessions_total,
                f.sessions_search_catalog,
                f.sessions_card,
                f.returned_units,
                f.cancelled_units,
                f.delivered_units,
                f.funnel_contract,
                f.ordered_units,
                f.ordered_units_dynamic,
                CASE WHEN COALESCE(f.impressions_search_catalog, 0) <> 0
                    THEN round(f.card_visits::numeric / f.impressions_search_catalog::numeric * 100, 4)
                    ELSE 0 END AS search_to_card_visit_pct,
                CASE WHEN COALESCE(f.impressions_total, 0) <> 0
                    THEN round(f.card_visits::numeric / f.impressions_total::numeric * 100, 4)
                    ELSE 0 END AS total_impression_to_card_visit_pct,
                CASE WHEN COALESCE(f.card_visits, 0) <> 0
                    THEN round(f.cart_adds::numeric / f.card_visits::numeric * 100, 4)
                    ELSE 0 END AS card_visit_to_cart_pct,
                CASE WHEN COALESCE(f.cart_adds, 0) <> 0
                    THEN round(f.ordered_units::numeric / f.cart_adds::numeric * 100, 4)
                    ELSE 0 END AS cart_to_order_pct,
                CASE WHEN COALESCE(f.card_visits, 0) <> 0
                    THEN round(f.ordered_units::numeric / f.card_visits::numeric * 100, 4)
                    ELSE 0 END AS card_visit_to_order_pct,
                CASE WHEN COALESCE(f.ordered_units, 0) <> 0
                    THEN round(f.ordered_amount_rub::numeric / f.ordered_units::numeric, 2)
                    ELSE 0 END AS ordered_amount_per_unit_rub,
                f.source_file,
                f.source_sheet,
                f.source_row_num,
                f.imported_at
            FROM public.ozon_funnel_daily f
            LEFT JOIN LATERAL (
                SELECT product_id, sku, artikul, nazvanie_tovara, category_id, category_name
                FROM public.ozon_cat_products cp
                WHERE NULLIF(f.sku, '') IS NOT NULL AND cp.sku = f.sku
                ORDER BY imported_at DESC NULLS LAST, product_id
                LIMIT 1
            ) sku_cp ON true
            LEFT JOIN LATERAL (
                SELECT product_id, sku, artikul, nazvanie_tovara, category_id, category_name
                FROM public.ozon_cat_products cp
                WHERE sku_cp.product_id IS NULL
                    AND NULLIF(f.barcode, '') IS NOT NULL
                    AND (
                        cp.shtrihkod_seriynyy_nomer_ean = f.barcode
                        OR (
                            NULLIF(cp.shtrihkod_seriynyy_nomer_ean, '') IS NULL
                            AND cp.artikul = f.barcode
                        )
                    )
                ORDER BY imported_at DESC NULLS LAST, product_id
                LIMIT 1
            ) barcode_cp ON true
            LEFT JOIN LATERAL (
                SELECT product_id, sku, artikul, nazvanie_tovara, category_id, category_name
                FROM public.ozon_cat_products cp
                WHERE sku_cp.product_id IS NULL
                    AND barcode_cp.product_id IS NULL
                    AND NULLIF(f.seller_article, '') IS NOT NULL
                    AND upper(cp.artikul) = upper(f.seller_article)
                ORDER BY imported_at DESC NULLS LAST, product_id
                LIMIT 1
            ) article_cp ON true
            """
        )
        view_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_date ON public.mv_ozon_funnel_daily_by_article_category(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_sku ON public.mv_ozon_funnel_daily_by_article_category(sku)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_date_sku ON public.mv_ozon_funnel_daily_by_article_category(report_date, sku)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_article ON public.mv_ozon_funnel_daily_by_article_category(product_artikul)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_category ON public.mv_ozon_funnel_daily_by_article_category(category_name)",
        ]
        for index_number, index_sql in enumerate(view_indexes, start=1):
            print(
                f"ПРОГРЕСС: индексы Ozon funnel {index_number}/{len(view_indexes)} "
                f"({index_number / len(view_indexes) * 100:.0f}%)",
                flush=True,
            )
            self.execute(index_sql)
        print(
            "ПРОГРЕСС: ANALYZE Ozon funnel | собираем статистику до зависимых витрин",
            flush=True,
        )
        self.execute("ANALYZE public.mv_ozon_funnel_daily_by_article_category")
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_ozon_funnel_filter_options AS
            SELECT
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
                count(DISTINCT category_name) AS categories
            FROM public.mv_ozon_funnel_daily_by_article_category
            """
        )
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_ozon_funnel_product_options AS
            SELECT DISTINCT
                category_name,
                product_name
            FROM public.mv_ozon_funnel_daily_by_article_category
            WHERE product_name IS NOT NULL
              AND product_name <> ''
            """
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_product_options_category "
            "ON public.mv_ozon_funnel_product_options(category_name)"
        )
        self.execute(
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_funnel_product_options_name "
            "ON public.mv_ozon_funnel_product_options(product_name)"
        )
        if not self.skip_dependent_views:
            create_or_refresh_ozon_abc_materialized_views(self.cur)
        rebuild_rollup(self.cur, "mv_ozon_funnel_daily_product_rollup")
        rebuild_rollup(self.cur, "mv_ozon_funnel_daily_summary_rollup")
        if has_ozon_adv_view and not self.skip_dependent_views:
            rebuild_dependent_ozon_adv_view(self.cur)
        self.conn.commit()

    def check(self) -> None:
        checks = [
            (
                "coverage",
                """
                SELECT
                    min(report_date) AS date_from,
                    max(report_date) AS date_to,
                    count(*) AS rows_count,
                    count(DISTINCT report_date) AS days_count,
                    count(DISTINCT sku) AS sku_count,
                    count(DISTINCT seller_article) AS seller_article_count
                FROM public.ozon_funnel_daily
                """,
            ),
            (
                "match_quality",
                """
                SELECT
                    match_type,
                    count(*) AS rows_count,
                    round(count(*)::numeric / nullif(sum(count(*)) over (), 0) * 100, 2) AS share_pct
                FROM public.mv_ozon_funnel_daily_by_article_category
                GROUP BY match_type
                ORDER BY rows_count DESC
                """,
            ),
            (
                "top_categories",
                """
                SELECT
                    category_name,
                    sum(ordered_units) AS ordered_units,
                    sum(ordered_amount_rub) AS ordered_amount_rub,
                    sum(card_visits) AS card_visits
                FROM public.mv_ozon_funnel_daily_by_article_category
                GROUP BY category_name
                ORDER BY ordered_units DESC NULLS LAST
                LIMIT 10
                """,
            ),
        ]
        for title, query in checks:
            print(f"\n[{title}]")
            self.execute(query)
            for row in self.cur.fetchall():
                print(row)

    def run(self) -> None:
        if self.only_file:
            files = [self.only_file]
        else:
            files = sorted(self.source_dir.rglob("*.xlsx"))
        if self.max_files:
            files = files[: self.max_files]
        if not files and not self.skip_import:
            raise RuntimeError(f"No XLSX files found in {self.source_dir}")
        print(f"Files found: {len(files)}")

        self.connect()
        try:
            if not self.dry_run:
                self.create_schema()
                if not self.skip_import and not self.force_reimport:
                    imported = self.imported_file_state()
                    original_count = len(files)
                    files = [file_path for file_path in files if not self.should_skip_file(file_path, imported)]
                    skipped = original_count - len(files)
                    print(f"Already imported skipped: {skipped}")
                    if not files:
                        print("Nothing to import: all files are already up to date.")
                        if self.skip_views:
                            print("Materialized views deferred; run view refresh after imports.", flush=True)
                        return
            total = 0
            errors = 0
            started_at = time.perf_counter()
            if self.skip_import:
                print("Skipping file import; rebuilding materialized views from existing Ozon funnel data.", flush=True)
            else:
                print(
                    f"ПЛАН: файлов к импорту {len(files)} | "
                    "витрины пересобираются после успешной загрузки",
                    flush=True,
                )
                for index, file_path in enumerate(files, start=1):
                    file_started_at = time.perf_counter()
                    filename_short = str(file_path.relative_to(self.source_dir))
                    if len(filename_short) > 54:
                        filename_short = "..." + filename_short[-51:]
                    print_progress(index - 1, len(files), filename_short, total, errors, started_at)

                    rows = self.import_file(file_path)
                    if rows == 0:
                        errors += 1
                    total += rows
                    file_elapsed = time.perf_counter() - file_started_at

                    print_progress(index, len(files), filename_short, total, errors, started_at)
                    print(flush=True)
                    print(
                        f"[{index}/{len(files)}] {filename_short}: imported {rows:,} rows, "
                        f"errors {errors}, file_time {format_duration(file_elapsed)}, total {total:,}",
                        flush=True,
                    )
                    logger.info(
                        "[%s/%s] %s: imported=%s, file_time=%s, total=%s",
                        index,
                        len(files),
                        file_path,
                        rows,
                        format_duration(file_elapsed),
                        total,
                    )
            if not self.dry_run:
                if self.skip_views:
                    print("Materialized views deferred; run view refresh after imports.", flush=True)
                else:
                    print("Rebuilding Ozon funnel materialized views...", flush=True)
                    self.rebuild_view()
                    self.check()
            print(f"\nImported rows: {total:,} | errors: {errors:,} | total time: {format_duration(time.perf_counter() - started_at)}")
        finally:
            self.close()


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--only-file", type=Path, default=None)
    parser.add_argument(
        "--force-reimport",
        action="store_true",
        help="Reimport all matching files even if they were already imported successfully.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Do not read XLSX files; rebuild Ozon funnel materialized views from existing raw data.",
    )
    parser.add_argument(
        "--skip-views",
        action="store_true",
        help="Load source files only; defer materialized-view rebuilds to the dedicated views step.",
    )
    parser.add_argument(
        "--skip-dependent-views",
        action="store_true",
        help="Rebuild only Ozon funnel views; leave Ozon adv/ABC dependent views to separate jobs.",
    )
    args = parser.parse_args()

    importer = OzonFunnelImporter(
        args.source_dir,
        dry_run=args.dry_run,
        max_files=args.max_files,
        only_file=args.only_file,
        force_reimport=args.force_reimport,
        skip_import=args.skip_import,
        skip_views=args.skip_views,
        skip_dependent_views=args.skip_dependent_views,
    )
    importer.run()


if __name__ == "__main__":
    main()
