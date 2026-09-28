#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Import Ozon media advertising campaign reports into PostgreSQL."""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import psycopg2
from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


DEFAULT_SOURCE_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Дашборды\Data\Ozon\Adv"
)
DEFAULT_YEAR = 2026
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_SOURCE_COLS = 32
MONTH_DIRS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


@dataclass(frozen=True)
class ColumnSpec:
    db_name: str
    kind: str
    legacy_names: tuple[str, ...]
    current_names: tuple[str, ...]


COLUMNS = [
    ColumnSpec("campaign_id", "text", ("ID",), ("ID",)),
    ColumnSpec("campaign_name", "text", ("Название",), ("Название кампании",)),
    ColumnSpec("campaign_format", "text", ("Формат",), ("Формат",)),
    ColumnSpec("campaign_status", "text", ("Статус",), ("Статус",)),
    ColumnSpec("daily_budget_rub", "numeric", ("Дневной бюджет, ₽",), ()),
    ColumnSpec("campaign_budget_rub", "numeric", ("Бюджет кампании, ₽",), ()),
    ColumnSpec("budget_type", "text", ("Тип бюджета",), ("Тип бюджета",)),
    ColumnSpec("payment_model", "text", ("Способ оплаты",), ("Тип оплаты",)),
    ColumnSpec("expense_rub", "numeric", ("Расход, ₽",), ("Расход",)),
    ColumnSpec("impressions", "int", ("Показы",), ("Показы",)),
    ColumnSpec("clicks", "int", ("Клики",), ("Клики",)),
    ColumnSpec("ctr_pct", "numeric", ("CTR, %",), ("CTR",)),
    ColumnSpec(
        "cpm_rub",
        "numeric",
        ("Ср. стоимость 1000 показов, ₽",),
        ("Средняя стоимость 1000 показов",),
    ),
    ColumnSpec(
        "cpc_rub",
        "numeric",
        ("Ср. стоимость клика, ₽",),
        ("Средняя стоимость клика",),
    ),
    ColumnSpec("orders_qty", "int", ("Заказы, шт.",), ("Продано товаров",)),
    ColumnSpec("orders_amount_rub", "numeric", ("Заказы, ₽",), ("Продажи",)),
    ColumnSpec(
        "post_view_orders_qty",
        "int",
        ("Заказы post-view, шт",),
        ("Продано товаров после просмотра",),
    ),
    ColumnSpec(
        "post_view_revenue_rub",
        "numeric",
        ("Выручка post-view, ₽",),
        ("Продажи после просмотра",),
    ),
    ColumnSpec("drr_pct", "numeric", ("ДРР, %",), ("ДРР в продвижении",)),
]
DB_COLUMNS = [column.db_name for column in COLUMNS]

LEGACY_MEDIA_REQUIRED = {
    "ID", "Название", "Формат", "Статус", "Дневной бюджет, ₽",
    "Бюджет кампании, ₽", "Расход, ₽", "Показы", "Клики",
    "Заказы, шт.", "Заказы post-view, шт",
}
CURRENT_MEDIA_REQUIRED = {
    "ID", "Название кампании", "Статус", "Формат", "Тип оплаты",
    "Бюджет", "Тип бюджета", "Показы", "Клики", "Расход",
    "Продано товаров", "Продано товаров после просмотра",
}
PRODUCT_ADV_REQUIRED = {
    "ID кампании", "Название кампании", "Места размещения", "Стратегия",
    "Добавления в корзину",
}
NON_MEDIA_REPORT_SIGNATURES = (
    {"SKU", "Товары", "Продажи", "Воронка продаж"},
    {"SKU", "Артикул", "Продажи"},
)


class WrongReportFamilyError(ValueError):
    """Raised when a recognizable non-media report is placed in the media folder."""


@dataclass(frozen=True)
class DetectedMediaTable:
    schema_version: str
    sheet_name: str
    sheet_path: str
    header_row_num: int
    rows: dict[int, list[str | None]]


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes:d}m {secs:02d}s"
    return f"{secs:d}s"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "-", "–", "—"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def to_numeric(value: Any) -> float | None:
    text = clean_text(value)
    if text is None:
        return None
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".").rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_numeric(value)
    if number is None:
        return None
    return int(round(number))


def normalize_header(value: Any) -> str:
    return re.sub(r"\s+", " ", clean_text(value) or "").strip()


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            strings: list[str] = []
            for _, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag == XML_NS + "si":
                    strings.append("".join(t.text or "" for t in elem.iter(XML_NS + "t")))
                    elem.clear()
            return strings
    except KeyError:
        return []


def normalize_sheet_target(target: str) -> str:
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target


def workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        rel.attrib["Id"]: normalize_sheet_target(rel.attrib["Target"])
        for rel in rels
    }
    sheets = workbook.find(XML_NS + "sheets")
    if sheets is None:
        return []
    return [
        (sheet.attrib.get("name", ""), rid_to_target.get(sheet.attrib.get(REL_NS + "id"), ""))
        for sheet in sheets
    ]


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
        return text if text else None

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


def read_sheet_rows(
    file_path: Path,
    sheet_path: str,
    shared_strings: list[str],
    *,
    max_col: int = MAX_SOURCE_COLS,
) -> dict[int, list[str | None]]:
    rows: dict[int, list[str | None]] = {}
    with zipfile.ZipFile(file_path, "r") as zf:
        with zf.open(sheet_path) as fh:
            for _, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != XML_NS + "row":
                    continue
                row_num = int(elem.attrib.get("r", "0"))
                values: list[str | None] = [None] * max_col
                for cell in elem.findall(XML_NS + "c"):
                    idx = column_index(cell.attrib.get("r", ""))
                    if 0 <= idx < max_col:
                        values[idx] = cell_text(cell, shared_strings)
                rows[row_num] = values
                elem.clear()
    return rows


def report_date_from_path(file_path: Path, default_year: int) -> date:
    month = MONTH_DIRS.get(file_path.parent.name) or MONTH_DIRS.get(file_path.parent.name.title())
    if not month:
        raise ValueError(f"Cannot infer month from parent folder: {file_path.parent.name}")
    day_text = file_path.stem
    dated_name = re.search(r"(\d{4}-\d{2}-\d{2})", file_path.stem)
    if dated_name:
        return datetime.strptime(dated_name.group(1), "%Y-%m-%d").date()
    if not day_text.isdigit():
        raise ValueError(f"Cannot infer day from filename: {file_path.name}")
    return date(default_year, month, int(day_text))


def source_file_value(source_dir: Path, file_path: Path) -> str:
    try:
        return str(file_path.relative_to(source_dir))
    except ValueError:
        return str(file_path)


def row_value(row: list[str | None], positions: dict[str, int], header: str) -> str | None:
    index = positions.get(header)
    if index is None or index >= len(row):
        return None
    return row[index]


def first_present_value(
    row: list[str | None],
    positions: dict[str, int],
    names: Iterable[str],
) -> str | None:
    for name in names:
        if name in positions:
            return row_value(row, positions, name)
    return None


def detect_media_table(file_path: Path) -> DetectedMediaTable:
    wrong_family_found = False
    with zipfile.ZipFile(file_path, "r") as zf:
        sheet_map = workbook_sheets(zf)
        shared_strings = read_shared_strings(zf)

    for sheet_name, sheet_path in sheet_map:
        if not sheet_path:
            continue
        rows = read_sheet_rows(file_path, sheet_path, shared_strings)
        for row_num in sorted(row for row in rows if 1 <= row <= 10):
            headers = {
                normalize_header(value)
                for value in rows[row_num]
                if normalize_header(value)
            }
            if LEGACY_MEDIA_REQUIRED <= headers:
                return DetectedMediaTable("legacy", sheet_name, sheet_path, row_num, rows)
            if CURRENT_MEDIA_REQUIRED <= headers:
                return DetectedMediaTable("current", sheet_name, sheet_path, row_num, rows)
            if PRODUCT_ADV_REQUIRED <= headers or any(
                signature <= headers for signature in NON_MEDIA_REPORT_SIGNATURES
            ):
                wrong_family_found = True

    if wrong_family_found:
        raise WrongReportFamilyError(
            f"Non-media Ozon export found in media folder: {file_path.name}"
        )
    sheet_names = ", ".join(name for name, _ in sheet_map) or "<none>"
    raise ValueError(
        f"Unrecognized Ozon media export schema in {file_path.name}; sheets: {sheet_names}"
    )


def read_media_rows(source_dir: Path, file_path: Path, default_year: int):
    detected = detect_media_table(file_path)
    rows = detected.rows
    header = rows[detected.header_row_num]
    headers = [normalize_header(value) for value in header]
    positions = {name: index for index, name in enumerate(headers) if name}
    aliases_attr = "legacy_names" if detected.schema_version == "legacy" else "current_names"
    missing = [
        column.db_name
        for column in COLUMNS
        if column.db_name not in {"daily_budget_rub", "campaign_budget_rub"}
        and not any(name in positions for name in getattr(column, aliases_attr))
    ]
    if missing:
        raise ValueError(f"Missing columns in {file_path}: {missing}")

    report_date = report_date_from_path(file_path, default_year)
    stat = file_path.stat()
    source_file = source_file_value(source_dir, file_path)
    for row_num in sorted(row for row in rows if row > detected.header_row_num):
        values = rows[row_num]
        campaign_id = clean_text(row_value(values, positions, "ID"))
        campaign_name = clean_text(
            first_present_value(values, positions, ("Название", "Название кампании"))
        )
        if not campaign_id and not campaign_name:
            continue
        parsed: dict[str, Any] = {
            "report_date": report_date,
            "source_file": source_file,
            "source_sheet": detected.sheet_name,
            "source_row_num": row_num,
            "file_size_bytes": stat.st_size,
            "file_mtime": datetime.fromtimestamp(stat.st_mtime),
        }
        for column in COLUMNS:
            names = getattr(column, aliases_attr)
            value = first_present_value(values, positions, names)
            if column.kind == "text":
                parsed[column.db_name] = clean_text(value)
            elif column.kind == "int":
                parsed[column.db_name] = to_int(value)
            else:
                parsed[column.db_name] = to_numeric(value)

        if detected.schema_version == "current":
            budget = to_numeric(row_value(values, positions, "Бюджет"))
            budget_type = (parsed.get("budget_type") or "").lower()
            parsed["daily_budget_rub"] = budget if "день" in budget_type else None
            parsed["campaign_budget_rub"] = budget if "день" not in budget_type else None
            if parsed["ctr_pct"] is not None:
                parsed["ctr_pct"] *= 100
            if parsed["drr_pct"] is not None:
                parsed["drr_pct"] *= 100
        yield tuple(
            parsed[column]
            for column in [
                "report_date",
                *DB_COLUMNS,
                "source_file",
                "source_sheet",
                "source_row_num",
                "file_size_bytes",
                "file_mtime",
            ]
        )


class OzonMediaAdvImporter:
    def __init__(
        self,
        source_dir: Path,
        *,
        default_year: int = DEFAULT_YEAR,
        dry_run: bool = False,
        force_reimport: bool = False,
        max_files: int | None = None,
        skip_import: bool = False,
        skip_views: bool = False,
    ) -> None:
        self.source_dir = source_dir
        self.default_year = default_year
        self.dry_run = dry_run
        self.force_reimport = force_reimport
        self.max_files = max_files
        self.skip_import = skip_import
        self.skip_views = skip_views
        self.conn = None
        self.cur = None

    def connect(self) -> None:
        self.conn = app.get_conn()
        self.cur = self.conn.cursor()

    def close(self) -> None:
        if self.cur is not None:
            self.cur.close()
        if self.conn is not None:
            self.conn.close()

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        assert self.cur is not None
        self.cur.execute(query, params)

    def create_schema(self, *, drop_views: bool = True) -> None:
        if drop_views:
            self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_media_adv_daily")
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_media_adv_daily_raw (
                id bigserial PRIMARY KEY,
                report_date date NOT NULL,
                campaign_id text,
                campaign_name text,
                campaign_format text,
                campaign_status text,
                daily_budget_rub numeric,
                campaign_budget_rub numeric,
                budget_type text,
                payment_model text,
                expense_rub numeric,
                impressions bigint,
                clicks bigint,
                ctr_pct numeric,
                cpm_rub numeric,
                cpc_rub numeric,
                orders_qty bigint,
                orders_amount_rub numeric,
                post_view_orders_qty bigint,
                post_view_revenue_rub numeric,
                drr_pct numeric,
                source_file text NOT NULL,
                source_sheet text NOT NULL,
                source_row_num int NOT NULL,
                file_size_bytes bigint,
                file_mtime timestamp,
                imported_at timestamp NOT NULL DEFAULT now(),
                UNIQUE (source_file, source_sheet, source_row_num)
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_media_adv_import_files (
                source_file text PRIMARY KEY,
                report_date date,
                rows_imported int NOT NULL DEFAULT 0,
                file_size_bytes bigint,
                file_mtime timestamp,
                imported_at timestamp NOT NULL DEFAULT now(),
                status text NOT NULL DEFAULT 'ok',
                error text
            )
            """
        )
        if self.force_reimport:
            self.execute("TRUNCATE TABLE public.ozon_media_adv_daily_raw RESTART IDENTITY")
            self.execute("TRUNCATE TABLE public.ozon_media_adv_import_files")
        self.conn.commit()

    def imported_file_state(self) -> dict[str, tuple[int | None, datetime | None, str | None]]:
        self.execute(
            """
            SELECT source_file, file_size_bytes, file_mtime, status
            FROM public.ozon_media_adv_import_files
            """
        )
        return {
            row["source_file"]: (row["file_size_bytes"], row["file_mtime"], row["status"])
            for row in self.cur.fetchall()
        }

    def should_skip_file(self, file_path: Path, imported: dict[str, tuple[int | None, datetime | None, str | None]]) -> bool:
        source_file = source_file_value(self.source_dir, file_path)
        state = imported.get(source_file)
        if state is None:
            return False
        size, mtime, status = state
        stat = file_path.stat()
        return status in {"ok", "ignored"} and size == stat.st_size and mtime and abs(mtime.timestamp() - stat.st_mtime) < 1

    def import_file(self, file_path: Path) -> int:
        assert self.cur is not None and self.conn is not None
        source_file = source_file_value(self.source_dir, file_path)
        stat = file_path.stat()
        try:
            rows = list(read_media_rows(self.source_dir, file_path, self.default_year))
            if self.dry_run:
                return len(rows)

            self.execute(
                "DELETE FROM public.ozon_media_adv_daily_raw WHERE source_file = %s",
                (source_file,),
            )
            csv_buf = io.StringIO()
            writer = csv.writer(csv_buf, lineterminator="\n")
            writer.writerows(rows)
            csv_buf.seek(0)
            columns = [
                "report_date",
                *DB_COLUMNS,
                "source_file",
                "source_sheet",
                "source_row_num",
                "file_size_bytes",
                "file_mtime",
            ]
            self.cur.copy_expert(
                f"""
                COPY public.ozon_media_adv_daily_raw ({", ".join(columns)})
                FROM STDIN WITH (FORMAT csv)
                """,
                csv_buf,
            )
            report_date = rows[0][0]
            self.execute(
                """
                INSERT INTO public.ozon_media_adv_import_files (
                    source_file, report_date, rows_imported, file_size_bytes,
                    file_mtime, imported_at, status, error
                )
                VALUES (%s, %s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET
                    report_date = EXCLUDED.report_date,
                    rows_imported = EXCLUDED.rows_imported,
                    file_size_bytes = EXCLUDED.file_size_bytes,
                    file_mtime = EXCLUDED.file_mtime,
                    imported_at = now(),
                    status = 'ok',
                    error = NULL
                """,
                (
                    source_file,
                    report_date,
                    len(rows),
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime),
                ),
            )
            self.conn.commit()
            return len(rows)
        except WrongReportFamilyError as exc:
            if not self.dry_run:
                self.conn.rollback()
                self.execute(
                    """
                    INSERT INTO public.ozon_media_adv_import_files (
                        source_file, rows_imported, file_size_bytes, file_mtime,
                        imported_at, status, error
                    )
                    VALUES (%s, 0, %s, %s, now(), 'ignored', %s)
                    ON CONFLICT (source_file) DO UPDATE SET
                        rows_imported = 0,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        file_mtime = EXCLUDED.file_mtime,
                        imported_at = now(),
                        status = 'ignored',
                        error = EXCLUDED.error
                    """,
                    (
                        source_file,
                        stat.st_size,
                        datetime.fromtimestamp(stat.st_mtime),
                        str(exc)[:2000],
                    ),
                )
                self.conn.commit()
            print(f"IGNORED {file_path}: {exc}")
            return 0
        except Exception as exc:
            if not self.dry_run:
                self.conn.rollback()
                self.execute(
                    """
                    INSERT INTO public.ozon_media_adv_import_files (
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
                        source_file,
                        stat.st_size,
                        datetime.fromtimestamp(stat.st_mtime),
                        str(exc)[:2000],
                    ),
                )
                self.conn.commit()
            print(f"ERROR {file_path}: {exc}")
            return 0

    def rebuild_view(self) -> None:
        self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_media_adv_daily")
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_ozon_media_adv_daily AS
            SELECT
                report_date,
                campaign_id,
                campaign_name,
                campaign_format AS category_name,
                campaign_format,
                campaign_status,
                CASE
                    WHEN campaign_name ILIKE '%оффервол%' THEN 'Оффервол'
                    WHEN campaign_name ILIKE '%поиск%' OR campaign_name ILIKE '%каталог%' THEN 'Поиск и каталог'
                    WHEN campaign_name ILIKE '%конкурент%' THEN 'Конкуренты'
                    WHEN campaign_name ILIKE '%сегмент%' OR campaign_name ILIKE '%покупател%' OR campaign_name ILIKE '%аудитор%' THEN 'Аудиторные сегменты'
                    WHEN campaign_name ILIKE '%распродаж%' THEN 'Распродажа'
                    ELSE 'Прочее'
                END AS campaign_segment,
                NULLIF(
                    trim(both ' _-' from regexp_replace(coalesce(campaign_name, ''), '.*[_-]', '')),
                    ''
                ) AS promoted_category,
                campaign_name AS product_name,
                campaign_id AS product_artikul,
                daily_budget_rub,
                campaign_budget_rub,
                budget_type,
                payment_model,
                expense_rub,
                impressions,
                clicks,
                ctr_pct,
                CASE WHEN coalesce(impressions, 0) <> 0
                    THEN round(coalesce(clicks, 0)::numeric / impressions::numeric * 100, 4)
                    ELSE 0 END AS ctr_calc_pct,
                cpm_rub,
                CASE WHEN coalesce(impressions, 0) <> 0
                    THEN round(coalesce(expense_rub, 0)::numeric / impressions::numeric * 1000, 4)
                    ELSE 0 END AS cpm_calc_rub,
                cpc_rub,
                CASE WHEN coalesce(clicks, 0) <> 0
                    THEN round(coalesce(expense_rub, 0)::numeric / clicks::numeric, 4)
                    ELSE 0 END AS cpc_calc_rub,
                orders_qty,
                orders_amount_rub,
                post_view_orders_qty,
                post_view_revenue_rub,
                coalesce(orders_qty, 0) + coalesce(post_view_orders_qty, 0) AS attributed_orders_qty,
                coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0) AS attributed_revenue_rub,
                drr_pct,
                CASE WHEN coalesce(orders_amount_rub, 0) <> 0
                    THEN round(coalesce(expense_rub, 0)::numeric / orders_amount_rub::numeric * 100, 4)
                    ELSE 0 END AS drr_direct_pct,
                CASE WHEN coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0) <> 0
                    THEN round(coalesce(expense_rub, 0)::numeric / (coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0))::numeric * 100, 4)
                    ELSE 0 END AS drr_attributed_pct,
                CASE WHEN coalesce(expense_rub, 0) <> 0
                    THEN round(coalesce(orders_amount_rub, 0)::numeric / expense_rub::numeric, 4)
                    ELSE 0 END AS direct_roas,
                CASE WHEN coalesce(expense_rub, 0) <> 0
                    THEN round((coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0))::numeric / expense_rub::numeric, 4)
                    ELSE 0 END AS attributed_roas,
                CASE WHEN coalesce(clicks, 0) <> 0
                    THEN round(coalesce(orders_qty, 0)::numeric / clicks::numeric * 100, 4)
                    ELSE 0 END AS click_to_order_pct,
                CASE WHEN coalesce(impressions, 0) <> 0
                    THEN round(coalesce(post_view_orders_qty, 0)::numeric / impressions::numeric * 1000, 4)
                    ELSE 0 END AS post_view_orders_per_1000_impressions,
                CASE WHEN coalesce(impressions, 0) <> 0
                    THEN round((coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0))::numeric / impressions::numeric * 1000, 4)
                    ELSE 0 END AS revenue_per_1000_impressions,
                CASE WHEN coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0) <> 0
                    THEN round(coalesce(post_view_revenue_rub, 0)::numeric / (coalesce(orders_amount_rub, 0) + coalesce(post_view_revenue_rub, 0))::numeric * 100, 4)
                    ELSE 0 END AS post_view_revenue_share_pct,
                source_file,
                source_sheet,
                source_row_num,
                file_size_bytes,
                file_mtime,
                imported_at
            FROM public.ozon_media_adv_daily_raw
            """
        )
        for index_sql in [
            "CREATE INDEX IF NOT EXISTS idx_ozon_media_adv_raw_source ON public.ozon_media_adv_daily_raw(source_file, source_sheet, source_row_num)",
            "CREATE INDEX IF NOT EXISTS idx_ozon_media_adv_raw_date ON public.ozon_media_adv_daily_raw(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_media_adv_date ON public.mv_ozon_media_adv_daily(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_media_adv_format ON public.mv_ozon_media_adv_daily(campaign_format)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_media_adv_campaign ON public.mv_ozon_media_adv_daily(campaign_id)",
            "CREATE INDEX IF NOT EXISTS idx_mv_ozon_media_adv_expense ON public.mv_ozon_media_adv_daily(expense_rub)",
        ]:
            self.execute(index_sql)
        self.execute("ANALYZE public.ozon_media_adv_daily_raw")
        self.execute("ANALYZE public.mv_ozon_media_adv_daily")
        self.conn.commit()

    def check(self) -> None:
        for title, query in [
            (
                "coverage",
                """
                SELECT
                    count(*) AS rows_count,
                    count(DISTINCT report_date) AS days_count,
                    min(report_date) AS date_from,
                    max(report_date) AS date_to,
                    count(DISTINCT campaign_id) AS campaigns,
                    count(DISTINCT campaign_format) AS formats
                FROM public.mv_ozon_media_adv_daily
                """,
            ),
            (
                "totals",
                """
                SELECT
                    round(sum(expense_rub), 2) AS expense_rub,
                    sum(impressions) AS impressions,
                    sum(clicks) AS clicks,
                    sum(orders_qty) AS direct_orders,
                    round(sum(orders_amount_rub), 2) AS direct_revenue,
                    sum(post_view_orders_qty) AS post_view_orders,
                    round(sum(post_view_revenue_rub), 2) AS post_view_revenue,
                    round(sum(attributed_revenue_rub), 2) AS attributed_revenue
                FROM public.mv_ozon_media_adv_daily
                """,
            ),
            (
                "formats",
                """
                SELECT campaign_format, count(DISTINCT campaign_id) AS campaigns, round(sum(expense_rub), 2) AS expense_rub
                FROM public.mv_ozon_media_adv_daily
                GROUP BY campaign_format
                ORDER BY expense_rub DESC NULLS LAST
                """,
            ),
        ]:
            print(f"\n[{title}]")
            self.execute(query)
            for row in self.cur.fetchall():
                print(dict(row))

    def run(self) -> None:
        files: list[Path] = []
        if not self.skip_import:
            files = sorted(
                file for file in self.source_dir.rglob("*.xlsx")
                if not file.name.startswith("~$")
            )
            if self.max_files:
                files = files[: self.max_files]
            if not files:
                raise RuntimeError(f"No XLSX files found in {self.source_dir}")
            print(
                f"ПЛАН: Ozon media | файлов {len(files)} | схема auto legacy/current | "
                f"витрины {'отложены' if self.skip_views else 'после импорта'}",
                flush=True,
            )
        else:
            print("Skipping file import; rebuilding Ozon media adv materialized view from existing raw data.")
        self.connect()
        try:
            if not self.dry_run:
                self.create_schema(drop_views=not self.skip_views)
                if not self.skip_import and not self.force_reimport:
                    imported = self.imported_file_state()
                    original_count = len(files)
                    files = [file for file in files if not self.should_skip_file(file, imported)]
                    print(f"Already imported skipped: {original_count - len(files)}")
                    if not files:
                        print("Nothing to import: all files are already up to date.")
                        if self.skip_views:
                            self.conn.commit()
                            print("Materialized views deferred; run view refresh after imports.")
                        else:
                            print("Rebuilding Ozon media adv materialized view...")
                            self.rebuild_view()
                            self.check()
                        return
            total = 0
            started_at = time.perf_counter()
            if not self.skip_import:
                with tqdm(total=len(files), desc="Import Ozon media adv", unit="file", ncols=120, ascii=True) as pbar:
                    for file_path in files:
                        file_started = time.perf_counter()
                        rows = self.import_file(file_path)
                        total += rows
                        pbar.set_postfix({"rows": f"{total:,}"}, refresh=True)
                        pbar.update(1)
                        current = pbar.n
                        elapsed = time.perf_counter() - started_at
                        eta = (elapsed / current * (len(files) - current)) if current else 0
                        print(
                            f"ПРОГРЕСС: {current}/{len(files)} ({current / len(files) * 100:.1f}%) | "
                            f"{file_path.name} | imported {rows}, total {total} | "
                            f"item {format_duration(time.perf_counter() - file_started)} | ETA {format_duration(eta)}",
                            flush=True,
                        )
            if not self.dry_run:
                if self.skip_views:
                    self.conn.commit()
                    print("Materialized views deferred; run view refresh after imports.")
                else:
                    print("Rebuilding Ozon media adv materialized view...")
                    self.rebuild_view()
                    self.check()
            print(
                f"ИТОГ: Ozon media | imported {total:,} rows | files {len(files)} | "
                f"elapsed {format_duration(time.perf_counter() - started_at)}",
                flush=True,
            )
        finally:
            self.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-reimport", action="store_true")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-views", action="store_true")
    args = parser.parse_args()

    importer = OzonMediaAdvImporter(
        args.source_dir,
        default_year=args.year,
        dry_run=args.dry_run,
        force_reimport=args.force_reimport,
        max_files=args.max_files,
        skip_import=args.skip_import,
        skip_views=args.skip_views,
    )
    importer.run()


if __name__ == "__main__":
    main()
