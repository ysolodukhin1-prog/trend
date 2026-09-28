#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Import WB search-query ZIP/XLSX exports and rebuild dashboard marts."""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from xml.etree import ElementTree as ET

import psycopg2
from psycopg2.extras import RealDictCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


SOURCE_DIRS = {
    "gloria_jeans": Path(
        r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
        r"\Дашборды\Data\Wb\SEO\Jul"
    ),
    "sportmaster": Path(
        r"G:\Общие диски\Kokoc Marketplaces\Clients\Спортмастер\Аналитика"
        r"\Дашборды\WB\SEO"
    ),
}
DEFAULT_CLIENT = "gloria_jeans"
DEFAULT_SOURCE_DIR = SOURCE_DIRS[DEFAULT_CLIENT]
XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADER_ROW = 2
DATA_START_ROW = 3
MAX_SOURCE_COLS = 40
COPY_BATCH_ROWS = 5_000
PROGRESS_ROWS = 25_000
TARGET_SHEET_TITLE = "Детальный отчет по поисковым запросам по карточкам товаров"


@dataclass(frozen=True)
class ColumnSpec:
    source_name: str
    db_name: str
    kind: str
    required: bool = True


COLUMNS = [
    ColumnSpec("Артикул продавца", "seller_article", "text"),
    ColumnSpec("Артикул WB", "wb_sku", "bigint"),
    ColumnSpec("Название", "product_name", "text"),
    ColumnSpec("Предмет", "subject_name", "text"),
    ColumnSpec("Бренд", "brand", "text"),
    ColumnSpec("Рейтинг карточки", "card_rating", "numeric"),
    ColumnSpec("Рейтинг по отзывам", "review_rating", "numeric"),
    ColumnSpec("Поисковый запрос", "search_query", "text"),
    ColumnSpec("Количество запросов", "query_count", "bigint"),
    ColumnSpec("Количество запросов (предыдущий период)", "query_count_prev", "bigint"),
    ColumnSpec("Видимость, %", "visibility_pct", "numeric"),
    ColumnSpec("Видимость, % (предыдущий период)", "visibility_pct_prev", "numeric"),
    ColumnSpec("Средняя позиция", "average_position", "numeric"),
    ColumnSpec("Средняя позиция (предыдущий период)", "average_position_prev", "numeric"),
    ColumnSpec("Медианная позиция", "median_position", "numeric"),
    ColumnSpec("Медианная позиция (предыдущий период)", "median_position_prev", "numeric"),
    ColumnSpec("Переходы в карточку", "card_visits", "bigint"),
    ColumnSpec("Переходы в карточку (предыдущий период)", "card_visits_prev", "bigint"),
    ColumnSpec(
        "Переходы в карточку больше, чем у n% карточек конкурентов, %",
        "card_visits_competitor_percentile",
        "numeric",
        required=False,
    ),
    ColumnSpec("Положили в корзину", "cart_adds", "bigint"),
    ColumnSpec("Положили в корзину (предыдущий период)", "cart_adds_prev", "bigint"),
    ColumnSpec(
        "Положили в корзину больше, чем n% карточек конкурентов, %",
        "cart_adds_competitor_percentile",
        "numeric",
        required=False,
    ),
    ColumnSpec("Конверсия в корзину, %", "card_to_cart_pct", "numeric"),
    ColumnSpec("Конверсия в корзину, % (предыдущий период)", "card_to_cart_pct_prev", "numeric"),
    ColumnSpec(
        "Конверсия в корзину больше, чем у n% карточек конкурентов, %",
        "card_to_cart_competitor_percentile",
        "numeric",
        required=False,
    ),
    ColumnSpec("Заказали, шт", "ordered_units", "bigint"),
    ColumnSpec("Заказали, шт (предыдущий период)", "ordered_units_prev", "bigint"),
    ColumnSpec(
        "Заказали больше, чем n% карточек конкурентов, %",
        "ordered_units_competitor_percentile",
        "numeric",
        required=False,
    ),
    ColumnSpec("Конверсия в заказ, %", "cart_to_order_pct", "numeric"),
    ColumnSpec("Конверсия в заказ, % (предыдущий период)", "cart_to_order_pct_prev", "numeric"),
    ColumnSpec(
        "Конверсия в заказ больше, чем у n% карточек конкурентов, %",
        "cart_to_order_competitor_percentile",
        "numeric",
        required=False,
    ),
    ColumnSpec("Минимальная цена со скидкой (по размерам), ₽", "min_price_rub", "numeric"),
    ColumnSpec("Максимальная цена со скидкой (по размерам), ₽", "max_price_rub", "numeric"),
]

DB_COLUMNS = [column.db_name for column in COLUMNS]
COPY_COLUMNS = [
    "report_date",
    *DB_COLUMNS,
    "source_file",
    "source_inner_file",
    "source_row_num",
]


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\xa0", " ")
    if text.lower() in {"", "nan", "none", "null", "–", "-"}:
        return None
    return text


def normalize_header(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = text.replace("₽", " руб ").replace("руб.", " руб ").replace("ё", "е").lower()
    text = re.sub(r"[«»\"'“”]", "", text)
    text = re.sub(r"[^0-9a-zа-я%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_number(value: Any, kind: str) -> str | None:
    text = clean_text(value)
    if text is None or text.lower() == "без рейтинга":
        return None
    normalized = text.replace(" ", "").replace(",", ".")
    try:
        numeric = float(normalized)
    except ValueError:
        return None
    if kind == "bigint":
        return str(int(numeric))
    return normalized


def report_date_from_name(path: Path) -> str:
    match = re.search(r"с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})", path.name)
    if not match:
        raise ValueError(f"Не удалось определить дату из имени: {path.name}")
    if match.group(1) != match.group(2):
        raise ValueError(f"Ожидалась дневная выгрузка: {path.name}")
    return datetime.strptime(match.group(1), "%d.%m.%Y").date().isoformat()


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        with zf.open("xl/sharedStrings.xml") as handle:
            values: list[str] = []
            for _event, elem in ET.iterparse(handle, events=("end",)):
                if elem.tag == XML_NS + "si":
                    values.append("".join(node.text or "" for node in elem.iter(XML_NS + "t")))
                    elem.clear()
            return values
    except KeyError:
        return []


def cell_text(cell: ET.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text = "".join(node.text or "" for node in cell.iter(XML_NS + "t"))
        return text or None
    value = cell.find(XML_NS + "v")
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    return value.text


def row_values(row: ET.Element, shared_strings: list[str]) -> list[str | None]:
    values: list[str | None] = [None] * MAX_SOURCE_COLS
    for cell in row.findall(XML_NS + "c"):
        index = column_index(cell.attrib.get("r", ""))
        if 0 <= index < MAX_SOURCE_COLS:
            values[index] = cell_text(cell, shared_strings)
    return values


def first_xlsx_member(zip_path: Path) -> tuple[str, bytes]:
    with zipfile.ZipFile(zip_path, "r") as outer:
        members = [name for name in outer.namelist() if name.lower().endswith(".xlsx")]
        if not members:
            raise ValueError(f"В архиве нет XLSX: {zip_path}")
        member = members[0]
        return member, outer.read(member)


def worksheet_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )


def find_detail_sheet(zf: zipfile.ZipFile, shared_strings: list[str]) -> str:
    for sheet_name in worksheet_names(zf):
        with zf.open(sheet_name) as handle:
            for _event, elem in ET.iterparse(handle, events=("end",)):
                if elem.tag != XML_NS + "row":
                    continue
                values = row_values(elem, shared_strings)
                elem.clear()
                if clean_text(values[0]) == TARGET_SHEET_TITLE:
                    return sheet_name
                break
    raise ValueError(f"Лист «{TARGET_SHEET_TITLE}» не найден")


def iter_report_rows(
    zip_path: Path,
    progress: Callable[[int, float], None] | None = None,
) -> tuple[str, str, Iterator[list[Any]]]:
    report_date = report_date_from_name(zip_path)
    inner_name, xlsx_bytes = first_xlsx_member(zip_path)
    workbook_zip = zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r")
    shared_strings = read_shared_strings(workbook_zip)
    sheet_name = find_detail_sheet(workbook_zip, shared_strings)
    sheet_size = max(workbook_zip.getinfo(sheet_name).file_size, 1)

    def generate() -> Iterator[list[Any]]:
        header_map: dict[str, int] | None = None
        yielded = 0
        try:
            with workbook_zip.open(sheet_name) as handle:
                for _event, elem in ET.iterparse(handle, events=("end",)):
                    if elem.tag != XML_NS + "row":
                        continue
                    row_num = int(elem.attrib.get("r", "0"))
                    values = row_values(elem, shared_strings)
                    elem.clear()
                    if row_num == HEADER_ROW:
                        header_map = {
                            normalized: index
                            for index, value in enumerate(values)
                            if (normalized := normalize_header(value))
                        }
                        missing = [
                            column.source_name
                            for column in COLUMNS
                            if column.required
                            and normalize_header(column.source_name) not in header_map
                        ]
                        if missing:
                            raise ValueError(f"{zip_path.name}: отсутствуют поля: {missing}")
                        continue
                    if row_num < DATA_START_ROW or header_map is None:
                        continue
                    output: list[Any] = [report_date]
                    for column in COLUMNS:
                        source_index = header_map.get(normalize_header(column.source_name))
                        raw = values[source_index] if source_index is not None else None
                        output.append(clean_text(raw) if column.kind == "text" else to_number(raw, column.kind))
                    wb_sku = output[1 + DB_COLUMNS.index("wb_sku")]
                    search_query = output[1 + DB_COLUMNS.index("search_query")]
                    if wb_sku is None and search_query is None:
                        continue
                    if wb_sku is None or search_query is None:
                        raise ValueError(f"{zip_path.name}, строка {row_num}: пустой ключ WB/query")
                    output.extend([str(zip_path), inner_name, row_num])
                    yielded += 1
                    if progress and yielded % PROGRESS_ROWS == 0:
                        progress(yielded, min(1.0, handle.tell() / sheet_size))
                    yield output
        finally:
            workbook_zip.close()

    return report_date, inner_name, generate()


class WbSearchQueriesImporter:
    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self.cur = conn.cursor()

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.cur.execute(query, params)

    def ensure_schema(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.wb_search_queries_daily (
                report_date date NOT NULL,
                seller_article text,
                wb_sku bigint NOT NULL,
                product_name text,
                subject_name text,
                brand text,
                card_rating numeric,
                review_rating numeric,
                search_query text NOT NULL,
                query_count bigint,
                query_count_prev bigint,
                visibility_pct numeric,
                visibility_pct_prev numeric,
                average_position numeric,
                average_position_prev numeric,
                median_position numeric,
                median_position_prev numeric,
                card_visits bigint,
                card_visits_prev bigint,
                card_visits_competitor_percentile numeric,
                cart_adds bigint,
                cart_adds_prev bigint,
                cart_adds_competitor_percentile numeric,
                card_to_cart_pct numeric,
                card_to_cart_pct_prev numeric,
                card_to_cart_competitor_percentile numeric,
                ordered_units bigint,
                ordered_units_prev bigint,
                ordered_units_competitor_percentile numeric,
                cart_to_order_pct numeric,
                cart_to_order_pct_prev numeric,
                cart_to_order_competitor_percentile numeric,
                min_price_rub numeric,
                max_price_rub numeric,
                source_file text NOT NULL,
                source_inner_file text,
                source_row_num integer,
                imported_at timestamp without time zone NOT NULL DEFAULT now()
            )
            """
        )
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS public.wb_search_queries_import_files (
                source_file text PRIMARY KEY,
                report_date date,
                rows_imported integer NOT NULL DEFAULT 0,
                file_size_bytes bigint,
                file_mtime timestamp without time zone,
                imported_at timestamp without time zone NOT NULL DEFAULT now(),
                status text NOT NULL DEFAULT 'ok',
                error text
            )
            """
        )
        for statement in (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_wb_search_queries_daily_key ON public.wb_search_queries_daily(report_date, wb_sku, search_query)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_daily_date ON public.wb_search_queries_daily(report_date)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_daily_sku ON public.wb_search_queries_daily(wb_sku)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_daily_query ON public.wb_search_queries_daily(search_query)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_daily_subject ON public.wb_search_queries_daily(subject_name)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_daily_product ON public.wb_search_queries_daily(product_name)",
            "CREATE INDEX IF NOT EXISTS idx_wb_search_queries_import_date ON public.wb_search_queries_import_files(report_date)",
        ):
            self.execute(statement)

    def imported_file_state(self) -> dict[str, tuple[int | None, datetime | None, str | None]]:
        self.execute(
            "SELECT source_file, file_size_bytes, file_mtime, status FROM public.wb_search_queries_import_files"
        )
        return {
            row["source_file"]: (row["file_size_bytes"], row["file_mtime"], row["status"])
            for row in self.cur.fetchall()
        }

    def should_skip(self, path: Path, states: dict[str, tuple[int | None, datetime | None, str | None]]) -> bool:
        state = states.get(str(path))
        if not state:
            return False
        size, mtime, status = state
        stat = path.stat()
        return bool(
            status == "ok"
            and size == stat.st_size
            and mtime
            and abs(mtime.timestamp() - stat.st_mtime) < 2
        )

    def _copy_batch(self, rows: list[list[Any]]) -> None:
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(rows)
        buffer.seek(0)
        self.cur.copy_expert(
            f"COPY wb_search_queries_stage ({', '.join(COPY_COLUMNS)}) FROM STDIN WITH CSV",
            buffer,
        )

    def import_file(
        self,
        zip_path: Path,
        on_progress: Callable[[int, float], None] | None = None,
    ) -> int:
        report_date, _inner_name, rows = iter_report_rows(zip_path, progress=on_progress)
        self.execute("DROP TABLE IF EXISTS wb_search_queries_stage")
        self.execute(
            "CREATE TEMP TABLE wb_search_queries_stage (LIKE public.wb_search_queries_daily INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        batch: list[list[Any]] = []
        row_count = 0
        for row in rows:
            batch.append(row)
            if len(batch) >= COPY_BATCH_ROWS:
                self._copy_batch(batch)
                row_count += len(batch)
                batch.clear()
        if batch:
            self._copy_batch(batch)
            row_count += len(batch)

        self.execute(
            """
            SELECT count(*) - count(DISTINCT (wb_sku, search_query)) AS duplicates
            FROM wb_search_queries_stage
            """
        )
        duplicates = int(self.cur.fetchone()["duplicates"] or 0)
        if duplicates:
            print(
                f"ПРОГРЕСС: дедупликация | {zip_path.name} | "
                f"повторных строк {duplicates} | правило: сохранить последнюю строку файла",
                flush=True,
            )
        imported_count = row_count - duplicates

        self.execute("DELETE FROM public.wb_search_queries_daily WHERE report_date = %s", (report_date,))
        self.execute(
            f"""
            INSERT INTO public.wb_search_queries_daily ({', '.join(COPY_COLUMNS)})
            SELECT {', '.join(COPY_COLUMNS)}
            FROM (
                SELECT s.*, row_number() OVER (
                    PARTITION BY wb_sku, search_query ORDER BY ctid DESC
                ) AS source_row_rank
                FROM wb_search_queries_stage s
            ) deduplicated
            WHERE source_row_rank = 1
            """
        )
        stat = zip_path.stat()
        self.execute(
            """
            INSERT INTO public.wb_search_queries_import_files (
                source_file, report_date, rows_imported, file_size_bytes,
                file_mtime, imported_at, status, error
            )
            VALUES (%s, %s, %s, %s, %s, now(), 'ok', NULL)
            ON CONFLICT (source_file) DO UPDATE SET
                report_date = EXCLUDED.report_date,
                rows_imported = EXCLUDED.rows_imported,
                file_size_bytes = EXCLUDED.file_size_bytes,
                file_mtime = EXCLUDED.file_mtime,
                imported_at = now(), status = 'ok', error = NULL
            """,
            (
                str(zip_path),
                report_date,
                imported_count,
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime),
            ),
        )
        return imported_count

    def register_error(self, zip_path: Path, error: Exception) -> None:
        stat = zip_path.stat()
        report_date = None
        try:
            report_date = report_date_from_name(zip_path)
        except ValueError:
            pass
        self.execute(
            """
            INSERT INTO public.wb_search_queries_import_files (
                source_file, report_date, rows_imported, file_size_bytes,
                file_mtime, imported_at, status, error
            )
            VALUES (%s, %s, 0, %s, %s, now(), 'error', %s)
            ON CONFLICT (source_file) DO UPDATE SET
                report_date = EXCLUDED.report_date,
                rows_imported = 0,
                file_size_bytes = EXCLUDED.file_size_bytes,
                file_mtime = EXCLUDED.file_mtime,
                imported_at = now(), status = 'error', error = EXCLUDED.error
            """,
            (
                str(zip_path),
                report_date,
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime),
                str(error)[:2000],
            ),
        )

    def rebuild_materialized_views(self) -> bool:
        self.execute(
            """
            SELECT to_regclass(
                'public.mv_wb_search_query_classification_summary'
            ) IS NOT NULL AS exists
            """
        )
        classification_summary_existed = bool(self.cur.fetchone()["exists"])
        for view_name in (
            "mv_wb_search_query_classification_summary",
            "mv_wb_search_queries_daily_summary",
            "mv_wb_search_category_query_daily",
            "mv_wb_search_product_daily",
            "mv_wb_search_query_daily",
        ):
            self.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")

        views_total = 4
        views_started_at = time.monotonic()

        def announce_view(index: int, view_name: str) -> float:
            elapsed = time.monotonic() - views_started_at
            print(
                f"ПРОГРЕСС: витрины {index}/{views_total} "
                f"({(index - 1) / views_total * 100:.1f}%) | {view_name} | "
                f"создание начато | прошло {format_duration(elapsed)}",
                flush=True,
            )
            return time.monotonic()

        def complete_view(index: int, view_name: str, step_started_at: float) -> None:
            elapsed = time.monotonic() - views_started_at
            step_elapsed = time.monotonic() - step_started_at
            eta = elapsed / index * (views_total - index)
            print(
                f"ПРОГРЕСС: витрины {index}/{views_total} "
                f"({index / views_total * 100:.1f}%) | {view_name} | "
                f"готово за {format_duration(step_elapsed)} | "
                f"прошло {format_duration(elapsed)} | ETA {format_duration(eta)}",
                flush=True,
            )

        query_view_started_at = announce_view(
            1,
            "mv_wb_search_query_daily",
        )
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_search_query_daily AS
            SELECT
                report_date,
                search_query,
                max(query_count)::bigint AS query_count,
                max(query_count_prev)::bigint AS query_count_prev,
                count(*)::bigint AS product_query_pairs,
                count(DISTINCT wb_sku)::bigint AS sku_count,
                count(*) FILTER (WHERE coalesce(visibility_pct, 0) > 0)::bigint AS visible_sku_count,
                count(*) FILTER (WHERE coalesce(visibility_pct_prev, 0) > 0)::bigint AS visible_sku_count_prev,
                count(*) FILTER (WHERE average_position <= 20)::bigint AS top20_sku_count,
                count(*) FILTER (WHERE average_position_prev <= 20)::bigint AS top20_sku_count_prev,
                coalesce(sum(query_count), 0)::bigint AS visibility_weight,
                coalesce(sum(query_count_prev), 0)::bigint AS visibility_prev_weight,
                round(
                    sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)), 0), 2
                ) AS visibility_pct,
                round(
                    sum(coalesce(visibility_pct_prev, 0) * coalesce(query_count_prev, 0))
                    / nullif(sum(coalesce(query_count_prev, 0)), 0), 2
                ) AS visibility_pct_prev,
                coalesce(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0)::bigint AS average_position_weight,
                coalesce(sum(query_count_prev) FILTER (WHERE average_position_prev IS NOT NULL), 0)::bigint AS average_position_prev_weight,
                round(
                    sum(average_position * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2
                ) AS average_position,
                round(
                    sum(average_position_prev * coalesce(query_count_prev, 0))
                    / nullif(sum(coalesce(query_count_prev, 0)) FILTER (WHERE average_position_prev IS NOT NULL), 0), 2
                ) AS average_position_prev,
                coalesce(sum(query_count) FILTER (WHERE median_position > 0), 0)::bigint AS median_position_weight,
                coalesce(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0)::bigint AS median_position_prev_weight,
                round(
                    sum(median_position * coalesce(query_count, 0)) FILTER (WHERE median_position > 0)
                    / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE median_position > 0), 0), 2
                ) AS median_position,
                round(
                    sum(median_position_prev * coalesce(query_count_prev, 0)) FILTER (WHERE median_position_prev > 0)
                    / nullif(sum(coalesce(query_count_prev, 0)) FILTER (WHERE median_position_prev > 0), 0), 2
                ) AS median_position_prev,
                min(average_position) AS best_position,
                min(average_position_prev) AS best_position_prev,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(card_visits_prev), 0)::bigint AS card_visits_prev,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(cart_adds_prev), 0)::bigint AS cart_adds_prev,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                coalesce(sum(ordered_units_prev), 0)::bigint AS ordered_units_prev,
                coalesce(sum(query_count), 0)::bigint AS competitor_weight,
                round(sum(card_visits_competitor_percentile * coalesce(query_count, 0))
                    / nullif(sum(query_count) FILTER (WHERE card_visits_competitor_percentile IS NOT NULL), 0), 2) AS card_visits_competitor_percentile,
                round(sum(cart_adds_competitor_percentile * coalesce(query_count, 0))
                    / nullif(sum(query_count) FILTER (WHERE cart_adds_competitor_percentile IS NOT NULL), 0), 2) AS cart_adds_competitor_percentile,
                round(sum(card_to_cart_competitor_percentile * coalesce(query_count, 0))
                    / nullif(sum(query_count) FILTER (WHERE card_to_cart_competitor_percentile IS NOT NULL), 0), 2) AS card_to_cart_competitor_percentile,
                round(sum(ordered_units_competitor_percentile * coalesce(query_count, 0))
                    / nullif(sum(query_count) FILTER (WHERE ordered_units_competitor_percentile IS NOT NULL), 0), 2) AS ordered_units_competitor_percentile,
                round(sum(cart_to_order_competitor_percentile * coalesce(query_count, 0))
                    / nullif(sum(query_count) FILTER (WHERE cart_to_order_competitor_percentile IS NOT NULL), 0), 2) AS cart_to_order_competitor_percentile,
                round(coalesce(sum(cart_adds), 0)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(coalesce(sum(cart_adds_prev), 0)::numeric / nullif(sum(card_visits_prev), 0) * 100, 2) AS card_to_cart_pct_prev,
                round(coalesce(sum(ordered_units), 0)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
                round(coalesce(sum(ordered_units_prev), 0)::numeric / nullif(sum(cart_adds_prev), 0) * 100, 2) AS cart_to_order_pct_prev
            FROM public.wb_search_queries_daily
            GROUP BY report_date, search_query
            """
        )
        self.execute(
            "CREATE UNIQUE INDEX ux_mv_wb_search_query_daily ON public.mv_wb_search_query_daily(report_date, search_query)"
        )
        self.execute(
            "CREATE INDEX idx_mv_wb_search_query_daily_query ON public.mv_wb_search_query_daily(search_query)"
        )
        complete_view(
            1,
            "mv_wb_search_query_daily",
            query_view_started_at,
        )
        category_query_view_started_at = announce_view(2, "mv_wb_search_category_query_daily")
        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_search_category_query_daily AS
            SELECT
                report_date,
                coalesce(nullif(subject_name, ''), 'Без категории') AS subject_name,
                search_query,
                max(query_count)::bigint AS query_count,
                count(*)::bigint AS product_query_pairs,
                count(DISTINCT wb_sku)::bigint AS sku_count,
                round(
                    sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)), 0), 2
                ) AS visibility_pct,
                round(
                    sum(average_position * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2
                ) AS average_position,
                min(average_position) AS best_position,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units
            FROM public.wb_search_queries_daily
            GROUP BY report_date, coalesce(nullif(subject_name, ''), 'Без категории'), search_query
            """
        )
        self.execute(
            "CREATE UNIQUE INDEX ux_mv_wb_search_category_query_daily "
            "ON public.mv_wb_search_category_query_daily(report_date, subject_name, search_query)"
        )
        self.execute(
            "CREATE INDEX idx_mv_wb_search_category_query_daily_subject "
            "ON public.mv_wb_search_category_query_daily(subject_name)"
        )
        self.execute(
            "CREATE INDEX idx_mv_wb_search_category_query_daily_query "
            "ON public.mv_wb_search_category_query_daily(search_query)"
        )
        complete_view(
            2,
            "mv_wb_search_category_query_daily",
            category_query_view_started_at,
        )
        product_view_started_at = announce_view(3, "mv_wb_search_product_daily")

        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_search_product_daily AS
            SELECT
                report_date,
                wb_sku,
                max(seller_article) AS seller_article,
                max(product_name) AS product_name,
                max(subject_name) AS subject_name,
                max(brand) AS brand,
                max(card_rating) AS card_rating,
                max(review_rating) AS review_rating,
                count(DISTINCT search_query)::bigint AS query_count,
                coalesce(sum(query_count), 0)::bigint AS search_demand,
                coalesce(sum(query_count_prev), 0)::bigint AS search_demand_prev,
                count(*) FILTER (WHERE coalesce(visibility_pct, 0) > 0)::bigint AS visible_query_count,
                count(*) FILTER (WHERE coalesce(visibility_pct_prev, 0) > 0)::bigint AS visible_query_count_prev,
                count(*) FILTER (WHERE average_position <= 20)::bigint AS top20_query_count,
                count(*) FILTER (WHERE average_position_prev <= 20)::bigint AS top20_query_count_prev,
                coalesce(sum(query_count), 0)::bigint AS visibility_weight,
                coalesce(sum(query_count_prev), 0)::bigint AS visibility_prev_weight,
                round(
                    sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)), 0), 2
                ) AS visibility_pct,
                round(
                    sum(coalesce(visibility_pct_prev, 0) * coalesce(query_count_prev, 0))
                    / nullif(sum(coalesce(query_count_prev, 0)), 0), 2
                ) AS visibility_pct_prev,
                coalesce(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0)::bigint AS average_position_weight,
                coalesce(sum(query_count_prev) FILTER (WHERE average_position_prev IS NOT NULL), 0)::bigint AS average_position_prev_weight,
                round(
                    sum(average_position * coalesce(query_count, 0))
                    / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2
                ) AS average_position,
                round(
                    sum(average_position_prev * coalesce(query_count_prev, 0))
                    / nullif(sum(coalesce(query_count_prev, 0)) FILTER (WHERE average_position_prev IS NOT NULL), 0), 2
                ) AS average_position_prev,
                coalesce(sum(query_count) FILTER (WHERE median_position > 0), 0)::bigint AS median_position_weight,
                coalesce(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0)::bigint AS median_position_prev_weight,
                round(sum(median_position * coalesce(query_count, 0)) FILTER (WHERE median_position > 0)
                    / nullif(sum(query_count) FILTER (WHERE median_position > 0), 0), 2) AS median_position,
                round(sum(median_position_prev * coalesce(query_count_prev, 0)) FILTER (WHERE median_position_prev > 0)
                    / nullif(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0), 2) AS median_position_prev,
                min(average_position) AS best_position,
                min(average_position_prev) AS best_position_prev,
                coalesce(sum(card_visits), 0)::bigint AS card_visits,
                coalesce(sum(card_visits_prev), 0)::bigint AS card_visits_prev,
                coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                coalesce(sum(cart_adds_prev), 0)::bigint AS cart_adds_prev,
                coalesce(sum(ordered_units), 0)::bigint AS ordered_units,
                coalesce(sum(ordered_units_prev), 0)::bigint AS ordered_units_prev,
                coalesce(sum(query_count), 0)::bigint AS competitor_weight,
                round(sum(card_visits_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_visits_competitor_percentile,
                round(sum(cart_adds_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_adds_competitor_percentile,
                round(sum(card_to_cart_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_to_cart_competitor_percentile,
                round(sum(ordered_units_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS ordered_units_competitor_percentile,
                round(sum(cart_to_order_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_to_order_competitor_percentile,
                round(coalesce(sum(cart_adds), 0)::numeric / nullif(sum(card_visits), 0) * 100, 2) AS card_to_cart_pct,
                round(coalesce(sum(cart_adds_prev), 0)::numeric / nullif(sum(card_visits_prev), 0) * 100, 2) AS card_to_cart_pct_prev,
                round(coalesce(sum(ordered_units), 0)::numeric / nullif(sum(cart_adds), 0) * 100, 2) AS cart_to_order_pct,
                round(coalesce(sum(ordered_units_prev), 0)::numeric / nullif(sum(cart_adds_prev), 0) * 100, 2) AS cart_to_order_pct_prev,
                min(nullif(min_price_rub, 0)) AS min_price_rub,
                max(nullif(max_price_rub, 0)) AS max_price_rub
            FROM public.wb_search_queries_daily
            GROUP BY report_date, wb_sku
            """
        )
        self.execute(
            "CREATE UNIQUE INDEX ux_mv_wb_search_product_daily ON public.mv_wb_search_product_daily(report_date, wb_sku)"
        )
        self.execute(
            "CREATE INDEX idx_mv_wb_search_product_daily_subject ON public.mv_wb_search_product_daily(subject_name)"
        )
        complete_view(
            3,
            "mv_wb_search_product_daily",
            product_view_started_at,
        )
        summary_view_started_at = announce_view(4, "mv_wb_search_queries_daily_summary")

        self.execute(
            """
            CREATE MATERIALIZED VIEW public.mv_wb_search_queries_daily_summary AS
            WITH pair_stats AS (
                SELECT
                    report_date,
                    count(*)::bigint AS product_query_pairs,
                    count(DISTINCT wb_sku)::bigint AS sku_count,
                    count(DISTINCT search_query)::bigint AS search_query_count,
                    count(*) FILTER (WHERE coalesce(visibility_pct, 0) > 0)::bigint AS visible_pairs,
                    count(*) FILTER (WHERE coalesce(visibility_pct_prev, 0) > 0)::bigint AS visible_pairs_prev,
                    count(*) FILTER (WHERE average_position <= 20)::bigint AS top20_pairs,
                    count(*) FILTER (WHERE average_position_prev <= 20)::bigint AS top20_pairs_prev,
                    coalesce(sum(query_count), 0)::bigint AS visibility_weight,
                    coalesce(sum(query_count_prev), 0)::bigint AS visibility_prev_weight,
                    round(
                        sum(coalesce(visibility_pct, 0) * coalesce(query_count, 0))
                        / nullif(sum(coalesce(query_count, 0)), 0), 2
                    ) AS visibility_pct,
                    round(
                        sum(coalesce(visibility_pct_prev, 0) * coalesce(query_count_prev, 0))
                        / nullif(sum(coalesce(query_count_prev, 0)), 0), 2
                    ) AS visibility_pct_prev,
                    coalesce(sum(query_count) FILTER (WHERE average_position IS NOT NULL), 0)::bigint AS average_position_weight,
                    coalesce(sum(query_count_prev) FILTER (WHERE average_position_prev IS NOT NULL), 0)::bigint AS average_position_prev_weight,
                    round(
                        sum(average_position * coalesce(query_count, 0))
                        / nullif(sum(coalesce(query_count, 0)) FILTER (WHERE average_position IS NOT NULL), 0), 2
                    ) AS average_position,
                    round(
                        sum(average_position_prev * coalesce(query_count_prev, 0))
                        / nullif(sum(coalesce(query_count_prev, 0)) FILTER (WHERE average_position_prev IS NOT NULL), 0), 2
                    ) AS average_position_prev,
                    coalesce(sum(query_count) FILTER (WHERE median_position > 0), 0)::bigint AS median_position_weight,
                    coalesce(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0)::bigint AS median_position_prev_weight,
                    round(sum(median_position * coalesce(query_count, 0)) FILTER (WHERE median_position > 0)
                        / nullif(sum(query_count) FILTER (WHERE median_position > 0), 0), 2) AS median_position,
                    round(sum(median_position_prev * coalesce(query_count_prev, 0)) FILTER (WHERE median_position_prev > 0)
                        / nullif(sum(query_count_prev) FILTER (WHERE median_position_prev > 0), 0), 2) AS median_position_prev,
                    min(average_position) AS best_position,
                    coalesce(sum(card_visits), 0)::bigint AS card_visits,
                    coalesce(sum(card_visits_prev), 0)::bigint AS card_visits_prev,
                    coalesce(sum(cart_adds), 0)::bigint AS cart_adds,
                    coalesce(sum(cart_adds_prev), 0)::bigint AS cart_adds_prev,
                    coalesce(sum(ordered_units), 0)::bigint AS ordered_units
                    ,coalesce(sum(ordered_units_prev), 0)::bigint AS ordered_units_prev,
                    coalesce(sum(query_count), 0)::bigint AS competitor_weight,
                    round(sum(card_visits_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_visits_competitor_percentile,
                    round(sum(cart_adds_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_adds_competitor_percentile,
                    round(sum(card_to_cart_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS card_to_cart_competitor_percentile,
                    round(sum(ordered_units_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS ordered_units_competitor_percentile,
                    round(sum(cart_to_order_competitor_percentile * coalesce(query_count, 0)) / nullif(sum(query_count), 0), 2) AS cart_to_order_competitor_percentile
                FROM public.wb_search_queries_daily
                GROUP BY report_date
            ), demand AS (
                SELECT report_date,
                    coalesce(sum(query_count), 0)::bigint AS search_demand,
                    coalesce(sum(query_count_prev), 0)::bigint AS search_demand_prev
                FROM public.mv_wb_search_query_daily
                GROUP BY report_date
            ), product_stats AS (
                SELECT report_date,
                    round(avg(card_rating), 2) AS card_rating,
                    round(avg(review_rating), 2) AS review_rating,
                    min(min_price_rub) AS min_price_rub,
                    max(max_price_rub) AS max_price_rub
                FROM public.mv_wb_search_product_daily
                GROUP BY report_date
            )
            SELECT
                p.*,
                d.search_demand,
                d.search_demand_prev,
                s.card_rating,
                s.review_rating,
                s.min_price_rub,
                s.max_price_rub,
                round(p.visible_pairs::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS visible_pairs_pct,
                round(p.visible_pairs_prev::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS visible_pairs_pct_prev,
                round(p.top20_pairs::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS top20_pairs_pct,
                round(p.top20_pairs_prev::numeric / nullif(p.product_query_pairs, 0) * 100, 2) AS top20_pairs_pct_prev,
                round(p.cart_adds::numeric / nullif(p.card_visits, 0) * 100, 2) AS card_to_cart_pct,
                round(p.cart_adds_prev::numeric / nullif(p.card_visits_prev, 0) * 100, 2) AS card_to_cart_pct_prev,
                round(p.ordered_units::numeric / nullif(p.cart_adds, 0) * 100, 2) AS cart_to_order_pct,
                round(p.ordered_units_prev::numeric / nullif(p.cart_adds_prev, 0) * 100, 2) AS cart_to_order_pct_prev
            FROM pair_stats p
            JOIN demand d USING (report_date)
            JOIN product_stats s USING (report_date)
            """
        )
        self.execute(
            "CREATE UNIQUE INDEX ux_mv_wb_search_queries_daily_summary ON public.mv_wb_search_queries_daily_summary(report_date)"
        )
        complete_view(
            4,
            "mv_wb_search_queries_daily_summary",
            summary_view_started_at,
        )
        self.execute("ANALYZE public.wb_search_queries_daily")
        self.execute("ANALYZE public.mv_wb_search_query_daily")
        self.execute("ANALYZE public.mv_wb_search_category_query_daily")
        self.execute("ANALYZE public.mv_wb_search_product_daily")
        self.execute("ANALYZE public.mv_wb_search_queries_daily_summary")
        return classification_summary_existed

    def summary(self) -> dict[str, Any]:
        self.execute(
            """
            SELECT
                count(*) AS raw_rows,
                count(DISTINCT report_date) AS days_count,
                min(report_date) AS date_from,
                max(report_date) AS date_to,
                count(DISTINCT wb_sku) AS sku_count,
                count(DISTINCT search_query) AS search_query_count,
                count(*) - count(DISTINCT (report_date, wb_sku, search_query)) AS duplicate_keys,
                count(*) FILTER (WHERE average_position IS NULL) AS missing_average_position,
                count(*) FILTER (WHERE coalesce(visibility_pct, 0) = 0) AS zero_visibility_rows,
                coalesce(sum(card_visits), 0) AS card_visits,
                coalesce(sum(cart_adds), 0) AS cart_adds,
                coalesce(sum(ordered_units), 0) AS ordered_units
            FROM public.wb_search_queries_daily
            """
        )
        return dict(self.cur.fetchone())


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}ч {minutes:02d}м {secs:02d}с"
    if minutes:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт поисковых запросов WB")
    parser.add_argument("--client", choices=sorted(SOURCE_DIRS), default=DEFAULT_CLIENT)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--force-reimport", action="store_true")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-views", action="store_true")
    parser.add_argument("--skip-classification", action="store_true")
    args = parser.parse_args()

    started_at = time.monotonic()
    source_dir = args.source_dir or SOURCE_DIRS[args.client]
    # Search recursively because marketplace exports are split into monthly
    # folders (Jul/Aug/...) and the importer must advance across month bounds.
    files = sorted(source_dir.rglob("*.zip")) if not args.skip_import else []
    print(
        "ПЛАН: "
        f"клиент {args.client} | файлов {len(files)} | пакет COPY {COPY_BATCH_ROWS:,} строк | "
        f"импорт {'нет' if args.skip_import else 'да'} | "
        f"матвьюхи {'нет' if args.skip_views else 'да'} | "
        f"классификация {'нет' if args.skip_classification else 'да'} | "
        "по одному дню в транзакции; лимитов API и пауз нет",
        flush=True,
    )
    if not args.skip_import and not files:
        raise SystemExit(f"ZIP-файлы не найдены: {source_dir}")

    conn = psycopg2.connect(**app.read_db_config(args.client), cursor_factory=RealDictCursor)
    importer = WbSearchQueriesImporter(conn)
    imported_files = 0
    skipped_files = 0
    imported_rows = 0
    errors = 0
    classification_summary_existed = False
    try:
        importer.ensure_schema()
        conn.commit()
        states = importer.imported_file_state()
        total = len(files)
        for index, zip_path in enumerate(files, start=1):
            if not args.force_reimport and importer.should_skip(zip_path, states):
                skipped_files += 1
                print(f"[{index}/{total}] {zip_path.name}: пропуск — файл уже импортирован", flush=True)
                continue

            def progress(file_rows: int, xml_pct: float) -> None:
                elapsed = time.monotonic() - started_at
                completed_share = ((index - 1) + xml_pct) / max(total, 1)
                eta = elapsed / completed_share * (1 - completed_share) if completed_share > 0 else 0
                print(
                    f"ПРОГРЕСС: {index}/{total} ({completed_share * 100:.1f}%) | "
                    f"{zip_path.name} | строки файла {file_rows:,} | всего {imported_rows + file_rows:,} | "
                    f"XML {xml_pct * 100:.1f}% | ошибки {errors} | "
                    f"прошло {format_duration(elapsed)} | ETA {format_duration(eta)}",
                    flush=True,
                )

            try:
                rows = importer.import_file(zip_path, on_progress=progress)
                conn.commit()
                imported_files += 1
                imported_rows += rows
                print(
                    f"[{index}/{total}] {zip_path.name}: импортировано {rows:,}, ошибок 0",
                    flush=True,
                )
            except Exception as exc:
                conn.rollback()
                errors += 1
                importer.register_error(zip_path, exc)
                conn.commit()
                print(f"[{index}/{total}] {zip_path.name}: ошибка — {exc}", flush=True)

        if errors:
            raise RuntimeError(f"Импорт завершен с ошибками файлов: {errors}")

        if not args.skip_views:
            print(
                "ПРОГРЕСС: витрины | пересборка 4 основных и зависимой "
                "классификационной матвью",
                flush=True,
            )
            classification_summary_existed = importer.rebuild_materialized_views()
            conn.commit()

        if not args.skip_classification:
            print("ПРОГРЕСС: классификация | семантическая разметка поисковых запросов", flush=True)
            from wb_search_query_classifier import classify_database

            classify_database(conn, client_key=args.client)
            conn.commit()
        elif classification_summary_existed:
            print(
                "ПРОГРЕСС: классификация | разметка пропущена, "
                "зависимая сводная матвью восстанавливается",
                flush=True,
            )
            from wb_search_query_classifier import rebuild_summary_view

            rebuild_summary_view(conn)
            conn.commit()

        summary = importer.summary()
        elapsed = time.monotonic() - started_at
        print(
            "ИТОГ: "
            f"файлов импортировано {imported_files}, пропущено {skipped_files}, ошибок {errors}; "
            f"строк в запуске {imported_rows:,}; БД строк {summary['raw_rows']:,}, "
            f"дней {summary['days_count']}, SKU {summary['sku_count']:,}, "
            f"запросов {summary['search_query_count']:,}, дублей ключа {summary['duplicate_keys']}; "
            "таблицы public.wb_search_queries_daily, public.wb_search_queries_import_files; "
            "матвьюхи public.mv_wb_search_query_daily, public.mv_wb_search_category_query_daily, "
            "public.mv_wb_search_product_daily, public.mv_wb_search_queries_daily_summary; "
            f"время {format_duration(elapsed)}; статус полный",
            flush=True,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
