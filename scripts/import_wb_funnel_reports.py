#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Import Wildberries sales funnel ZIP/XLSX reports into PostgreSQL."""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402
from import_wb_adv_daily_reports import (  # noqa: E402
    DEFAULT_SOURCE_DIR as DEFAULT_WB_ADV_SOURCE_DIR,
    WbAdvImporter,
)
from sportmaster_wb_category_mapping import wb_parent_category_case_sql  # noqa: E402


DEFAULT_SOURCE_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Дашборды\Data\Wb\Funel"
)
LOG_PATH = PROJECT_ROOT / "wb_funnel_import_runtime.log"
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADER_ROW = 2
DATA_START_ROW = 3
MAX_SOURCE_COLS = 80

logger = logging.getLogger("wb_funnel_import")


def connection_database_name(conn: Any) -> str:
    info = getattr(conn, "info", None)
    database_name = getattr(info, "dbname", None)
    if database_name:
        return str(database_name)
    try:
        database_name = (conn.get_dsn_parameters() or {}).get("dbname")
    except (AttributeError, TypeError):
        database_name = None
    if database_name:
        return str(database_name)
    return str(app.read_db_config().get("database") or "")


def wb_dashboard_category_sql(source_expr: str, database_name: str) -> str:
    """Return the client-appropriate dashboard category expression.

    Gloria Jeans reports use the native WB subject ("Предмет") as the product
    category. Sportmaster intentionally keeps its coarser parent mapping so its
    WB and Ozon category levels remain comparable.
    """

    if str(database_name or "").strip().casefold() == "sportmaster":
        return wb_parent_category_case_sql(source_expr)
    return f"coalesce(nullif({source_expr}, ''), 'Без категории')"


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    db_name: str
    kind: str


COLUMNS = [
    ColumnSpec("Артикул продавца", "seller_article", "text"),
    ColumnSpec("Артикул WB", "wb_nmid", "text"),
    ColumnSpec("Название", "product_name", "text"),
    ColumnSpec("Предмет", "category_name", "text"),
    ColumnSpec("Бренд", "brand", "text"),
    ColumnSpec("Удаленный товар", "is_deleted", "text"),
    ColumnSpec("Рейтинг карточки", "card_rating", "numeric"),
    ColumnSpec("Рейтинг по отзывам", "review_rating", "numeric"),
    ColumnSpec("Показы", "impressions_total", "numeric"),
    ColumnSpec("Показы (предыдущий период)", "impressions_total_prev", "numeric"),
    ColumnSpec("CTR", "ctr_pct", "numeric"),
    ColumnSpec("CTR (предыдущий период)", "ctr_pct_prev", "numeric"),
    ColumnSpec("Доля карточки в выручке", "revenue_share_pct", "numeric"),
    ColumnSpec("Доля карточки в выручке (предыдущий период)", "revenue_share_pct_prev", "numeric"),
    ColumnSpec("Переходы в карточку", "card_visits", "numeric"),
    ColumnSpec("Переходы в карточку (предыдущий период)", "card_visits_prev", "numeric"),
    ColumnSpec("Положили в корзину", "cart_adds", "numeric"),
    ColumnSpec("Положили в корзину (предыдущий период)", "cart_adds_prev", "numeric"),
    ColumnSpec("Добавили в отложенные", "favorites_adds", "numeric"),
    ColumnSpec("Добавили в отложенные (предыдущий период)", "favorites_adds_prev", "numeric"),
    ColumnSpec("Заказали, шт", "ordered_units", "numeric"),
    ColumnSpec("Заказали, шт (предыдущий период)", "ordered_units_prev", "numeric"),
    ColumnSpec("Выкупили, шт", "bought_units", "numeric"),
    ColumnSpec("Выкупы, шт (предыдущий период)", "bought_units_prev", "numeric"),
    ColumnSpec("Отменили, шт", "cancelled_units", "numeric"),
    ColumnSpec("Отменили, шт (предыдущий период)", "cancelled_units_prev", "numeric"),
    ColumnSpec("Конверсия в корзину, %", "card_to_cart_pct", "numeric"),
    ColumnSpec("Конверсия в корзину, % (предыдущий период)", "card_to_cart_pct_prev", "numeric"),
    ColumnSpec("Конверсия в заказ, %", "cart_to_order_pct", "numeric"),
    ColumnSpec("Конверсия в заказ, % (предыдущий период)", "cart_to_order_pct_prev", "numeric"),
    ColumnSpec("Процент выкупа", "buyout_pct", "numeric"),
    ColumnSpec("Процент выкупа (предыдущий период)", "buyout_pct_prev", "numeric"),
    ColumnSpec("Заказали на сумму, ₽", "ordered_amount_rub", "numeric"),
    ColumnSpec("Заказали на сумму, ₽ (предыдущий период)", "ordered_amount_rub_prev", "numeric"),
    ColumnSpec("Динамика суммы заказов, ₽", "ordered_amount_dynamic", "numeric"),
    ColumnSpec("Выкупили на сумму, ₽", "bought_amount_rub", "numeric"),
    ColumnSpec("Выкупили на сумму, ₽ (предыдущий период)", "bought_amount_rub_prev", "numeric"),
    ColumnSpec("Отменили на сумму, ₽", "cancelled_amount_rub", "numeric"),
    ColumnSpec("Отменили на сумму, ₽ (предыдущий период)", "cancelled_amount_rub_prev", "numeric"),
    ColumnSpec("Средняя цена, ₽", "avg_price_rub", "numeric"),
    ColumnSpec("Средняя цена, ₽ (предыдущий период)", "avg_price_rub_prev", "numeric"),
    ColumnSpec("Среднее количество заказов в день, шт", "avg_daily_orders_qty", "numeric"),
    ColumnSpec("Среднее количество заказов в день, шт (предыдущий период)", "avg_daily_orders_qty_prev", "numeric"),
    ColumnSpec("Заказали ВБ клуб, шт", "wb_club_ordered_units", "numeric"),
    ColumnSpec("Заказали ВБ клуб, шт (предыдущий период)", "wb_club_ordered_units_prev", "numeric"),
    ColumnSpec("Выкупили ВБ клуб, шт", "wb_club_bought_units", "numeric"),
    ColumnSpec("Выкупы ВБ клуб, шт (предыдущий период)", "wb_club_bought_units_prev", "numeric"),
    ColumnSpec("Отменили ВБ клуб, шт", "wb_club_cancelled_units", "numeric"),
    ColumnSpec("Отменили ВБ клуб, шт (предыдущий период)", "wb_club_cancelled_units_prev", "numeric"),
    ColumnSpec("Заказали на сумму ВБ клуб, ₽", "wb_club_ordered_amount_rub", "numeric"),
    ColumnSpec("Заказали на сумму ВБ клуб, ₽ (предыдущий период)", "wb_club_ordered_amount_rub_prev", "numeric"),
    ColumnSpec("Выкупили на сумму ВБ клуб, ₽", "wb_club_bought_amount_rub", "numeric"),
    ColumnSpec("Выкупили на сумму ВБ клуб, ₽ (предыдущий период)", "wb_club_bought_amount_rub_prev", "numeric"),
    ColumnSpec("Отменили на сумму ВБ клуб, ₽", "wb_club_cancelled_amount_rub", "numeric"),
    ColumnSpec("Отменили на сумму ВБ клуб, ₽ (предыдущий период)", "wb_club_cancelled_amount_rub_prev", "numeric"),
    ColumnSpec("Процент выкупа ВБ клуб", "wb_club_buyout_pct", "numeric"),
    ColumnSpec("Процент выкупа ВБ клуб (предыдущий период)", "wb_club_buyout_pct_prev", "numeric"),
    ColumnSpec("Среднее количество заказов в день ВБ клуб, шт", "wb_club_avg_daily_orders_qty", "numeric"),
    ColumnSpec("Среднее количество заказов в день ВБ клуб, шт (предыдущий период)", "wb_club_avg_daily_orders_qty_prev", "numeric"),
    ColumnSpec("Остатки «Склад WB», шт", "stock_wb_qty", "numeric"),
    ColumnSpec("Остатки «Свой склад», шт", "stock_own_qty", "numeric"),
    ColumnSpec("Сумма остатков на складах, ₽", "stock_amount_rub", "numeric"),
    ColumnSpec("Среднее время доставки", "avg_delivery_time", "numeric"),
    ColumnSpec("Среднее время доставки (предыдущий период)", "avg_delivery_time_prev", "numeric"),
    ColumnSpec("Локальные заказы, %", "local_orders_pct", "numeric"),
    ColumnSpec("Локальные заказы, % (предыдущий период)", "local_orders_pct_prev", "numeric"),
]

DB_COLUMNS = [column.db_name for column in COLUMNS]
EXPECTED_HEADERS = [column.source_name for column in COLUMNS]
OPTIONAL_DB_COLUMNS = {
    "wb_club_ordered_units",
    "wb_club_ordered_units_prev",
    "wb_club_bought_units",
    "wb_club_bought_units_prev",
    "wb_club_cancelled_units",
    "wb_club_cancelled_units_prev",
    "wb_club_ordered_amount_rub",
    "wb_club_ordered_amount_rub_prev",
    "wb_club_bought_amount_rub",
    "wb_club_bought_amount_rub_prev",
    "wb_club_cancelled_amount_rub",
    "wb_club_cancelled_amount_rub_prev",
    "wb_club_buyout_pct",
    "wb_club_buyout_pct_prev",
    "wb_club_avg_daily_orders_qty",
    "wb_club_avg_daily_orders_qty_prev",
}
HEADER_ALIASES = {
    "Заказали, шт": ["Заказали товаров, шт"],
    "Заказали, шт (предыдущий период)": ["Заказали товаров, шт (предыдущий период)"],
}


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "–", "-"}:
        return None
    return text


def normalize_header(value: Any) -> str:
    text = clean_text(value)
    if text is None:
        return ""
    text = text.replace("\xa0", " ")
    text = text.replace("₽", " руб ")
    text = text.replace("руб.", " руб ")
    text = text.replace("ё", "е").lower()
    text = re.sub(r"[«»\"'“”]", "", text)
    text = re.sub(r"[^0-9a-zа-я%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def header_lookup_keys(source_name: str) -> list[str]:
    names = [source_name, *HEADER_ALIASES.get(source_name, [])]
    return [key for key in (normalize_header(name) for name in names) if key]


def find_header_index(header_map: dict[str, int], column: ColumnSpec) -> int | None:
    for key in header_lookup_keys(column.source_name):
        if key in header_map:
            return header_map[key]
    return None


def to_numeric(value: Any) -> float | None:
    text = clean_text(value)
    if text is None or text == "Без рейтинга":
        return None
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            strings: list[str] = []
            for _event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag == XML_NS + "si":
                    strings.append("".join(t.text or "" for t in elem.iter(XML_NS + "t")))
                    elem.clear()
            return strings
    except KeyError:
        return []


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


def row_values(row: ET.Element, shared_strings: list[str], max_col: int = MAX_SOURCE_COLS) -> list[str | None]:
    values: list[str | None] = [None] * max_col
    for cell in row.findall(XML_NS + "c"):
        col_idx = column_index(cell.attrib.get("r", ""))
        if 0 <= col_idx < max_col:
            values[col_idx] = cell_text(cell, shared_strings)
    return values


def first_xlsx_member(zip_path: Path) -> tuple[str, bytes]:
    with zipfile.ZipFile(zip_path, "r") as outer:
        xlsx_members = [name for name in outer.namelist() if name.lower().endswith(".xlsx")]
        if not xlsx_members:
            raise ValueError(f"No XLSX inside {zip_path}")
        member = xlsx_members[0]
        return member, outer.read(member)


def worksheet_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )


def find_products_sheet(zf: zipfile.ZipFile, shared_strings: list[str]) -> str:
    for sheet_name in worksheet_names(zf):
        with zf.open(sheet_name) as fh:
            for _event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != XML_NS + "row":
                    continue
                values = row_values(elem, shared_strings, max_col=3)
                elem.clear()
                if values and values[0] == "Детальный отчет воронки продаж по карточкам товаров":
                    return sheet_name
                break
    raise ValueError("Products worksheet not found")


def report_date_from_name(path: Path) -> str:
    match = re.search(r"с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})", path.name)
    if not match:
        raise ValueError(f"Cannot parse date from {path.name}")
    if match.group(1) != match.group(2):
        raise ValueError(f"Expected one-day report, got {path.name}")
    return datetime.strptime(match.group(1), "%d.%m.%Y").date().isoformat()


def parse_report(zip_path: Path) -> tuple[str, str, list[list[Any]]]:
    report_date = report_date_from_name(zip_path)
    inner_name, xlsx_bytes = first_xlsx_member(zip_path)
    rows: list[list[Any]] = []

    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        shared_strings = read_shared_strings(zf)
        sheet_name = find_products_sheet(zf, shared_strings)
        header_map: dict[str, int] | None = None
        with zf.open(sheet_name) as fh:
            for _event, elem in ET.iterparse(fh, events=("end",)):
                if elem.tag != XML_NS + "row":
                    continue
                row_num = int(elem.attrib.get("r", "0"))
                values = row_values(elem, shared_strings)
                elem.clear()

                if row_num == HEADER_ROW:
                    header_map = {}
                    for idx, value in enumerate(values):
                        key = normalize_header(value)
                        if key and key not in header_map:
                            header_map[key] = idx
                    missing = [
                        column.source_name
                        for column in COLUMNS
                        if column.db_name not in OPTIONAL_DB_COLUMNS
                        and find_header_index(header_map, column) is None
                    ]
                    if missing:
                        raise ValueError(f"{zip_path}: missing headers: {missing[:8]}")
                    continue
                if row_num < DATA_START_ROW or header_map is None:
                    continue

                wb_nmid = clean_text(values[header_map[normalize_header("Артикул WB")]])
                seller_article = clean_text(values[header_map[normalize_header("Артикул продавца")]])
                product_name = clean_text(values[header_map[normalize_header("Название")]])
                if not wb_nmid and not seller_article and not product_name:
                    continue

                out: list[Any] = [report_date]
                for column in COLUMNS:
                    header_index = find_header_index(header_map, column)
                    value = values[header_index] if header_index is not None else None
                    out.append(clean_text(value) if column.kind == "text" else to_numeric(value))
                out.extend([str(zip_path), inner_name, row_num])
                rows.append(out)
    return report_date, inner_name, rows


class Importer:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.cur = conn.cursor()
        self.database_name = connection_database_name(conn)

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.cur.execute(query, params)

    def relation_exists(self, relation_name: str) -> bool:
        self.execute("SELECT to_regclass(%s) AS relation_name", (f"public.{relation_name}",))
        row = self.cur.fetchone()
        if not row:
            return False
        value = row["relation_name"] if isinstance(row, dict) else row[0]
        return bool(value)

    def drop_dependent_wb_adv_view(self) -> None:
        if self.relation_exists("mv_wb_adv_daily_by_article_category"):
            self.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_wb_adv_daily_by_article_category")

    def rebuild_dependent_wb_adv_view(self) -> None:
        if not self.relation_exists("wb_adv_daily_raw"):
            return
        if not self.relation_exists("mv_wb_funnel_daily_by_article_category"):
            return

        adv_importer = WbAdvImporter(DEFAULT_WB_ADV_SOURCE_DIR)
        adv_importer.conn = self.conn
        adv_importer.cur = self.cur
        adv_importer.rebuild_view()

    def ensure_schema(self) -> None:
        numeric_columns = "\n".join(f"                {column.db_name} numeric," for column in COLUMNS if column.kind == "numeric")
        text_columns = "\n".join(f"                {column.db_name} text," for column in COLUMNS if column.kind == "text")
        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS public.wb_funnel_daily (
                report_date date NOT NULL,
{text_columns}
{numeric_columns}
                source_file text NOT NULL,
                source_inner_file text,
                source_row_num integer,
                imported_at timestamp without time zone DEFAULT now()
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.wb_funnel_import_files (
                source_file text PRIMARY KEY,
                report_date date,
                rows_imported integer NOT NULL DEFAULT 0,
                file_size_bytes bigint,
                file_mtime timestamp without time zone,
                imported_at timestamp without time zone DEFAULT now(),
                status text NOT NULL DEFAULT 'ok',
                error text
            )
            """
        )
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_report_date ON public.wb_funnel_daily(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_nmid ON public.wb_funnel_daily(wb_nmid)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_seller_article ON public.wb_funnel_daily(seller_article)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_category ON public.wb_funnel_daily(category_name)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_date_nmid ON public.wb_funnel_daily(report_date, wb_nmid)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_daily_source_file ON public.wb_funnel_daily(source_file)",
            "CREATE INDEX IF NOT EXISTS idx_wb_funnel_import_files_date ON public.wb_funnel_import_files(report_date)",
        ]
        for index_sql in indexes:
            self.execute(index_sql)

    def imported_file_state(self) -> dict[str, tuple[int | None, datetime | None, str | None]]:
        self.execute(
            """
            SELECT source_file, file_size_bytes, file_mtime, status
            FROM public.wb_funnel_import_files
            """
        )
        return {
            row["source_file"]: (row["file_size_bytes"], row["file_mtime"], row["status"])
            for row in self.cur.fetchall()
        }

    def imported_report_dates(self) -> set[str]:
        self.execute("SELECT DISTINCT report_date FROM public.wb_funnel_daily")
        return {row["report_date"].isoformat() for row in self.cur.fetchall() if row["report_date"]}

    def seed_import_file_states(self, files: list[Path]) -> None:
        states = self.imported_file_state()
        for file_path in files:
            source_file = str(file_path)
            if source_file in states:
                continue
            stat = file_path.stat()
            self.execute(
                """
                SELECT min(report_date) AS report_date, count(*) AS rows_imported
                FROM public.wb_funnel_daily
                WHERE source_file = %s
                """,
                (source_file,),
            )
            row = self.cur.fetchone()
            if not row or not row["rows_imported"]:
                continue
            self.execute(
                """
                INSERT INTO public.wb_funnel_import_files (
                    source_file, report_date, rows_imported, file_size_bytes,
                    file_mtime, imported_at, status, error
                )
                VALUES (%s, %s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO NOTHING
                """,
                (
                    source_file,
                    row["report_date"],
                    row["rows_imported"],
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime),
                ),
            )

    def should_skip_file(
        self,
        file_path: Path,
        imported: dict[str, tuple[int | None, datetime | None, str | None]],
        imported_dates: set[str],
        *,
        force_reimport: bool,
    ) -> bool:
        if force_reimport:
            return False
        report_date = report_date_from_name(file_path)
        state = imported.get(str(file_path))
        if not state:
            return False
        file_size, file_mtime, status = state
        stat = file_path.stat()
        registry_is_current = status == "ok" and file_size == stat.st_size and file_mtime and abs(file_mtime.timestamp() - stat.st_mtime) < 2
        if report_date in imported_dates and registry_is_current:
            return True
        return False

    def import_file(self, zip_path: Path) -> int:
        stat = zip_path.stat()
        report_date, _inner_name, rows = parse_report(zip_path)
        self.execute("DELETE FROM public.wb_funnel_daily WHERE report_date = %s", (report_date,))
        if rows:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows(rows)
            buffer.seek(0)
            columns = ["report_date", *DB_COLUMNS, "source_file", "source_inner_file", "source_row_num"]
            self.cur.copy_expert(
                f"COPY public.wb_funnel_daily ({', '.join(columns)}) FROM STDIN WITH CSV",
                buffer,
            )
        self.execute(
            """
            INSERT INTO public.wb_funnel_import_files (
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
                str(zip_path),
                report_date,
                len(rows),
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime),
            ),
        )
        return len(rows)

    def rebuild_materialized_views(self) -> None:
        wb_funnel_category_expr = wb_dashboard_category_sql("f.category_name", self.database_name)
        category_source = (
            "укрупненный маппинг предметов WB"
            if self.database_name.strip().casefold() == "sportmaster"
            else 'поле WB "Предмет" без укрупнения'
        )
        rebuild_started = time.monotonic()
        total_stages = 5

        def print_stage(stage: int, label: str) -> None:
            elapsed = time.monotonic() - rebuild_started
            completed = max(0, stage - 1)
            eta = elapsed / completed * (total_stages - completed) if completed else 0
            print(
                f"ПРОГРЕСС: {stage}/{total_stages} ({completed / total_stages * 100:.0f}%) | "
                f"{label} | источник категории: {category_source} | "
                f"прошло {format_duration(elapsed)} | ETA {format_duration(eta)}",
                flush=True,
            )

        print(f"ПЛАН: пересборка WB funnel/ABC витрин | этапов {total_stages} | БД {self.database_name}", flush=True)
        print_stage(1, "удаление зависимых витрин")
        self.drop_dependent_wb_adv_view()
        for view_name in (
            "mv_wb_funnel_daily_summary_rollup",
            "mv_wb_funnel_daily_product_rollup",
        ):
            self.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")

        for view_name in (
            "mv_wb_abc_product_orders_base",
            "mv_wb_abc_product_stock_base",
            "mv_wb_funnel_product_options",
            "mv_wb_funnel_filter_options",
            "mv_wb_funnel_daily_by_article_category",
        ):
            self.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")

        print_stage(2, "дневная витрина по SKU и предмету WB")
        self.execute(
            f"""
            CREATE MATERIALIZED VIEW public.mv_wb_funnel_daily_by_article_category AS
            SELECT
                f.report_date,
                f.wb_nmid::text AS sku,
                max(m.barcode) AS barcode,
                f.seller_article,
                f.seller_article AS product_artikul,
                max(f.product_name) AS product_name,
                NULL::text AS category_level_1,
                NULL::text AS category_level_2,
                max(f.category_name) AS category_level_3,
                max({wb_funnel_category_expr}) AS category_name,
                max(f.category_name) AS subcategory_name,
                max(f.brand) AS brand,
                NULL::text AS model,
                NULL::text AS work_schema,
                NULL::text AS abc_orders_amount,
                NULL::text AS abc_orders_qty,
                coalesce(sum(f.ordered_amount_rub), 0)::numeric AS ordered_amount_rub,
                NULL::numeric AS ordered_amount_dynamic,
                NULL::numeric AS search_catalog_position,
                NULL::numeric AS search_catalog_position_dynamic,
                coalesce(sum(f.impressions_total), 0)::numeric AS impressions_total,
                NULL::numeric AS impressions_total_dynamic,
                coalesce(sum(f.impressions_total), 0)::numeric AS impressions_search_catalog,
                NULL::numeric AS impressions_search_catalog_dynamic,
                coalesce(sum(f.card_visits), 0)::numeric AS card_visits,
                NULL::numeric AS card_visits_dynamic,
                coalesce(sum(f.cart_adds), 0)::numeric AS cart_adds,
                NULL::numeric AS cart_adds_dynamic,
                coalesce(sum(f.ordered_units), 0)::numeric AS ordered_units,
                NULL::numeric AS ordered_units_dynamic,
                coalesce(sum(f.bought_units), 0)::numeric AS bought_units,
                coalesce(sum(f.bought_amount_rub), 0)::numeric AS bought_amount_rub,
                coalesce(sum(f.favorites_adds), 0)::numeric AS favorites_adds,
                coalesce(sum(f.cancelled_units), 0)::numeric AS cancelled_units,
                coalesce(sum(f.cancelled_amount_rub), 0)::numeric AS cancelled_amount_rub,
                coalesce(sum(f.wb_club_ordered_units), 0)::numeric AS wb_club_ordered_units,
                coalesce(sum(f.wb_club_bought_units), 0)::numeric AS wb_club_bought_units,
                coalesce(sum(f.wb_club_cancelled_units), 0)::numeric AS wb_club_cancelled_units,
                coalesce(sum(f.wb_club_ordered_amount_rub), 0)::numeric AS wb_club_ordered_amount_rub,
                coalesce(sum(f.wb_club_bought_amount_rub), 0)::numeric AS wb_club_bought_amount_rub,
                coalesce(sum(f.wb_club_cancelled_amount_rub), 0)::numeric AS wb_club_cancelled_amount_rub,
                CASE WHEN coalesce(sum(f.impressions_total), 0) <> 0
                    THEN round(coalesce(sum(f.card_visits), 0)::numeric / coalesce(sum(f.impressions_total), 0)::numeric * 100, 2)
                    ELSE 0 END AS search_to_card_visit_pct,
                CASE WHEN coalesce(sum(f.impressions_total), 0) <> 0
                    THEN round(coalesce(sum(f.card_visits), 0)::numeric / coalesce(sum(f.impressions_total), 0)::numeric * 100, 2)
                    ELSE 0 END AS total_impression_to_card_visit_pct,
                CASE WHEN coalesce(sum(f.card_visits), 0) <> 0
                    THEN round(coalesce(sum(f.cart_adds), 0)::numeric / coalesce(sum(f.card_visits), 0)::numeric * 100, 2)
                    ELSE 0 END AS card_visit_to_cart_pct,
                CASE WHEN coalesce(sum(f.cart_adds), 0) <> 0
                    THEN round(coalesce(sum(f.ordered_units), 0)::numeric / coalesce(sum(f.cart_adds), 0)::numeric * 100, 2)
                    ELSE 0 END AS cart_to_order_pct,
                CASE WHEN coalesce(sum(f.card_visits), 0) <> 0
                    THEN round(coalesce(sum(f.ordered_units), 0)::numeric / coalesce(sum(f.card_visits), 0)::numeric * 100, 2)
                    ELSE 0 END AS card_visit_to_order_pct,
                CASE WHEN coalesce(sum(f.ordered_units), 0) <> 0
                    THEN round(coalesce(sum(f.ordered_amount_rub), 0)::numeric / coalesce(sum(f.ordered_units), 0)::numeric, 2)
                    ELSE 0 END AS ordered_amount_per_unit_rub
            FROM public.wb_funnel_daily f
            LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m ON m.wb_nmid = f.wb_nmid
            WHERE f.wb_nmid IS NOT NULL
            GROUP BY f.report_date, f.wb_nmid::text, f.seller_article
            """
        )
        print_stage(3, "фильтры и список товаров")
        self.execute(
            # Category options must follow the same client-specific grain.
            """
            CREATE MATERIALIZED VIEW public.mv_wb_funnel_filter_options AS
            SELECT
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                array_remove(array_agg(DISTINCT category_name ORDER BY category_name), NULL) AS category_names,
                count(DISTINCT category_name) AS categories
            FROM public.mv_wb_funnel_daily_by_article_category
            """
        )
        self.execute(
            # Product options expose both the dashboard category and WB subject.
            """
            CREATE MATERIALIZED VIEW public.mv_wb_funnel_product_options AS
            SELECT DISTINCT
                category_name,
                subcategory_name,
                product_name,
                product_artikul,
                seller_article,
                sku,
                barcode
            FROM public.mv_wb_funnel_daily_by_article_category
            WHERE product_name IS NOT NULL
            """
        )
        self.execute(
            # Kept for compatibility with older databases where the view may survive.
            """
            DROP MATERIALIZED VIEW IF EXISTS public.mv_wb_abc_product_orders_base CASCADE
            """
        )
        print_stage(4, "базовые витрины ABC заказов и остатков")
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_abc_product_orders_base AS
            SELECT
                v.report_date,
                v.sku::text AS artikul_wb,
                max(coalesce(v.product_name, v.sku::text)) AS naimenovanie,
                max(coalesce(v.category_name, 'Без категории')) AS category_name,
                max(coalesce(v.subcategory_name, v.category_name, 'Без категории')) AS subcategory_name,
                coalesce(sum(v.ordered_units), 0)::numeric AS zakazano_sht,
                coalesce(sum(v.ordered_amount_rub), 0)::numeric AS zakazano_rub,
                max(f.card_rating) AS reyting_kartochki,
                max(f.review_rating) AS reyting_po_otzyvam,
                max(m.wb_article) AS gj_wb_article,
                max(m.ozon_sku) AS gj_ozon_sku,
                max(m.gj_model) AS gj_model,
                max(m.assortment_bia) AS assortment_bia,
                max(m.tg) AS tg,
                max(m.tg_plus) AS tg_plus,
                max(m.cg) AS cg,
                max(m.season) AS season
            FROM public.mv_wb_funnel_daily_by_article_category v
            LEFT JOIN public.wb_funnel_daily f
                ON f.report_date = v.report_date
                AND f.wb_nmid = v.sku
                AND coalesce(f.seller_article, '') = coalesce(v.seller_article, '')
            LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m ON m.wb_nmid = v.sku
            WHERE v.sku IS NOT NULL
            GROUP BY v.report_date, v.sku::text
            """
        )
        self.execute(
            f"""
            CREATE MATERIALIZED VIEW public.mv_wb_abc_product_stock_base AS
            WITH latest AS (
                SELECT
                    f.*,
                    rank() OVER (PARTITION BY f.wb_nmid ORDER BY f.report_date DESC) AS date_rank
                FROM public.wb_funnel_daily f
                WHERE f.wb_nmid IS NOT NULL
            )
            SELECT
                f.wb_nmid::text AS artikul_wb,
                max(coalesce(f.product_name, f.wb_nmid::text)) AS naimenovanie,
                max(coalesce({wb_funnel_category_expr}, 'Без категории')) AS category_name,
                max(coalesce(f.category_name, {wb_funnel_category_expr}, 'Без категории')) AS subcategory_name,
                coalesce(sum(coalesce(f.stock_wb_qty, 0) + coalesce(f.stock_own_qty, 0)), 0)::numeric AS total_stock_qty,
                max(f.card_rating) AS reyting_kartochki,
                max(f.review_rating) AS reyting_po_otzyvam,
                max(m.wb_article) AS gj_wb_article,
                max(m.ozon_sku) AS gj_ozon_sku,
                max(m.gj_model) AS gj_model,
                max(m.assortment_bia) AS assortment_bia,
                max(m.tg) AS tg,
                max(m.tg_plus) AS tg_plus,
                max(m.cg) AS cg,
                max(m.season) AS season
            FROM latest f
            LEFT JOIN public.mv_sku_mapping_gj_wb_nmid m ON m.wb_nmid = f.wb_nmid
            WHERE f.date_rank = 1
            GROUP BY f.wb_nmid::text
            """
        )
        print_stage(5, "индексы, статистика и зависимая рекламная витрина")
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_date ON public.mv_wb_funnel_daily_by_article_category(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_sku ON public.mv_wb_funnel_daily_by_article_category(sku)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_article ON public.mv_wb_funnel_daily_by_article_category(product_artikul)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_category ON public.mv_wb_funnel_daily_by_article_category(category_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_product_options_category ON public.mv_wb_funnel_product_options(category_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_funnel_product_options_name ON public.mv_wb_funnel_product_options(product_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_orders_date ON public.mv_wb_abc_product_orders_base(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_orders_category ON public.mv_wb_abc_product_orders_base(category_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_orders_subcategory ON public.mv_wb_abc_product_orders_base(subcategory_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_orders_sku ON public.mv_wb_abc_product_orders_base(artikul_wb)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_sku ON public.mv_wb_abc_product_stock_base(artikul_wb)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_category ON public.mv_wb_abc_product_stock_base(category_name)",
            "CREATE INDEX IF NOT EXISTS idx_mv_wb_abc_stock_subcategory ON public.mv_wb_abc_product_stock_base(subcategory_name)",
        ]
        for index_sql in indexes:
            self.execute(index_sql)
        for view_name in (
            "wb_funnel_daily",
            "wb_funnel_import_files",
            "mv_wb_funnel_daily_by_article_category",
            "mv_wb_funnel_filter_options",
            "mv_wb_funnel_product_options",
            "mv_wb_abc_product_orders_base",
            "mv_wb_abc_product_stock_base",
        ):
            self.execute(f"ANALYZE public.{view_name}")
        self.rebuild_dependent_wb_adv_view()
        elapsed = time.monotonic() - rebuild_started
        print(
            f"ИТОГ: WB funnel/ABC витрины пересобраны | этапов {total_stages}/{total_stages} | "
            f"ошибок 0 | stopped no | partial no | elapsed {format_duration(elapsed)}",
            flush=True,
        )

    def summary(self) -> dict[str, Any]:
        self.execute(
            """
            SELECT
                count(*) AS raw_rows,
                count(DISTINCT report_date) AS days_count,
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                count(DISTINCT wb_nmid) AS sku_count,
                coalesce(sum(impressions_total), 0) AS impressions_total,
                coalesce(sum(card_visits), 0) AS card_visits,
                coalesce(sum(cart_adds), 0) AS cart_adds,
                coalesce(sum(ordered_units), 0) AS ordered_units,
                coalesce(sum(ordered_amount_rub), 0) AS ordered_amount_rub
            FROM public.wb_funnel_daily
            """
        )
        return dict(self.cur.fetchone())

    def import_registry_summary(self) -> dict[str, Any]:
        self.execute(
            """
            SELECT
                count(*) AS files_count,
                count(DISTINCT report_date) AS days_count,
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                coalesce(sum(rows_imported), 0) AS registered_rows
            FROM public.wb_funnel_import_files
            WHERE status = 'ok'
            """
        )
        return dict(self.cur.fetchone())


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def progress_line(current: int, total: int, item: str, rows_done: int, errors: int, started_at: float) -> str:
    pct = (current / total * 100) if total else 100.0
    elapsed = time.monotonic() - started_at
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Import WB sales funnel ZIP reports")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument(
        "--skip-views",
        action="store_true",
        help="Load source files only; defer materialized-view rebuilds to the dedicated views step.",
    )
    parser.add_argument(
        "--force-reimport",
        action="store_true",
        help="Reimport dates that are already present in wb_funnel_daily.",
    )
    args = parser.parse_args()

    setup_logging()
    started = time.monotonic()
    files = sorted(args.source_dir.rglob("*.zip"))
    if args.limit:
        files = files[: args.limit]
    if not files and not args.skip_import:
        raise SystemExit(f"No ZIP files found in {args.source_dir}")

    print(f"Found {len(files)} WB funnel ZIP files in {args.source_dir}")
    with app.get_conn() as conn:
        importer = Importer(conn)
        importer.ensure_schema()
        imported_rows = 0
        files_to_import_count = 0
        errors = 0
        if not args.skip_import:
            importer.seed_import_file_states(files)
            imported = importer.imported_file_state()
            imported_dates = importer.imported_report_dates()
            original_count = len(files)
            files = [
                file_path
                for file_path in files
                if not importer.should_skip_file(
                    file_path,
                    imported,
                    imported_dates,
                    force_reimport=args.force_reimport,
                )
            ]
            files_to_import_count = len(files)
            print(f"Already imported skipped: {original_count - len(files)}")
            if files:
                print(
                    f"ПЛАН: файлов к импорту {len(files)} | пропущено {original_count - len(files)} | "
                    "витрины пересобираются после успешной загрузки",
                    flush=True,
                )
                import_started_at = time.monotonic()
                for index, file_path in enumerate(files, start=1):
                    item_name = file_path.name
                    print_progress(index - 1, len(files), item_name, imported_rows, errors, import_started_at)
                    try:
                        rows_count = importer.import_file(file_path)
                        imported_rows += rows_count
                        conn.commit()
                        print_progress(index, len(files), item_name, imported_rows, errors, import_started_at)
                        print(flush=True)
                        print(
                            f"[{index}/{len(files)}] {item_name}: imported {rows_count:,} rows, "
                            f"total {imported_rows:,}, errors {errors}",
                            flush=True,
                        )
                    except Exception:
                        errors += 1
                        conn.rollback()
                        logger.exception("Failed to import %s", file_path)
                        print(flush=True)
                        print(
                            f"[{index}/{len(files)}] {item_name}: imported 0 rows, errors {errors}",
                            flush=True,
                        )
                        raise
        print(f"Imported rows: {imported_rows:,} | errors: {errors:,}")
        should_rebuild_views = (not args.skip_views) and (args.skip_import or args.force_reimport or files_to_import_count > 0)
        if should_rebuild_views:
            print("Rebuilding WB funnel materialized views...")
            importer.rebuild_materialized_views()
            conn.commit()
            print(importer.summary())
        elif args.skip_views:
            conn.commit()
            print("Materialized views deferred; run view refresh after imports.")
        else:
            conn.commit()
            registry_summary = importer.import_registry_summary()
            print("No new WB funnel files; materialized views were not rebuilt.")
            print(registry_summary)
    print(f"Done in {format_duration(time.monotonic() - started)}")


if __name__ == "__main__":
    main()
