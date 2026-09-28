#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Import Gloria Jeans Ozon product/category workbooks into PostgreSQL.

This is the project-local version of the legacy Gloria Jeans
"Ассортиментная матрица/Ozon/ozon_cat_import.py" flow. It keeps the same
dynamic ozon_cat_* model, but reads the new Ozon Prodacts ZIP source, uses the
dashboard DB config, creates a DB backup before replace imports, and prints
structured progress for the Admin terminal.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


DEFAULT_SOURCE_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Дашборды\Data\Ozon\Prodacts"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "ozon_product_categories_import"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups" / "ozon_product_categories_import"
DEFAULT_FILE_PATTERN = "*.xlsx"
ARCHIVE_EXTENSIONS = {".zip"}
ARCHIVE_DATE_RE = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{4})(?!\d)")
EXTRACTED_ARCHIVES_DIR_NAME = "extracted_archives"

HEADER_ROW = 1
DATA_SKIP_ROWS = 3
CATEGORY_COLUMNS = ("Тип*", "Тип")
BATCH_SIZE_PRODUCTS = 1000
BATCH_SIZE_ATTRIBUTES = 5000

SERVICE_COLUMNS = {
    "№",
    "Ошибка",
    "Предупреждение",
    "Ссылки на фото 360",
    "Артикул фото",
}

# Columns consumed directly by rebuild_ozon_sku_scoring_view.py. Keep these on
# ozon_cat_products whenever Ozon provides them, even if a niche category file
# misses one of them.
FORCED_COMMON_COLUMNS = {
    "#Хештеги",
    "Rich-контент JSON",
    "SKU",
    "Аннотация",
    "Артикул*",
    "Артикул",
    "Вес в упаковке, г*",
    "Вес в упаковке, г",
    "Высота упаковки, мм*",
    "Высота упаковки, мм",
    "Длина упаковки, мм*",
    "Длина упаковки, мм",
    "Количество заводских упаковок",
    "Количество товара в УЕИ",
    "Минимальное количество оптом",
    "НДС, %*",
    "НДС, %",
    "Название товара",
    "Объединить в похожие товары",
    "Рассрочка",
    "Ссылка на главное фото*",
    "Ссылка на главное фото",
    "Ссылки на дополнительные фото",
    "Страна-изготовитель",
    "Тип*",
    "Тип",
    "Ускоренный сбор отзывов",
    "Цена до скидки, руб.",
    "Цена, руб.*",
    "Цена, руб.",
    "Ширина упаковки, мм*",
    "Ширина упаковки, мм",
    "Штрихкод (Серийный номер / EAN)",
    "Barcode",
}

DEPENDENT_MATERIALIZED_VIEWS_FOR_DROP = [
    "mv_product_abc_ozon",
    "mv_sku_card_scoring_ozon",
    "mv_ozon_category_stock_sku_attribute_stats",
    "mv_ozon_abc_product_orders_base",
    "mv_ozon_abc_product_stock_base",
    "mv_ozon_funnel_daily_summary_rollup",
    "mv_ozon_funnel_daily_product_rollup",
    "mv_ozon_funnel_product_options",
    "mv_ozon_funnel_filter_options",
    "mv_ozon_adv_daily_by_article_category",
    "mv_ozon_funnel_daily_by_article_category",
]

DEPENDENT_REGULAR_VIEWS_FOR_DROP = [
    "vw_ozon_category_stock_sku_attribute_stats",
    "vw_ozon_sku_sales_90d",
]

DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_LOG_PATH = DEFAULT_OUTPUT_DIR / "ozon_product_categories_import_runtime.log"

log_file_handler = logging.FileHandler(RUNTIME_LOG_PATH, encoding="utf-8")
log_file_handler.setLevel(logging.INFO)
log_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[log_file_handler])
logger = logging.getLogger(__name__)


def format_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {sec:02d}s"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def progress_line(current, total, item, totals, started_at):
    pct = (current / total * 100) if total else 100
    elapsed = time.time() - started_at
    eta = "-"
    if current and total and current < total:
        eta = format_duration(elapsed / current * (total - current))
    return (
        f"ПРОГРЕСС: {current}/{total} ({pct:.1f}%) | {item} | "
        f"{totals} | elapsed {format_duration(elapsed)} | ETA {eta}"
    )


def safe_backup_part(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "database")).strip("_") or "database"


def discover_source_files(directory_path, file_pattern=DEFAULT_FILE_PATTERN):
    directory = Path(directory_path)
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.rglob(file_pattern)
        if path.is_file() and not path.name.startswith("~$")
    )


def discover_archive_files(directory_path):
    directory = Path(directory_path)
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith("~$")
        and path.suffix.lower() in ARCHIVE_EXTENSIONS
    )


def archive_export_date(archive_path):
    match = ARCHIVE_DATE_RE.search(Path(archive_path).name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_archive_date_filter(value):
    normalized = str(value or "latest").strip().lower()
    if normalized in {"latest", "all"}:
        return normalized
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    raise ValueError("--archive-date должен быть latest, all, DD.MM.YYYY или YYYY-MM-DD")


def select_archives_for_import(archives, archive_date="latest"):
    archives = list(archives)
    date_filter = parse_archive_date_filter(archive_date)
    if date_filter == "all":
        return archives, None, []

    dated_archives = []
    undated_archives = []
    for archive_path in archives:
        archive_date_value = archive_export_date(archive_path)
        if archive_date_value is None:
            undated_archives.append(archive_path)
        else:
            dated_archives.append((archive_date_value, archive_path))

    if not dated_archives:
        return archives, None, []

    selected_date = max(date_value for date_value, _path in dated_archives) if date_filter == "latest" else date_filter
    selected = [path for date_value, path in dated_archives if date_value == selected_date]
    skipped = [path for date_value, path in dated_archives if date_value != selected_date] + undated_archives
    if not selected:
        available_dates = ", ".join(sorted({date_value.strftime("%d.%m.%Y") for date_value, _path in dated_archives}))
        raise RuntimeError(f"Не найдено архивов для даты {selected_date.strftime('%d.%m.%Y')}; доступны даты: {available_dates}")
    return selected, selected_date, skipped


def _ensure_inside_directory(target_path, root_dir):
    target = target_path.resolve()
    root = root_dir.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Небезопасный путь внутри архива: {target_path}") from exc
    return target


def decode_zip_member_name(member):
    filename = member.filename
    if member.flag_bits & 0x800:
        return filename
    try:
        return filename.encode("cp437").decode("cp866")
    except UnicodeError:
        return filename


def extract_zip_archive(archive_path, target_dir):
    archive = Path(archive_path)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    extracted_xlsx = 0
    try:
        with zipfile.ZipFile(archive) as zip_file:
            for member in zip_file.infolist():
                if member.is_dir():
                    continue
                member_name = decode_zip_member_name(member)
                destination = _ensure_inside_directory(target / member_name, target)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(member) as source, open(destination, "wb") as output:
                    shutil.copyfileobj(source, output)
                if destination.suffix.lower() == ".xlsx" and not destination.name.startswith("~$"):
                    extracted_xlsx += 1
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Не удалось распаковать ZIP {archive}: архив поврежден или не является ZIP") from exc
    return extracted_xlsx


def prepare_import_source(directory_path, file_pattern=DEFAULT_FILE_PATTERN, output_dir=DEFAULT_OUTPUT_DIR, archive_date="latest"):
    source_dir = Path(directory_path)
    if not source_dir.exists():
        raise FileNotFoundError(f"Source folder not found: {source_dir}")

    all_archives = discover_archive_files(source_dir)
    if not all_archives:
        return source_dir

    archives, selected_archive_date, skipped_archives = select_archives_for_import(all_archives, archive_date)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = Path(output_dir) / EXTRACTED_ARCHIVES_DIR_NAME / timestamp
    work_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print("ПЛАН: распаковка архивов Ozon Prodacts")
    print("=" * 80)
    print(f"Источник архивов: {source_dir}")
    print(f"Рабочая папка: {work_dir}")
    print(f"Архивов найдено: {len(all_archives)}; к распаковке: {len(archives)}; форматы: {', '.join(sorted(ARCHIVE_EXTENSIONS))}")
    if selected_archive_date:
        print(f"Дата архивов к импорту: {selected_archive_date.strftime('%d.%m.%Y')}")
    if skipped_archives:
        print(f"Архивов пропущено по дате: {len(skipped_archives)}")

    direct_files = discover_source_files(source_dir, file_pattern)
    copied_direct = 0
    if direct_files:
        direct_dir = work_dir / "_source_xlsx"
        direct_dir.mkdir(parents=True, exist_ok=True)
        for source_file in direct_files:
            shutil.copy2(source_file, direct_dir / source_file.name)
            copied_direct += 1
        print(f"Прямые XLSX скопированы в рабочую папку: {copied_direct}")

    started_at = time.time()
    total_extracted = 0
    total_errors = 0
    for idx, archive_path in enumerate(archives, 1):
        archive_target_dir = work_dir / f"{idx:03d}_{safe_backup_part(archive_path.stem)}"
        try:
            extracted = extract_zip_archive(archive_path, archive_target_dir)
        except Exception as exc:
            total_errors += 1
            logger.error("Ошибка распаковки %s: %s", archive_path, exc)
            raise
        total_extracted += extracted
        totals = f"распаковано XLSX {total_extracted:,}; прямых XLSX {copied_direct:,}; ошибок {total_errors:,}"
        print(progress_line(idx, len(archives), f"архив: {archive_path.name}", totals, started_at), flush=True)
        print(f"[{idx}/{len(archives)}] {archive_path.name}: extracted_xlsx {extracted:,}, errors 0", flush=True)

    prepared_files = discover_source_files(work_dir, file_pattern)
    if not prepared_files:
        raise RuntimeError(f"После распаковки архивов не найдено файлов по шаблону {file_pattern}: {work_dir}")

    print("=" * 80)
    print("РАСПАКОВКА ЗАВЕРШЕНА")
    print("=" * 80)
    print(f"  • Архивов найдено: {len(all_archives):,}")
    print(f"  • Архивов обработано: {len(archives):,}")
    print(f"  • Архивов пропущено по дате: {len(skipped_archives):,}")
    print(f"  • XLSX из архивов: {total_extracted:,}")
    print(f"  • Прямых XLSX скопировано: {copied_direct:,}")
    print(f"  • XLSX к импорту: {len(prepared_files):,}")
    print(f"  • Рабочая папка: {work_dir}")
    print("=" * 80 + "\n")
    return work_dir


def fix_and_read_excel(file_path: Path, **kwargs) -> pd.DataFrame:
    """Read an Ozon XLSX, fixing malformed empty fill styles if present."""
    buf = io.BytesIO()
    with zipfile.ZipFile(file_path, "r") as zin:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/styles.xml":
                    xml = data.decode("utf-8")
                    xml = re.sub(
                        r"<fill>\s*</fill>",
                        '<fill><patternFill patternType="none"/></fill>',
                        xml,
                    )
                    data = xml.encode("utf-8")
                zout.writestr(item, data)
    buf.seek(0)
    try:
        return pd.read_excel(buf, sheet_name="Шаблон", engine="openpyxl", **kwargs)
    except ValueError:
        buf.seek(0)
        return pd.read_excel(buf, engine="openpyxl", **kwargs)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Импорт Ozon категорий, товаров и характеристик из новых Prodacts ZIP/XLSX в PostgreSQL."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--file-pattern", default=DEFAULT_FILE_PATTERN)
    parser.add_argument(
        "--archive-date",
        default="latest",
        help="Какие ZIP-архивы распаковывать: latest, all, DD.MM.YYYY или YYYY-MM-DD.",
    )
    parser.add_argument("--mode", choices=["replace", "append"], default="replace")
    parser.add_argument("--db-backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument(
        "--skip-db-backup",
        action="store_true",
        help="Аварийный режим: не делать pg_dump перед replace-импортом.",
    )
    parser.add_argument(
        "--skip-views",
        action="store_true",
        help="Не пересобирать Ozon SKU/ABC materialized views после импорта.",
    )
    return parser


def dashboard_db_config():
    os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("WB_PRODUCTS_DB_NAME", "wb_products"))
    return app.read_db_config()


def find_pg_dump():
    explicit = os.environ.get("PG_DUMP")
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)
    found = shutil.which("pg_dump")
    if found:
        return found
    root = Path(r"C:\Program Files\PostgreSQL")
    if root.exists():
        candidates = sorted(root.glob(r"*\bin\pg_dump.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


class OzonCatImporter:
    """Dynamic importer for Ozon category workbooks."""

    def __init__(
        self,
        db_config,
        mode="replace",
        require_db_backup=True,
        db_backup_dir=DEFAULT_BACKUP_DIR,
        output_dir=DEFAULT_OUTPUT_DIR,
        file_pattern=DEFAULT_FILE_PATTERN,
        rebuild_views=True,
        archive_date="latest",
    ):
        self.db_config = db_config
        self.mode = mode
        self.require_db_backup = require_db_backup
        self.db_backup_dir = Path(db_backup_dir)
        self.output_dir = Path(output_dir)
        self.file_pattern = file_pattern
        self.rebuild_views = rebuild_views
        self.archive_date = archive_date
        self.db_backup_path = None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_backup_dir.mkdir(parents=True, exist_ok=True)

        self.conn = None
        self.cursor = None
        self.all_columns = set()
        self.files_analyzed = 0
        self.file_analysis = {}
        self.file_categories = {}
        self.file_columns_map = {}
        self.category_to_columns = defaultdict(set)
        self.category_to_files = defaultdict(set)
        self.category_total_rows = defaultdict(int)
        self.category_file_count = defaultdict(int)
        self.category_column_frequency = defaultdict(int)
        self.common_columns = set()
        self.category_specific_columns = set()
        self.all_categories = set()
        self.category_cache = {}
        self.attribute_cache = {}
        self.fields_mapping = {}

    def connect(self):
        self.conn = psycopg2.connect(**self.db_config)
        self.conn.autocommit = False
        self.cursor = self.conn.cursor()
        logger.info("Подключено к БД: %s", self.db_config.get("database"))

    def close(self):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        logger.info("Соединение с БД закрыто")

    def normalize_db_name(self, col_name: str) -> str:
        db_name = str(col_name).lower().strip()
        db_name = db_name.replace(" ", "_").replace("/", "_")
        db_name = db_name.replace("(", "").replace(")", "")
        transliteration = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
            "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
            "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
            "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
            ".": "_", ",": "", ":": "", ";": "", '"': "", "'": "", "№": "num",
            "%": "pct", "-": "_", "₽": "rub", "*": "",
        }
        for ru, en in transliteration.items():
            db_name = db_name.replace(ru, en)
        db_name = re.sub(r"[^a-z0-9_]", "", db_name)
        db_name = re.sub(r"_+", "_", db_name).strip("_")
        if not db_name:
            db_name = "unnamed_col"
        if db_name[0].isdigit():
            db_name = f"col_{db_name}"
        return db_name

    def create_common_fields_mapping(self):
        mapping = {}
        used_names = set()
        for col in sorted(self.common_columns):
            db_name = self.normalize_db_name(col)
            base_name = db_name
            counter = 2
            while db_name in used_names:
                db_name = f"{base_name}_{counter}"
                counter += 1
            used_names.add(db_name)
            mapping[col] = db_name
        self.fields_mapping = mapping
        return mapping

    def clean_value(self, value):
        if pd.isna(value):
            return None
        text = str(value).strip()
        if text.startswith("'"):
            text = text[1:].strip()
        if text == "" or text.lower() in {"none", "nan"}:
            return None
        return text

    def valid_columns(self, df):
        return [
            col
            for col in df.columns
            if col not in SERVICE_COLUMNS and not str(col).startswith("Unnamed")
        ]

    def category_column(self, df):
        for column in CATEGORY_COLUMNS:
            if column in df.columns:
                return column
        return None

    def read_product_frame(self, file_path: Path):
        df = fix_and_read_excel(file_path, header=HEADER_ROW)
        df = df.iloc[DATA_SKIP_ROWS:].reset_index(drop=True)
        return df.dropna(how="all")

    def analyze_file_structure(self, file_path: Path):
        try:
            df = self.read_product_frame(file_path)
            columns = set(self.valid_columns(df))
            total_rows = len(df)
            category_column = self.category_column(df)
            if not category_column:
                logger.warning("%s: нет столбца категории %s", file_path.name, CATEGORY_COLUMNS)
                return {
                    "columns": columns,
                    "categories": [],
                    "total_rows": total_rows,
                    "rows_without_category": total_rows,
                    "missing_category_column": True,
                    "multi_category_file": False,
                }

            cat_series = df[category_column].fillna("").astype(str).str.strip()
            rows_without_category = int((cat_series == "").sum())
            unique_categories = sorted(cat_series[cat_series != ""].unique().tolist())
            return {
                "columns": columns,
                "categories": unique_categories,
                "total_rows": total_rows,
                "rows_without_category": rows_without_category,
                "missing_category_column": False,
                "multi_category_file": len(unique_categories) > 1,
            }
        except Exception as exc:
            logger.warning("Не удалось прочитать %s: %s", file_path.name, exc)
            return {
                "columns": set(),
                "categories": [],
                "total_rows": 0,
                "rows_without_category": 0,
                "missing_category_column": True,
                "multi_category_file": False,
            }

    def analyze_all_files(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        directory = Path(directory_path)
        files = discover_source_files(directory, file_pattern)
        print("\n" + "=" * 80)
        print("ПЛАН: анализ структуры Ozon Prodacts")
        print("=" * 80)
        print(f"Источник: {directory}")
        print(f"Файлов XLSX: {len(files)}")

        total_rows_all = 0
        total_rows_without_category = 0
        files_without_category_column = 0
        multi_category_files = 0
        started_at = time.time()

        for idx, file_path in enumerate(files, 1):
            analysis = self.analyze_file_structure(file_path)
            filename = file_path.name
            if analysis["columns"]:
                columns = analysis["columns"]
                categories = analysis["categories"]
                self.file_analysis[filename] = analysis
                self.file_categories[filename] = categories
                self.file_columns_map[filename] = columns
                self.all_columns.update(columns)
                self.files_analyzed += 1
                total_rows_all += analysis["total_rows"]
                total_rows_without_category += analysis["rows_without_category"]
                if analysis["missing_category_column"]:
                    files_without_category_column += 1
                if analysis["multi_category_file"]:
                    multi_category_files += 1
                for category in categories:
                    self.all_categories.add(category)
                    self.category_to_columns[category].update(columns)
                    self.category_to_files[category].add(filename)
                    self.category_total_rows[category] += analysis["total_rows"]
            totals = (
                f"категорий {len(self.all_categories):,}; строк {total_rows_all:,}; "
                f"без категории {total_rows_without_category:,}; ошибок {files_without_category_column:,}"
            )
            print(progress_line(idx, len(files), f"файл: {filename}", totals, started_at), flush=True)

        for category, columns in self.category_to_columns.items():
            self.category_file_count[category] = len(self.category_to_files[category])
            for col in columns:
                self.category_column_frequency[col] += 1

        total_categories = len(self.all_categories)
        if total_categories > 0:
            self.common_columns = {
                col for col, freq in self.category_column_frequency.items()
                if freq == total_categories
            }
        self.common_columns.update(col for col in FORCED_COMMON_COLUMNS if col in self.all_columns)
        self.category_specific_columns = self.all_columns - self.common_columns

        print("=" * 80)
        print("АНАЛИЗ ЗАВЕРШЕН")
        print("=" * 80)
        print(f"  • Файлов: {self.files_analyzed:,}")
        print(f"  • Строк: {total_rows_all:,}")
        print(f"  • Категорий: {len(self.all_categories):,}")
        print(f"  • Всего характеристик: {len(self.all_columns):,}")
        print(f"  • Общих характеристик: {len(self.common_columns):,}")
        print(f"  • Категорийных характеристик: {len(self.category_specific_columns):,}")
        print(f"  • Строк без категории: {total_rows_without_category:,}")
        print(f"  • Файлов без столбца категории: {files_without_category_column:,}")
        print(f"  • Многокатегорийных файлов: {multi_category_files:,}")
        print("=" * 80 + "\n")
        return self.file_analysis

    def create_support_tables(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_cat_categories (
                category_id SERIAL PRIMARY KEY,
                category_name TEXT NOT NULL UNIQUE,
                files_count INTEGER DEFAULT 0,
                total_rows INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_cat_attribute_definitions (
                attribute_id SERIAL PRIMARY KEY,
                attribute_name TEXT NOT NULL UNIQUE,
                attribute_scope TEXT NOT NULL DEFAULT 'category_specific'
                    CHECK (attribute_scope IN ('common', 'category_specific')),
                data_type TEXT NOT NULL DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_cat_common_attributes (
                attribute_id INTEGER PRIMARY KEY
                    REFERENCES public.ozon_cat_attribute_definitions(attribute_id) ON DELETE CASCADE,
                attribute_name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_cat_category_attributes (
                id BIGSERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL
                    REFERENCES public.ozon_cat_categories(category_id) ON DELETE CASCADE,
                attribute_id INTEGER NOT NULL
                    REFERENCES public.ozon_cat_attribute_definitions(attribute_id) ON DELETE CASCADE,
                attribute_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category_id, attribute_id)
            )
            """
        )
        self.conn.commit()

    def drop_dependent_materialized_views(self):
        print("Удаление зависимых Ozon materialized views перед заменой ozon_cat_*...")
        for view_name in DEPENDENT_MATERIALIZED_VIEWS_FOR_DROP:
            self.cursor.execute(f"DROP MATERIALIZED VIEW IF EXISTS public.{view_name}")
        self.conn.commit()
        print(f"Зависимые Ozon materialized views удалены: {len(DEPENDENT_MATERIALIZED_VIEWS_FOR_DROP)}")

    def drop_dependent_regular_views(self):
        print("Удаление зависимых Ozon regular views перед заменой ozon_cat_*...")
        for view_name in DEPENDENT_REGULAR_VIEWS_FOR_DROP:
            self.cursor.execute(f"DROP VIEW IF EXISTS public.{view_name}")
        self.conn.commit()
        print(f"Зависимые Ozon regular views удалены: {len(DEPENDENT_REGULAR_VIEWS_FOR_DROP)}")

    def drop_existing_tables(self):
        print("Удаление старых ozon_cat_* таблиц...")
        self.drop_dependent_materialized_views()
        self.drop_dependent_regular_views()
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_product_attributes")
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_products")
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_category_attributes")
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_common_attributes")
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_attribute_definitions")
        self.cursor.execute("DROP TABLE IF EXISTS public.ozon_cat_categories")
        self.conn.commit()
        print("Старые ozon_cat_* таблицы удалены")

    def create_dynamic_products_table(self, fields_mapping):
        print(f"\nРежим работы: {self.mode}")
        columns_sql = ["product_id BIGSERIAL PRIMARY KEY"]
        for _source_col, db_col in sorted(fields_mapping.items()):
            columns_sql.append(f"{db_col} TEXT")
        columns_sql.extend(
            [
                "category_id INTEGER REFERENCES public.ozon_cat_categories(category_id)",
                "category_name TEXT",
                "import_file VARCHAR(500)",
                "source_row_num INTEGER",
                "imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            ]
        )
        self.cursor.execute(
            "CREATE TABLE IF NOT EXISTS public.ozon_cat_products (\n    "
            + ",\n    ".join(columns_sql)
            + "\n)"
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ozon_cat_product_attributes (
                id BIGSERIAL PRIMARY KEY,
                product_id BIGINT NOT NULL
                    REFERENCES public.ozon_cat_products(product_id) ON DELETE CASCADE,
                attribute_id INTEGER NOT NULL
                    REFERENCES public.ozon_cat_attribute_definitions(attribute_id),
                value_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, attribute_id)
            )
            """
        )
        self.conn.commit()
        print(f"Таблица public.ozon_cat_products создана с {len(fields_mapping)} общими столбцами")

    def preload_reference_data(self):
        self.category_cache = {}
        self.attribute_cache = {}
        self.cursor.execute("SELECT category_id, category_name FROM public.ozon_cat_categories")
        for category_id, category_name in self.cursor.fetchall():
            self.category_cache[category_name] = category_id
        self.cursor.execute("SELECT attribute_id, attribute_name FROM public.ozon_cat_attribute_definitions")
        for attribute_id, attribute_name in self.cursor.fetchall():
            self.attribute_cache[attribute_name] = attribute_id
        logger.info("Кэш: categories=%s, attributes=%s", len(self.category_cache), len(self.attribute_cache))

    def get_or_create_category(self, category_name):
        if category_name in self.category_cache:
            return self.category_cache[category_name]
        self.cursor.execute(
            """
            INSERT INTO public.ozon_cat_categories (category_name)
            VALUES (%s)
            ON CONFLICT (category_name) DO UPDATE
            SET category_name = EXCLUDED.category_name
            RETURNING category_id
            """,
            (category_name,),
        )
        category_id = self.cursor.fetchone()[0]
        self.category_cache[category_name] = category_id
        return category_id

    def get_or_create_attributes_bulk(self, attribute_names):
        new_names = [name for name in attribute_names if name not in self.attribute_cache]
        if not new_names:
            return
        unique_new_names = sorted(set(new_names))
        execute_values(
            self.cursor,
            """
            INSERT INTO public.ozon_cat_attribute_definitions (attribute_name, data_type)
            VALUES %s
            ON CONFLICT (attribute_name) DO NOTHING
            """,
            [(name, "text") for name in unique_new_names],
            page_size=1000,
        )
        self.cursor.execute(
            "SELECT attribute_id, attribute_name FROM public.ozon_cat_attribute_definitions WHERE attribute_name = ANY(%s)",
            (unique_new_names,),
        )
        for attribute_id, attribute_name in self.cursor.fetchall():
            self.attribute_cache[attribute_name] = attribute_id

    def sync_metadata_to_db(self):
        print("\nЗапись категорий и характеристик в БД...")
        started_at = time.time()
        categories = sorted(self.all_categories)
        for idx, category in enumerate(categories, 1):
            category_id = self.get_or_create_category(category)
            self.cursor.execute(
                """
                UPDATE public.ozon_cat_categories
                SET files_count = %s, total_rows = %s
                WHERE category_id = %s
                """,
                (
                    int(self.category_file_count.get(category, 0)),
                    int(self.category_total_rows.get(category, 0)),
                    category_id,
                ),
            )
            if idx == 1 or idx == len(categories) or idx % 25 == 0:
                print(
                    progress_line(
                        idx,
                        len(categories),
                        f"категория: {category}",
                        f"категорий записано {idx:,}; атрибутов всего {len(self.all_columns):,}",
                        started_at,
                    ),
                    flush=True,
                )

        all_attributes = []
        for attr_name in sorted(self.all_columns):
            scope = "common" if attr_name in self.common_columns else "category_specific"
            all_attributes.append((attr_name, scope, "text"))
        if all_attributes:
            execute_values(
                self.cursor,
                """
                INSERT INTO public.ozon_cat_attribute_definitions (attribute_name, attribute_scope, data_type)
                VALUES %s
                ON CONFLICT (attribute_name) DO UPDATE
                SET attribute_scope = EXCLUDED.attribute_scope
                """,
                all_attributes,
                page_size=1000,
            )

        self.cursor.execute("SELECT attribute_id, attribute_name FROM public.ozon_cat_attribute_definitions")
        self.attribute_cache = {name: attr_id for attr_id, name in self.cursor.fetchall()}

        if self.common_columns:
            common_rows = [
                (self.attribute_cache[attr_name], attr_name)
                for attr_name in sorted(self.common_columns)
                if attr_name in self.attribute_cache
            ]
            execute_values(
                self.cursor,
                """
                INSERT INTO public.ozon_cat_common_attributes (attribute_id, attribute_name)
                VALUES %s
                ON CONFLICT (attribute_id) DO UPDATE
                SET attribute_name = EXCLUDED.attribute_name
                """,
                common_rows,
                page_size=1000,
            )

        category_attr_rows = []
        for category, columns in self.category_to_columns.items():
            category_id = self.category_cache.get(category)
            if not category_id:
                continue
            for attr_name in sorted(columns - self.common_columns):
                attr_id = self.attribute_cache.get(attr_name)
                if not attr_id:
                    continue
                category_attr_rows.append((category_id, attr_id, attr_name))
        if category_attr_rows:
            execute_values(
                self.cursor,
                """
                INSERT INTO public.ozon_cat_category_attributes (category_id, attribute_id, attribute_name)
                VALUES %s
                ON CONFLICT (category_id, attribute_id) DO UPDATE
                SET attribute_name = EXCLUDED.attribute_name
                """,
                category_attr_rows,
                page_size=5000,
            )
        self.conn.commit()
        print("Категории и характеристики записаны в БД")

    def drop_indexes(self):
        print("Удаление индексов для ускорения загрузки...")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_products_file_row")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_products_category_id")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_products_artikul")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_products_sku")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_product_attrs_product")
        self.cursor.execute("DROP INDEX IF EXISTS public.idx_ozon_cat_product_attrs_attribute")
        self.conn.commit()
        print("Индексы удалены")

    def create_indexes(self):
        print("\nСоздание индексов...")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_file_row ON public.ozon_cat_products(import_file, source_row_num)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_category_id ON public.ozon_cat_products(category_id)")
        if "artikul" in self.fields_mapping.values():
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_artikul ON public.ozon_cat_products(artikul)")
        if "sku" in self.fields_mapping.values():
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_products_sku ON public.ozon_cat_products(sku)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_product_attrs_product ON public.ozon_cat_product_attributes(product_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_ozon_cat_product_attrs_attribute ON public.ozon_cat_product_attributes(attribute_id)")
        self.cursor.execute("ANALYZE public.ozon_cat_products")
        self.cursor.execute("ANALYZE public.ozon_cat_product_attributes")
        self.cursor.execute("ANALYZE public.ozon_cat_category_attributes")
        self.conn.commit()
        print("Индексы созданы")

    def build_product_rows(self, df, file_path):
        product_rows = []
        category_column = self.category_column(df)
        for row_idx, row in df.iterrows():
            row_data = {}
            for ozon_col, db_col in self.fields_mapping.items():
                if ozon_col in row.index:
                    row_data[db_col] = self.clean_value(row[ozon_col])
            category_name = self.clean_value(row[category_column]) if category_column else None
            category_id = self.get_or_create_category(category_name) if category_name else None
            row_data["category_id"] = category_id
            row_data["category_name"] = category_name
            row_data["import_file"] = file_path.name
            row_data["source_row_num"] = int(row_idx)
            product_rows.append(row_data)
        return product_rows

    def bulk_insert_products(self, batch_data):
        if not batch_data:
            return []
        columns = list(batch_data[0].keys())
        query = f"""
            INSERT INTO public.ozon_cat_products ({", ".join(columns)})
            VALUES %s
            RETURNING product_id, import_file, source_row_num
        """
        values = [tuple(row.get(col) for col in columns) for row in batch_data]
        execute_values(self.cursor, query, values, page_size=min(BATCH_SIZE_PRODUCTS, len(values)))
        return self.cursor.fetchall()

    def bulk_insert_attributes(self, attributes_batch):
        if not attributes_batch:
            return
        execute_values(
            self.cursor,
            """
            INSERT INTO public.ozon_cat_product_attributes (product_id, attribute_id, value_text)
            VALUES %s
            ON CONFLICT (product_id, attribute_id)
            DO UPDATE SET value_text = EXCLUDED.value_text
            """,
            attributes_batch,
            page_size=min(BATCH_SIZE_ATTRIBUTES, len(attributes_batch)),
        )

    def import_file(self, file_path: Path):
        try:
            df = self.read_product_frame(file_path)
            if df.empty:
                return 0, 0
            valid_cols = self.valid_columns(df)
            self.get_or_create_attributes_bulk(valid_cols)
            product_rows = self.build_product_rows(df, file_path)
            inserted_map = {}
            imported = 0
            errors = 0
            for start in range(0, len(product_rows), BATCH_SIZE_PRODUCTS):
                chunk = product_rows[start:start + BATCH_SIZE_PRODUCTS]
                returned = self.bulk_insert_products(chunk)
                for product_id, _import_file, source_row_num in returned:
                    inserted_map[int(source_row_num)] = int(product_id)
                imported += len(returned)

            category_column = self.category_column(df)
            specific_columns = [
                col for col in valid_cols
                if col not in self.common_columns and col != category_column
            ]
            attributes_batch = []
            for row_idx, row in df.iterrows():
                product_id = inserted_map.get(int(row_idx))
                if not product_id:
                    errors += 1
                    continue
                for col_name in specific_columns:
                    value = self.clean_value(row[col_name])
                    if value is None:
                        continue
                    attr_id = self.attribute_cache.get(col_name)
                    if not attr_id:
                        errors += 1
                        continue
                    attributes_batch.append((product_id, attr_id, value))
                    if len(attributes_batch) >= BATCH_SIZE_ATTRIBUTES:
                        self.bulk_insert_attributes(attributes_batch)
                        attributes_batch = []
            if attributes_batch:
                self.bulk_insert_attributes(attributes_batch)
            self.conn.commit()
            return imported, errors
        except Exception as exc:
            logger.error("Критическая ошибка при импорте %s: %s", file_path.name, exc)
            self.conn.rollback()
            return 0, 1

    def import_all_files(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        directory = Path(directory_path)
        files = discover_source_files(directory, file_pattern)
        print("\n" + "=" * 80)
        print("ПЛАН: импорт Ozon товаров и характеристик в БД")
        print("=" * 80)
        print(f"Файлов к обработке: {len(files)}")
        print(f"Батч товаров: {BATCH_SIZE_PRODUCTS}; батч атрибутов: {BATCH_SIZE_ATTRIBUTES}")

        started_at = time.time()
        total_imported = 0
        total_errors = 0
        for idx, file_path in enumerate(files, 1):
            imported, errors = self.import_file(file_path)
            total_imported += imported
            total_errors += errors
            totals = f"товаров {total_imported:,}; ошибок {total_errors:,}"
            print(progress_line(idx, len(files), f"файл: {file_path.name}", totals, started_at), flush=True)
            print(f"[{idx}/{len(files)}] {file_path.name}: imported {imported:,}, errors {errors:,}", flush=True)
            logger.info("[%s/%s] %s: imported %s, errors %s", idx, len(files), file_path.name, imported, errors)

        print("=" * 80)
        print("ИМПОРТ ФАЙЛОВ ЗАВЕРШЕН")
        print("=" * 80)
        print(f"  • Всего импортировано: {total_imported:,} товаров")
        print(f"  • Всего ошибок: {total_errors:,}")
        print("=" * 80 + "\n")
        return total_imported, total_errors

    def create_database_backup(self):
        if self.mode != "replace":
            return None
        pg_dump = find_pg_dump()
        if not pg_dump:
            raise RuntimeError("pg_dump не найден; replace-импорт Ozon ассортимента требует бэкап БД")
        database = self.db_config.get("database")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_backup_dir / f"{safe_backup_part(database)}_before_ozon_product_categories_{timestamp}.dump"
        command = [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(backup_path),
            "--host",
            str(self.db_config.get("host", "localhost")),
            "--port",
            str(self.db_config.get("port", 5432)),
            "--username",
            str(self.db_config.get("user", "postgres")),
            str(database),
        ]
        env = os.environ.copy()
        if self.db_config.get("password"):
            env["PGPASSWORD"] = str(self.db_config.get("password"))

        print("\n" + "=" * 80)
        print("БЭКАП БД ПЕРЕД ИМПОРТОМ")
        print("=" * 80)
        print(f"База данных: {database}")
        print(f"Файл бэкапа: {backup_path}")
        print("Секреты в лог не выводятся.")
        completed = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"pg_dump завершился с кодом {completed.returncode}: {completed.stderr.strip()}")
        self.db_backup_path = backup_path
        print(f"Бэкап БД создан: {backup_path}", flush=True)
        logger.info("Бэкап БД создан: %s", backup_path)
        return backup_path

    def rebuild_assortment_views(self):
        jobs = [
            (
                PROJECT_ROOT / "scripts" / "import_ozon_stock_reports.py",
                ["--skip-import", "--no-refresh"],
                "regular views Ozon stock/category",
            ),
            (
                PROJECT_ROOT / "scripts" / "import_ozon_funnel_reports.py",
                ["--skip-import", "--skip-dependent-views"],
                "витрины Ozon funnel",
            ),
            (
                PROJECT_ROOT / "Скрипты" / "rebuild_ozon_adv_daily_category_view.py",
                [],
                "витрина товарной рекламы Ozon",
            ),
            (
                PROJECT_ROOT / "scripts" / "build_ozon_abc_materialized_views.py",
                [],
                "Ozon ABC/SKU views",
            ),
            (
                PROJECT_ROOT / "scripts" / "rebuild_ozon_category_stock_materialized_view.py",
                [],
                "витрина остатков Ozon по категориям",
            ),
        ]
        env = os.environ.copy()
        env["DASHBOARD_DB_NAME"] = str(self.db_config.get("database") or "wb_products")
        print("\n" + "=" * 80)
        print("ПЛАН: пересборка Ozon assortment-dependent views")
        print("=" * 80)
        print(f"База данных: {env['DASHBOARD_DB_NAME']}")
        print(f"Задач пересборки: {len(jobs)}")
        started_at = time.time()
        for idx, (script_path, args, label) in enumerate(jobs, 1):
            if not script_path.exists():
                raise FileNotFoundError(f"View rebuild script not found: {script_path}")
            command = [sys.executable, "-X", "utf8", "-u", str(script_path), *args]
            print(progress_line(idx - 1, len(jobs), label, "запуск", started_at), flush=True)
            print(f"[{idx}/{len(jobs)}] {label}: {script_path} {' '.join(args)}", flush=True)
            completed = subprocess.run(command, env=env)
            if completed.returncode != 0:
                raise RuntimeError(f"View rebuild failed for {label} with code {completed.returncode}")
            print(progress_line(idx, len(jobs), label, "готово", started_at), flush=True)

    # =========================================================================
    # RUN
    # =========================================================================
    def run(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        start_time = datetime.now()
        total_imported = 0
        total_errors = 0

        print("ПЛАН: полный импорт ассортимента Ozon")
        print(f"Источник: {directory_path}")
        print(f"Режим: {self.mode}")
        print(f"Бэкап перед replace: {'да' if self.require_db_backup and self.mode == 'replace' else 'нет'}")

        prepared_directory = prepare_import_source(directory_path, file_pattern, self.output_dir, self.archive_date)
        if Path(prepared_directory) != Path(directory_path):
            print(f"Рабочая папка импорта после распаковки: {prepared_directory}")

        self.analyze_all_files(prepared_directory, file_pattern)
        self.create_common_fields_mapping()

        if self.mode == "replace":
            if self.require_db_backup:
                self.create_database_backup()
            else:
                print("ВНИМАНИЕ: replace-импорт запущен без бэкапа по явному --skip-db-backup")

        self.connect()
        try:
            if self.mode == "replace":
                self.drop_existing_tables()
            self.create_support_tables()
            self.create_dynamic_products_table(self.fields_mapping)
            self.preload_reference_data()
            self.sync_metadata_to_db()
            self.drop_indexes()
            total_imported, total_errors = self.import_all_files(prepared_directory, file_pattern)
            self.create_indexes()
            if self.rebuild_views:
                self.rebuild_assortment_views()
            else:
                print("Пересборка Ozon SKU/ABC views пропущена по --skip-views")
        finally:
            self.close()

        end_time = datetime.now()
        duration_seconds = (end_time - start_time).total_seconds()
        self.save_import_log(directory_path, total_imported, total_errors, start_time, end_time)
        print(
            f"ИТОГ: импортировано {total_imported:,}; ошибок {total_errors:,}; "
            f"backup={self.db_backup_path or 'нет'}; elapsed {format_duration(duration_seconds)}",
            flush=True,
        )

    def save_import_log(self, directory_path, total_imported, total_errors, start_time, end_time):
        payload = {
            "source_dir": str(directory_path),
            "database": str(self.db_config.get("database") or ""),
            "mode": self.mode,
            "files_analyzed": self.files_analyzed,
            "categories": len(self.all_categories),
            "all_columns": len(self.all_columns),
            "common_columns": len(self.common_columns),
            "category_specific_columns": len(self.category_specific_columns),
            "imported": int(total_imported),
            "errors": int(total_errors),
            "backup_path": str(self.db_backup_path or ""),
            "started_at": start_time.isoformat(timespec="seconds"),
            "finished_at": end_time.isoformat(timespec="seconds"),
            "elapsed_seconds": round((end_time - start_time).total_seconds(), 1),
        }
        timestamp = end_time.strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"ozon_product_categories_import_{timestamp}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Файл итогов: {output_path}", flush=True)


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    db_config = dashboard_db_config()
    importer = OzonCatImporter(
        db_config,
        mode=args.mode,
        require_db_backup=not args.skip_db_backup,
        db_backup_dir=args.db_backup_dir,
        file_pattern=args.file_pattern,
        rebuild_views=not args.skip_views,
        archive_date=args.archive_date,
    )
    try:
        importer.run(args.source_dir, args.file_pattern)
    except Exception as exc:
        logger.error("Критическая ошибка: %s", exc)
        raise
    logger.info("Готово")


if __name__ == "__main__":
    main()
