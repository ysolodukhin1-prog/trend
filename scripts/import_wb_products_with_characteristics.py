#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WB Data Import Script
Анализ структуры файлов и импорт данных WB в PostgreSQL

Логика:
1. Анализирует все xlsx-файлы
2. Собирает категории из столбца "Категория продавца"
3. Присваивает каждой категории все столбцы файла, в котором она встретилась
4. Определяет общие характеристики (общие для всех категорий)
5. Определяет категорийные характеристики
6. Пересоздаёт структуру БД (если MODE='replace')
7. Загружает метаданные
8. Импортирует товары и значения характеристик
"""

import argparse
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
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values


# =============================================================================
# PATHS / LOGGING
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))

import app  # noqa: E402


DEFAULT_SOURCE_DIR = Path(
    r"G:\Общие диски\Kokoc Marketplaces\Clients\Gloria Jeans\Аналитика"
    r"\Дашборды\Data\Wb\Prodacts"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "wb_products_characteristics_import"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups" / "wb_assortment_import"
SOURCE_SHEET_NAME = "Товары"
DEFAULT_FILE_PATTERN = "*.xlsx"
BATCH_SIZE_PRODUCTS = 1000
BATCH_SIZE_ATTRIBUTES = 5000
ARCHIVE_EXTENSIONS = {".zip"}
ARCHIVE_DATE_RE = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{4})(?!\d)")
EXTRACTED_ARCHIVES_DIR_NAME = "extracted_archives"

DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_LOG_PATH = DEFAULT_OUTPUT_DIR / "wb_products_characteristics_import_runtime.log"

log_file_handler = logging.FileHandler(RUNTIME_LOG_PATH, encoding="utf-8")
log_file_handler.setLevel(logging.INFO)
log_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)

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


def discover_source_files(directory_path, file_pattern=DEFAULT_FILE_PATTERN):
    directory = Path(directory_path)
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
                destination = _ensure_inside_directory(target / member.filename, target)
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
    all_archives = discover_archive_files(source_dir)
    if not all_archives:
        return source_dir
    archives, selected_archive_date, skipped_archives = select_archives_for_import(all_archives, archive_date)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = Path(output_dir) / EXTRACTED_ARCHIVES_DIR_NAME / timestamp
    work_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print("ПЛАН: распаковка архивов WB Prodacts")
    print("=" * 80)
    print(f"Источник архивов: {source_dir}")
    print(f"Рабочая папка: {work_dir}")
    print(f"Архивов найдено: {len(all_archives)}; к распаковке: {len(archives)}; поддерживаемые форматы: {', '.join(sorted(ARCHIVE_EXTENSIONS))}")
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


def _read_products_sheet_openpyxl(file_path):
    try:
        return pd.read_excel(
            file_path,
            sheet_name=SOURCE_SHEET_NAME,
            header=2,
            engine="openpyxl",
        )
    except ValueError as exc:
        if "Worksheet named" not in str(exc):
            raise
        return pd.read_excel(file_path, header=2, engine="openpyxl")


def _copy_xlsx_without_data_validations(source_path, target_path):
    """Создать техническую копию XLSX без dataValidations, не меняя источник."""
    removed_blocks = 0
    with zipfile.ZipFile(source_path) as source_zip, zipfile.ZipFile(
        target_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as target_zip:
        for member in source_zip.infolist():
            payload = source_zip.read(member.filename)
            if member.filename.startswith("xl/worksheets/") and member.filename.endswith(".xml"):
                try:
                    root = ElementTree.fromstring(payload)
                except ElementTree.ParseError:
                    pass
                else:
                    validation_nodes = [
                        child
                        for child in list(root)
                        if child.tag.rsplit("}", 1)[-1] == "dataValidations"
                    ]
                    if validation_nodes:
                        for child in validation_nodes:
                            root.remove(child)
                        removed_blocks += len(validation_nodes)
                        payload = ElementTree.tostring(
                            root,
                            encoding="utf-8",
                            xml_declaration=True,
                        )
            target_zip.writestr(member, payload)
    return removed_blocks


def read_products_sheet(file_path):
    try:
        df = _read_products_sheet_openpyxl(file_path)
    except Exception as original_exc:
        with TemporaryDirectory(prefix="wb_products_xlsx_") as temp_dir:
            sanitized_path = Path(temp_dir) / Path(file_path).name
            removed_blocks = _copy_xlsx_without_data_validations(file_path, sanitized_path)
            if not removed_blocks:
                raise
            logger.warning(
                "Повторное чтение %s после удаления %s некорректных блоков dataValidations: %s",
                Path(file_path).name,
                removed_blocks,
                original_exc,
            )
            df = _read_products_sheet_openpyxl(sanitized_path)
    return df.iloc[1:].reset_index(drop=True)


def normalize_product_sku(value):
    if pd.isna(value):
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if re.fullmatch(r"\d+\.0+", normalized):
        normalized = normalized.split(".", 1)[0]
    return normalized


def source_snapshot_date(file_path):
    """Дата снимка из имени XLSX или родительской папки распакованного архива."""
    path = Path(file_path)
    for part in reversed(path.parts):
        match = ARCHIVE_DATE_RE.search(part)
        if not match:
            continue
        try:
            return datetime.strptime(match.group(1), "%d.%m.%Y").date()
        except ValueError:
            continue
    # Прямой недатированный XLSX считаем более новым, чем архивные снимки.
    return date.max


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


def safe_backup_part(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "database")).strip("_") or "database"


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Импорт WB товаров и характеристик из новых Prodacts XLSX в PostgreSQL."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--file-pattern", default=DEFAULT_FILE_PATTERN)
    parser.add_argument(
        "--archive-date",
        default="all",
        help="Какие ZIP-архивы распаковывать: latest, all, DD.MM.YYYY или YYYY-MM-DD.",
    )
    parser.add_argument(
        "--mode",
        choices=["replace", "append", "backfill"],
        default="replace",
        help=(
            "replace — пересобрать ассортимент; append — добавить все строки; "
            "backfill — добавить только отсутствующие в БД nmID."
        ),
    )
    parser.add_argument("--db-backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument(
        "--skip-db-backup",
        action="store_true",
        help="Аварийный режим: не делать pg_dump перед записью в БД.",
    )
    parser.add_argument(
        "--skip-views",
        action="store_true",
        help="Не пересобирать WB assortment materialized views после импорта.",
    )
    return parser


def dashboard_db_config():
    os.environ.setdefault("DASHBOARD_DB_NAME", os.environ.get("WB_PRODUCTS_DB_NAME", "wb_products"))
    return app.read_db_config()


class WBDataImporterDynamic:
    """Импортер данных WB в PostgreSQL с анализом категорий и характеристик"""

    def __init__(
        self,
        db_config,
        mode="replace",
        require_db_backup=True,
        db_backup_dir=DEFAULT_BACKUP_DIR,
        output_dir=DEFAULT_OUTPUT_DIR,
        file_pattern=DEFAULT_FILE_PATTERN,
        rebuild_views=True,
        archive_date="all",
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

        # Общая статистика
        self.all_columns = set()
        self.files_analyzed = 0

        # По файлам
        self.file_analysis = {}
        self.file_categories = {}
        self.file_columns_map = {}

        # По категориям
        self.category_to_columns = defaultdict(set)
        self.category_to_files = defaultdict(set)
        self.category_total_rows = defaultdict(int)
        self.category_file_count = defaultdict(int)

        # Частоты по категориям
        self.category_column_frequency = defaultdict(int)

        # Итоговые множества
        self.common_columns = set()
        self.category_specific_columns = set()
        self.all_categories = set()

        # Кэши
        self.category_cache = {}
        self.attribute_cache = {}

        # Маппинг общих полей -> db columns
        self.fields_mapping = {}

        # План объединения архивных снимков по nmID.
        self.latest_snapshot_by_sku = {}
        self.max_source_snapshot_date = None
        self.existing_skus = set()
        self.backfill_skus = set()
        self.rows_skipped_superseded = 0
        self.rows_skipped_existing = 0

    # =========================================================================
    # CONNECTION
    # =========================================================================
    def connect(self):
        """Подключение к БД"""
        try:
            self.conn = psycopg2.connect(**self.db_config)
            self.conn.autocommit = False
            self.cursor = self.conn.cursor()
            logger.info(f"✅ Подключено к БД: {self.db_config['database']}")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            raise

    def close(self):
        """Закрытие соединения"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
            logger.info("🔌 Соединение с БД закрыто")
        except Exception as e:
            logger.warning(f"Ошибка при закрытии соединения: {e}")

    # =========================================================================
    # HELPERS
    # =========================================================================
    def normalize_db_name(self, col_name: str) -> str:
        """Нормализация имени столбца для PostgreSQL"""
        db_name = col_name.lower().strip()
        db_name = db_name.replace(" ", "_")
        db_name = db_name.replace("/", "_")
        db_name = db_name.replace("(", "")
        db_name = db_name.replace(")", "")

        transliteration = {
            "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
            "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
            "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
            "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
            "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
            ".": "_", ",": "", ":": "", ";": "", '"': "", "'": "", "№": "num",
            "%": "pct", "-": "_"
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
        """Создаёт маппинг общих полей для таблицы products"""
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
        """Очистка значения"""
        if pd.isna(value):
            return None

        if isinstance(value, str):
            value = value.strip()
            if value == "" or value.lower() == "none":
                return None

        return str(value)

    # =========================================================================
    # ANALYSIS
    # =========================================================================
    def analyze_file_structure(self, file_path: Path):
        """
        Анализ структуры одного файла:
        - считает строки
        - проверяет столбец 'Категория продавца'
        - собирает все уникальные категории в файле
        - собирает названия столбцов
        """
        try:
            df = read_products_sheet(file_path)

            columns = set(df.columns)
            total_rows = len(df)
            sku_values = set()
            if "Артикул WB" in columns:
                sku_values = {
                    sku
                    for sku in df["Артикул WB"].map(normalize_product_sku)
                    if sku is not None
                }
            snapshot_date = source_snapshot_date(file_path)

            if "Категория продавца" not in columns:
                logger.warning(f"⚠️ Файл {file_path.name}: НЕТ столбца 'Категория продавца'")
                return {
                    "columns": columns,
                    "categories": [],
                    "total_rows": total_rows,
                    "rows_without_category": total_rows,
                    "missing_category_column": True,
                    "multi_category_file": False,
                    "sku_values": sku_values,
                    "snapshot_date": snapshot_date,
                }

            cat_series = (
                df["Категория продавца"]
                .fillna("")
                .astype(str)
                .str.strip()
            )

            rows_without_category = int((cat_series == "").sum())

            unique_categories = sorted(
                cat_series[cat_series != ""].unique().tolist()
            )

            multi_category_file = len(unique_categories) > 1

            if len(unique_categories) == 0:
                logger.warning(f"⚠️ Файл {file_path.name}: все категории пустые")

            if multi_category_file:
                logger.warning(
                    f"⚠️ Файл {file_path.name}: найдено несколько категорий ({len(unique_categories)}): "
                    f"{', '.join(unique_categories[:5])}"
                    + ("..." if len(unique_categories) > 5 else "")
                )

            return {
                "columns": columns,
                "categories": unique_categories,
                "total_rows": total_rows,
                "rows_without_category": rows_without_category,
                "missing_category_column": False,
                "multi_category_file": multi_category_file,
                "sku_values": sku_values,
                "snapshot_date": snapshot_date,
            }

        except Exception as e:
            logger.warning(f"⚠️ Не удалось прочитать {file_path.name}: {e}")
            return {
                "columns": set(),
                "categories": [],
                "total_rows": 0,
                "rows_without_category": 0,
                "missing_category_column": True,
                "multi_category_file": False,
                "sku_values": set(),
                "snapshot_date": source_snapshot_date(file_path),
            }

    def analyze_all_files(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        """Анализ всех файлов с прогрессом и итоговым коротким отчётом"""
        print("\n" + "=" * 80)
        print("ПЛАН: анализ структуры WB Prodacts")
        print("=" * 80)

        files = discover_source_files(directory_path, file_pattern)
        total_files = len(files)
        print(f"Источник: {directory_path}")
        print(f"Файлов: {total_files}, шаблон: {file_pattern}, лист: {SOURCE_SHEET_NAME}")
        print("Паузы: API-запросов нет; импорт читает локальные XLSX.")
        logger.info("Найдено файлов: %s", total_files)

        total_rows_all = 0
        total_rows_without_category = 0
        files_without_category_column = 0
        multi_category_files = 0
        started_at = time.time()

        for idx, file_path in enumerate(files, 1):
            analysis = self.analyze_file_structure(file_path)
            filename = file_path.name
            try:
                file_key = file_path.relative_to(directory_path).as_posix()
            except ValueError:
                file_key = str(file_path)

            if analysis["columns"]:
                columns = analysis["columns"]
                categories = analysis["categories"]

                self.file_analysis[file_key] = analysis
                self.file_categories[file_key] = categories
                self.file_columns_map[file_key] = columns

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
                    self.category_to_files[category].add(file_key)
                    self.category_total_rows[category] += analysis["total_rows"]

            totals = (
                f"строк {total_rows_all:,}; категорий {len(self.all_categories)}; "
                f"без категории {total_rows_without_category:,}; ошибок 0"
            )
            print(progress_line(idx, total_files, f"анализ: {filename}", totals, started_at), flush=True)

        self.latest_snapshot_by_sku = {}
        source_dates = []
        for analysis in self.file_analysis.values():
            snapshot_date = analysis["snapshot_date"]
            source_dates.append(snapshot_date)
            for sku in analysis["sku_values"]:
                current_date = self.latest_snapshot_by_sku.get(sku)
                if current_date is None or snapshot_date > current_date:
                    self.latest_snapshot_by_sku[sku] = snapshot_date
        self.max_source_snapshot_date = max(source_dates, default=None)

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

        self.category_specific_columns = self.all_columns - self.common_columns

        print("\n" + "=" * 80)
        print("КРАТКИЙ ОТЧЁТ ПО АНАЛИЗУ")
        print("=" * 80)
        print(f"Файлов: {self.files_analyzed}")
        print(f"Строк: {total_rows_all:,}")
        print(f"Категорий: {len(self.all_categories)}")
        print(f"Всего характеристик: {len(self.all_columns)}")
        print(f"Общих характеристик: {len(self.common_columns)}")
        print(f"Категорийных характеристик: {len(self.category_specific_columns)}")
        print(f"Строк без категории: {total_rows_without_category:,}")
        print(f"Файлов без столбца 'Категория продавца': {files_without_category_column}")
        print(f"Многокатегорийных файлов: {multi_category_files}")
        print(f"Уникальных nmID в объединённом источнике: {len(self.latest_snapshot_by_sku):,}")
        print("=" * 80 + "\n")

        self.save_analysis_report(directory_path)
        return self.file_analysis

    def save_analysis_report(self, directory_path):
        """Сохранение расширенного отчёта по анализу в JSON"""
        report = {
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_files": self.files_analyzed,
            "total_categories": len(self.all_categories),
            "total_unique_columns": len(self.all_columns),
            "common_columns_count": len(self.common_columns),
            "category_specific_columns_count": len(self.category_specific_columns),
            "common_columns": sorted(list(self.common_columns)),
            "category_specific_columns": sorted(list(self.category_specific_columns)),
            "all_columns": sorted(list(self.all_columns)),
            "category_column_frequency": dict(self.category_column_frequency),
            "files_details": {
                filename: {
                    "categories": analysis["categories"],
                    "categories_count": len(analysis["categories"]),
                    "total_rows": int(analysis["total_rows"]),
                    "rows_without_category": int(analysis["rows_without_category"]),
                    "columns_count": len(analysis["columns"]),
                    "missing_category_column": analysis["missing_category_column"],
                    "multi_category_file": analysis["multi_category_file"]
                }
                for filename, analysis in self.file_analysis.items()
            },
            "categories": {
                category: {
                    "files_count": self.category_file_count[category],
                    "total_rows": int(self.category_total_rows[category]),
                    "columns_count": len(self.category_to_columns[category]),
                    "columns": sorted(list(self.category_to_columns[category]))
                }
                for category in sorted(self.all_categories)
            }
        }

        report["source_directory"] = str(directory_path)
        report_path = self.output_dir / "structure_analysis_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 Отчёт сохранён: {report_path}")

    # =========================================================================
    # CACHE
    # =========================================================================
    def preload_reference_data(self):
        """Загружаем справочники в память"""
        self.category_cache = {}
        self.attribute_cache = {}

        self.cursor.execute("SELECT category_id, category_name FROM categories")
        for category_id, category_name in self.cursor.fetchall():
            self.category_cache[category_name] = category_id

        self.cursor.execute("SELECT attribute_id, attribute_name FROM attribute_definitions")
        for attribute_id, attribute_name in self.cursor.fetchall():
            self.attribute_cache[attribute_name] = attribute_id

        logger.info(
            f"📚 Загружено в кэш: categories={len(self.category_cache)}, attributes={len(self.attribute_cache)}"
        )

    def get_or_create_category(self, category_name):
        """Получить ID категории или создать новую"""
        if category_name in self.category_cache:
            return self.category_cache[category_name]

        self.cursor.execute(
            """
            INSERT INTO categories (category_name)
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
        """Создать недостающие атрибуты пачкой"""
        new_names = [name for name in attribute_names if name not in self.attribute_cache]
        if not new_names:
            return

        unique_new_names = sorted(set(new_names))

        execute_values(
            self.cursor,
            """
            INSERT INTO attribute_definitions (attribute_name, data_type)
            VALUES %s
            ON CONFLICT (attribute_name) DO NOTHING
            """,
            [(name, "text") for name in unique_new_names],
            page_size=1000,
        )

        self.cursor.execute(
            """
            SELECT attribute_id, attribute_name
            FROM attribute_definitions
            WHERE attribute_name = ANY(%s)
            """,
            (unique_new_names,),
        )

        for attribute_id, attribute_name in self.cursor.fetchall():
            self.attribute_cache[attribute_name] = attribute_id

    # =========================================================================
    # DDL
    # =========================================================================
    def create_support_tables(self):
        """Создаём справочники, если их нет"""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
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
            CREATE TABLE IF NOT EXISTS attribute_definitions (
                attribute_id SERIAL PRIMARY KEY,
                attribute_name TEXT NOT NULL UNIQUE,
                attribute_scope TEXT NOT NULL CHECK (attribute_scope IN ('common', 'category_specific')),
                data_type TEXT NOT NULL DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS common_attributes (
                attribute_id INTEGER PRIMARY KEY REFERENCES attribute_definitions(attribute_id) ON DELETE CASCADE,
                attribute_name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS category_attributes (
                id BIGSERIAL PRIMARY KEY,
                category_id INTEGER NOT NULL REFERENCES categories(category_id) ON DELETE CASCADE,
                attribute_id INTEGER NOT NULL REFERENCES attribute_definitions(attribute_id) ON DELETE CASCADE,
                attribute_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category_id, attribute_id)
            )
            """
        )

        self.conn.commit()

    def save_views(self):
        """Сохраняем определения всех views которые зависят от наших таблиц"""
        self.cursor.execute(
            """
            SELECT table_name, view_definition
            FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name NOT IN ('vw_db_structure', 'vw_db_indexes')
            ORDER BY table_name
            """
        )
        views = self.cursor.fetchall()
        logger.info(f"💾 Сохранено {len(views)} views для восстановления после импорта")
        print(f"💾 Сохранено {len(views)} views: {', '.join(v[0] for v in views)}")
        return views

    def restore_views(self, views):
        """Восстанавливаем все views после пересоздания таблиц"""
        if not views:
            return

        print(f"\n🔄 Восстановление {len(views)} views...")
        restored = 0
        failed = 0

        for view_name, view_definition in views:
            try:
                self.cursor.execute(f"CREATE OR REPLACE VIEW {view_name} AS {view_definition}")
                self.conn.commit()
                print(f"  ✅ {view_name}")
                logger.info(f"✅ View восстановлен: {view_name}")
                restored += 1
            except Exception as e:
                self.conn.rollback()
                print(f"  ❌ {view_name}: {e}")
                logger.error(f"❌ Ошибка восстановления view {view_name}: {e}")
                failed += 1

        print(f"🔄 Восстановлено: {restored}, ошибок: {failed}")

    def create_dynamic_products_table(self, fields_mapping):
        """Создание таблиц"""
        print(f"\n📊 Режим работы: {self.mode}")

        if self.mode == "replace":
            # Сохраняем все views перед дропом таблиц
            saved_views = self.save_views()

            print("🗑️ Удаление старых таблиц...")
            self.cursor.execute("DROP TABLE IF EXISTS product_attributes CASCADE")
            self.cursor.execute("DROP TABLE IF EXISTS products CASCADE")
            self.cursor.execute("DROP TABLE IF EXISTS category_attributes CASCADE")
            self.cursor.execute("DROP TABLE IF EXISTS common_attributes CASCADE")
            self.cursor.execute("DROP TABLE IF EXISTS attribute_definitions CASCADE")
            self.cursor.execute("DROP TABLE IF EXISTS categories CASCADE")
            self.conn.commit()
            print("✅ Старые таблицы удалены")
            self.create_support_tables()

        columns_sql = ["product_id BIGSERIAL PRIMARY KEY"]

        for _, db_col in sorted(fields_mapping.items()):
            columns_sql.append(f"{db_col} TEXT")

        columns_sql.extend(
            [
                "category_id INTEGER REFERENCES categories(category_id)",
                "seller_category_name TEXT",
                "import_file VARCHAR(500)",
                "source_row_num INTEGER",
                "imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ]
        )

        create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS products (
                {", ".join(columns_sql)}
            )
        """
        self.cursor.execute(create_table_sql)

        if self.mode != "replace":
            for db_col in sorted(fields_mapping.values()):
                self.cursor.execute(
                    f"ALTER TABLE products ADD COLUMN IF NOT EXISTS {db_col} TEXT"
                )

        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS product_attributes (
                id BIGSERIAL PRIMARY KEY,
                product_id BIGINT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
                attribute_id INTEGER NOT NULL REFERENCES attribute_definitions(attribute_id),
                value_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(product_id, attribute_id)
            )
            """
        )

        self.conn.commit()
        print(f"✅ Таблица products создана с {len(fields_mapping)} общими столбцами")

        # Сохраняем views для восстановления после импорта
        if self.mode == "replace":
            self._saved_views = saved_views
        else:
            self._saved_views = []

    def sync_metadata_to_db(self):
        """Запись категорий и характеристик в БД после анализа"""
        print("\n📦 Запись категорий и характеристик в БД...")
        logger.info("Запись категорий и характеристик в БД...")

        # 1. Категории
        for category in sorted(self.all_categories):
            category_id = self.get_or_create_category(category)

            self.cursor.execute(
                """
                UPDATE categories
                SET files_count = %s,
                    total_rows = %s
                WHERE category_id = %s
                """,
                (
                    int(self.category_file_count.get(category, 0)),
                    int(self.category_total_rows.get(category, 0)),
                    category_id,
                )
            )

        # 2. Все атрибуты
        all_attributes = []
        for attr_name in sorted(self.all_columns):
            scope = "common" if attr_name in self.common_columns else "category_specific"
            all_attributes.append((attr_name, scope, "text"))

        if all_attributes:
            execute_values(
                self.cursor,
                """
                INSERT INTO attribute_definitions (attribute_name, attribute_scope, data_type)
                VALUES %s
                ON CONFLICT (attribute_name) DO UPDATE
                SET attribute_scope = EXCLUDED.attribute_scope,
                    data_type = EXCLUDED.data_type
                """,
                all_attributes,
                page_size=1000,
            )

        # Обновляем кэш атрибутов
        self.cursor.execute("SELECT attribute_id, attribute_name FROM attribute_definitions")
        self.attribute_cache = {name: attr_id for attr_id, name in self.cursor.fetchall()}

        # 3. Таблица общих атрибутов
        if self.common_columns:
            common_rows = [
                (self.attribute_cache[attr_name], attr_name)
                for attr_name in sorted(self.common_columns)
                if attr_name in self.attribute_cache
            ]

            execute_values(
                self.cursor,
                """
                INSERT INTO common_attributes (attribute_id, attribute_name)
                VALUES %s
                ON CONFLICT (attribute_id) DO UPDATE
                SET attribute_name = EXCLUDED.attribute_name
                """,
                common_rows,
                page_size=1000,
            )

        # 4. Таблица категорийных характеристик
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
                INSERT INTO category_attributes (category_id, attribute_id, attribute_name)
                VALUES %s
                ON CONFLICT (category_id, attribute_id) DO UPDATE
                SET attribute_name = EXCLUDED.attribute_name
                """,
                category_attr_rows,
                page_size=5000,
            )

        self.conn.commit()
        print("✅ Категории и характеристики записаны в БД")
        logger.info("✅ Категории и характеристики записаны в БД")

    def drop_indexes(self):
        """Удаляем индексы перед загрузкой"""
        print("🗑️ Удаление индексов для ускорения загрузки...")
        try:
            self.cursor.execute("DROP INDEX IF EXISTS idx_products_import_file_row")
            self.cursor.execute("DROP INDEX IF EXISTS idx_products_wb_article")
            self.cursor.execute("DROP INDEX IF EXISTS idx_products_category_id")
            self.cursor.execute("DROP INDEX IF EXISTS idx_product_attributes_product")
            self.cursor.execute("DROP INDEX IF EXISTS idx_product_attributes_attribute")
            self.conn.commit()
            print("✅ Индексы удалены")
        except Exception as e:
            logger.warning(f"Ошибка при удалении индексов: {e}")
            self.conn.rollback()

    def create_indexes(self):
        """Создаём индексы после загрузки"""
        print("\n📊 Создание индексов...")
        try:
            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_products_import_file_row
                ON products(import_file, source_row_num)
                """
            )

            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_products_category_id
                ON products(category_id)
                """
            )

            if "artikul_wb" in self.fields_mapping.values():
                self.cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_products_wb_article
                    ON products(artikul_wb)
                    """
                )

            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_product_attributes_product
                ON product_attributes(product_id)
                """
            )
            self.cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_product_attributes_attribute
                ON product_attributes(attribute_id)
                """
            )

            self.conn.commit()
            print("✅ Индексы созданы")
        except Exception as e:
            logger.error(f"Ошибка при создании индексов: {e}")
            self.conn.rollback()

    # =========================================================================
    # IMPORT
    # =========================================================================
    def prepare_backfill_scope(self):
        """Зафиксировать nmID, отсутствующие в БД до начала догрузки."""
        self.cursor.execute("SELECT to_regclass('public.products')")
        products_table = self.cursor.fetchone()[0]
        if products_table:
            self.cursor.execute(
                "SELECT DISTINCT artikul_wb FROM products WHERE artikul_wb IS NOT NULL"
            )
            self.existing_skus = {
                sku
                for (value,) in self.cursor.fetchall()
                if (sku := normalize_product_sku(value)) is not None
            }
        else:
            self.existing_skus = set()

        self.backfill_skus = set(self.latest_snapshot_by_sku) - self.existing_skus
        print("\n" + "=" * 80)
        print("ПЛАН: безопасная догрузка отсутствующих nmID")
        print("=" * 80)
        print(f"nmID в БД до импорта: {len(self.existing_skus):,}")
        print(f"nmID в объединённом источнике: {len(self.latest_snapshot_by_sku):,}")
        print(f"nmID к догрузке: {len(self.backfill_skus):,}")
        print("Повторный запуск идемпотентен: уже существующие nmID не добавляются.")

    def filter_dataframe_for_import(self, df, file_path):
        """Оставить самый свежий доступный снимок каждого nmID."""
        if df.empty or self.mode == "append":
            return df
        if "Артикул WB" not in df.columns:
            if self.mode == "backfill":
                self.rows_skipped_existing += len(df)
                return df.iloc[0:0].copy()
            if source_snapshot_date(file_path) != self.max_source_snapshot_date:
                self.rows_skipped_superseded += len(df)
                return df.iloc[0:0].copy()
            return df

        snapshot_date = source_snapshot_date(file_path)
        sku_series = df["Артикул WB"].map(normalize_product_sku)
        latest_mask = pd.Series(False, index=df.index)
        for row_index, sku in sku_series.items():
            if sku is None:
                keep = (
                    self.mode != "backfill"
                    and snapshot_date == self.max_source_snapshot_date
                )
            else:
                keep = self.latest_snapshot_by_sku.get(sku) == snapshot_date
            latest_mask.at[row_index] = keep

        self.rows_skipped_superseded += int((~latest_mask).sum())

        if self.mode == "backfill":
            backfill_mask = sku_series.isin(self.backfill_skus)
            self.rows_skipped_existing += int((latest_mask & ~backfill_mask).sum())
            latest_mask &= backfill_mask

        return df.loc[latest_mask].reset_index(drop=True)

    def build_product_rows(self, df, file_path):
        """Подготовка данных products с категорией из строки"""
        product_rows = []

        for row_idx, row in df.iterrows():
            row_data = {}

            for wb_col, db_col in self.fields_mapping.items():
                if wb_col in row.index:
                    row_data[db_col] = self.clean_value(row[wb_col])

            seller_category_name = None
            if "Категория продавца" in row.index:
                seller_category_name = self.clean_value(row["Категория продавца"])

            category_id = self.get_or_create_category(seller_category_name) if seller_category_name else None

            row_data["category_id"] = category_id
            row_data["seller_category_name"] = seller_category_name
            row_data["import_file"] = file_path.name
            row_data["source_row_num"] = int(row_idx)

            product_rows.append(row_data)

        return product_rows

    def bulk_insert_products(self, batch_data):
        """Bulk insert товаров"""
        if not batch_data:
            return []

        columns = list(batch_data[0].keys())

        sql = f"""
            INSERT INTO products ({", ".join(columns)})
            VALUES %s
            RETURNING product_id, import_file, source_row_num
        """

        values = [tuple(row[col] for col in columns) for row in batch_data]

        execute_values(
            self.cursor,
            sql,
            values,
            page_size=min(1000, len(values)),
        )

        return self.cursor.fetchall()

    def bulk_insert_attributes(self, attributes_batch):
        """Bulk insert атрибутов"""
        if not attributes_batch:
            return

        execute_values(
            self.cursor,
            """
            INSERT INTO product_attributes (product_id, attribute_id, value_text)
            VALUES %s
            ON CONFLICT (product_id, attribute_id)
            DO UPDATE SET value_text = EXCLUDED.value_text
            """,
            attributes_batch,
            page_size=min(5000, len(attributes_batch)),
        )

    def import_file(self, file_path):
        """Импорт одного файла"""
        try:
            df = read_products_sheet(file_path)
            df = self.filter_dataframe_for_import(df, file_path)

            if df.empty:
                return 0, 0

            self.get_or_create_attributes_bulk(df.columns.tolist())

            product_rows = self.build_product_rows(df, file_path)

            inserted_map = {}
            imported = 0
            errors = 0
            batch_size = BATCH_SIZE_PRODUCTS

            for start in range(0, len(product_rows), batch_size):
                chunk = product_rows[start:start + batch_size]
                returned = self.bulk_insert_products(chunk)

                for product_id, import_file, source_row_num in returned:
                    inserted_map[int(source_row_num)] = int(product_id)

                imported += len(returned)

            # Пишем ВСЕ колонки в product_attributes — и общие и категорийные
            # Признак товара — artikul_wb, поэтому хранить значения нужно для каждого product_id
            # Общие характеристики дублируются в product_attributes для симметричного скоринга
            all_attribute_columns = [col for col in df.columns if col in self.attribute_cache]
            attributes_batch = []

            for row_idx, row in df.iterrows():
                product_id = inserted_map.get(int(row_idx))
                if not product_id:
                    errors += 1
                    logger.error(f"Не найден product_id для файла={file_path.name}, row_idx={row_idx}")
                    continue

                for col_name in all_attribute_columns:
                    value = self.clean_value(row[col_name])
                    if value is None:
                        continue

                    attr_id = self.attribute_cache.get(col_name)
                    if not attr_id:
                        errors += 1
                        logger.error(f"Не найден attribute_id для атрибута '{col_name}'")
                        continue

                    attributes_batch.append((product_id, attr_id, value))

                    if len(attributes_batch) >= BATCH_SIZE_ATTRIBUTES:
                        self.bulk_insert_attributes(attributes_batch)
                        attributes_batch = []

            if attributes_batch:
                self.bulk_insert_attributes(attributes_batch)

            self.conn.commit()
            return imported, errors

        except Exception as e:
            logger.error(f"Критическая ошибка при импорте файла {file_path.name}: {e}")
            self.conn.rollback()
            return 0, 1

    def import_all_files(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        """Импорт всех файлов"""
        print("\n" + "=" * 80)
        print("ПЛАН: импорт WB товаров и характеристик в БД")
        print("=" * 80)

        files = discover_source_files(directory_path, file_pattern)
        total_files = len(files)
        print(f"Источник: {directory_path}")
        print(f"Файлов к обработке: {total_files}, batch товаров: {BATCH_SIZE_PRODUCTS}, batch характеристик: {BATCH_SIZE_ATTRIBUTES}")
        print("Паузы: API-запросов нет; импорт читает локальные XLSX.")

        total_imported = 0
        total_errors = 0
        started_at = time.time()

        for idx, file_path in enumerate(files, 1):
            imported, errors = self.import_file(file_path)
            total_imported += imported
            total_errors += errors
            totals = f"импортировано {total_imported:,}; ошибок {total_errors:,}"
            print(progress_line(idx, total_files, f"импорт: {file_path.name}", totals, started_at), flush=True)
            print(
                f"[{idx}/{total_files}] {file_path.name}: imported {imported:,}, errors {errors}",
                flush=True,
            )
            logger.info("[%s/%s] %s: импортировано %s, ошибок %s", idx, total_files, file_path.name, imported, errors)

        print()
        print("=" * 80)
        print("ИМПОРТ ЗАВЕРШЁН")
        print("=" * 80)
        print(f"  • Всего импортировано: {total_imported:,} товаров")
        print(f"  • Всего ошибок: {total_errors:,}")
        print(f"  • Строк пропущено как устаревшие снимки: {self.rows_skipped_superseded:,}")
        print(f"  • Строк пропущено как уже существующие nmID: {self.rows_skipped_existing:,}")
        success = (
            total_imported / (total_imported + total_errors) * 100
            if (total_imported + total_errors) > 0
            else 0
        )
        print(f"  • Успешность: {success:.2f}%")
        print("=" * 80 + "\n")

        return total_imported, total_errors

    # =========================================================================
    # LOG
    # =========================================================================
    def save_import_log(self, directory_path, total_imported, total_errors, start_time, end_time):
        """Сохранение итогового лога рядом со скриптом"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = self.output_dir / f"import_log_{timestamp}.txt"

        duration = end_time - start_time
        duration_str = str(duration).split(".")[0]

        success = (
            total_imported / (total_imported + total_errors) * 100
            if (total_imported + total_errors) > 0
            else 0
        )

        total_rows_all = sum(v["total_rows"] for v in self.file_analysis.values())
        total_rows_without_category = sum(v["rows_without_category"] for v in self.file_analysis.values())
        multi_category_files = sum(1 for v in self.file_analysis.values() if v["multi_category_file"])

        content = f"""
================================================================================
ЛОГ ИМПОРТА ДАННЫХ WB В POSTGRESQL
================================================================================

Дата и время начала: {start_time.strftime("%Y-%m-%d %H:%M:%S")}
Дата и время окончания: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
Длительность: {duration_str}

Режим работы: {self.mode}
База данных: {self.db_config['database']}
Папка с данными: {directory_path}
Папка скрипта: {SCRIPT_DIR}
Папка артефактов: {self.output_dir}
Бэкап БД: {self.db_backup_path or 'не создавался'}

================================================================================
РЕЗУЛЬТАТЫ АНАЛИЗА
================================================================================

Всего файлов проанализировано: {self.files_analyzed}
Всего строк: {total_rows_all:,}
Уникальных категорий: {len(self.all_categories)}
Строк без категории: {total_rows_without_category:,}
Многокатегорийных файлов: {multi_category_files}

Всего уникальных столбцов: {len(self.all_columns)}
Общих характеристик: {len(self.common_columns)}
Категорийно-специфичных характеристик: {len(self.category_specific_columns)}

================================================================================
РЕЗУЛЬТАТЫ ИМПОРТА
================================================================================

Всего импортировано товаров: {total_imported:,}
Всего ошибок: {total_errors:,}
Успешность: {success:.2f}%

================================================================================
ТОП КАТЕГОРИЙ ПО ОБЪЁМУ
================================================================================
"""

        top_categories = sorted(
            self.category_total_rows.items(),
            key=lambda x: x[1],
            reverse=True
        )[:30]

        for i, (cat, count) in enumerate(top_categories, 1):
            content += f"\n{i:2d}. {cat}: {count:,} строк"

        content += """

================================================================================
КОНЕЦ ЛОГА
================================================================================
"""

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"💾 Лог сохранён: {log_path}")

    # =========================================================================
    # BACKUP
    # =========================================================================
    def create_database_backup(self):
        pg_dump = find_pg_dump()
        if not pg_dump:
            raise RuntimeError(
                "pg_dump не найден. Установите PostgreSQL client tools или задайте переменную PG_DUMP; "
                "без бэкапа replace-импорт не запускается."
            )

        database = self.db_config.get("database")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.db_backup_dir / f"{safe_backup_part(database)}_before_wb_assortment_{timestamp}.dump"
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
        script_path = PROJECT_ROOT / "scripts" / "rebuild_wb_assortment_views.py"
        if not script_path.exists():
            raise FileNotFoundError(f"View rebuild script not found: {script_path}")

        command = [sys.executable, "-X", "utf8", "-u", str(script_path)]
        env = os.environ.copy()
        env["DASHBOARD_DB_NAME"] = str(self.db_config.get("database") or "wb_products")
        print("\n" + "=" * 80)
        print("ПЛАН: пересборка WB assortment views")
        print("=" * 80)
        print(f"Скрипт: {script_path}")
        print(f"База данных: {env['DASHBOARD_DB_NAME']}")
        print("ПРОГРЕСС: 0/1 (0.0%) | витрины WB ассортимента | запуск rebuild | ETA -", flush=True)
        completed = subprocess.run(command, env=env)
        if completed.returncode != 0:
            raise RuntimeError(f"View rebuild failed with code {completed.returncode}")
        print("ПРОГРЕСС: 1/1 (100.0%) | витрины WB ассортимента | rebuild готов | ETA 0s", flush=True)

    # =========================================================================
    # RUN
    # =========================================================================
    def run(self, directory_path, file_pattern=DEFAULT_FILE_PATTERN):
        """Полный цикл"""
        start_time = datetime.now()
        total_imported = 0
        total_errors = 0

        print("ПЛАН: полный импорт ассортимента WB")
        print(f"Источник: {directory_path}")
        print(f"Режим: {self.mode}")
        print(f"Бэкап перед записью: {'да' if self.require_db_backup else 'нет'}")

        prepared_directory = prepare_import_source(directory_path, file_pattern, self.output_dir, self.archive_date)
        if Path(prepared_directory) != Path(directory_path):
            print(f"Рабочая папка импорта после распаковки: {prepared_directory}")

        self.analyze_all_files(prepared_directory, file_pattern)
        self.create_common_fields_mapping()

        if self.require_db_backup:
            self.create_database_backup()
        else:
            print("ВНИМАНИЕ: импорт запущен без бэкапа по явному --skip-db-backup")

        self.connect()

        try:
            if self.mode == "backfill":
                self.prepare_backfill_scope()

            self.create_support_tables()
            self.create_dynamic_products_table(self.fields_mapping)
            self.preload_reference_data()

            self.sync_metadata_to_db()

            self.drop_indexes()
            total_imported, total_errors = self.import_all_files(prepared_directory, file_pattern)
            self.create_indexes()
            if total_errors:
                raise RuntimeError(
                    f"Импорт завершён с ошибками файлов: {total_errors}; "
                    "витрины не пересобраны, повторный backfill безопасен."
                )

            if self.rebuild_views:
                self.rebuild_assortment_views()
            else:
                print("Пересборка WB assortment views пропущена по --skip-views")

            if hasattr(self, '_saved_views') and self._saved_views:
                self.restore_views(self._saved_views)

        finally:
            self.close()

        end_time = datetime.now()
        self.save_import_log(directory_path, total_imported, total_errors, start_time, end_time)
        print(
            f"ИТОГ: импортировано {total_imported:,}; ошибок {total_errors:,}; "
            f"устаревших строк пропущено {self.rows_skipped_superseded:,}; "
            f"существующих строк пропущено {self.rows_skipped_existing:,}; "
            f"backup={self.db_backup_path or 'нет'}; elapsed {format_duration((end_time - start_time).total_seconds())}",
            flush=True,
        )



def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    db_config = dashboard_db_config()
    importer = WBDataImporterDynamic(
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
    except Exception as e:
        logger.error("Критическая ошибка: %s", e)
        raise

    logger.info("Готово!")


if __name__ == "__main__":
    main()
