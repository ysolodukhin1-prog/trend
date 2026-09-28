#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Read-only structural audit of local marketplace export files for all clients."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = PROJECT_ROOT / "ozon_category_dashboard"
sys.path.insert(0, str(DASHBOARD_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import app  # noqa: E402


XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsb", ".csv", ".tsv", ".zip"}


@dataclass(frozen=True)
class SourceRoot:
    client: str
    path: str
    import_keys: tuple[str, ...]


@dataclass(frozen=True)
class FileSchema:
    client: str
    source: str
    import_keys: tuple[str, ...]
    file: str
    modified_at: str
    size_bytes: int
    extension: str
    fingerprint: str
    family: str
    sheets: tuple[str, ...]
    header_rows: tuple[int | None, ...]
    headers: tuple[tuple[str, ...], ...]
    issue: str | None = None


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def short_hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_schema_label(value: object) -> str:
    """Remove dates and run-specific numbers while retaining header semantics."""
    result = normalize_text(value).lower()
    result = re.sub(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", "<date>", result)
    result = re.sub(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b", "<date>", result)
    result = re.sub(r"\b\d+(?:[.,]\d+)?%?\b", "#", result)
    return result


def column_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - 64
    return result - 1


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.iter(XML_NS + "t")) for item in root]


def workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
        for rel in rels.findall(PACKAGE_REL_NS + "Relationship")
    }
    sheets = workbook.find(XML_NS + "sheets")
    if sheets is None:
        return []
    result = []
    for sheet in sheets:
        target = targets.get(sheet.attrib.get(REL_NS + "id", ""), "")
        if target and not target.startswith("xl/"):
            target = "xl/" + target
        result.append((sheet.attrib.get("name", ""), target))
    return result


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(XML_NS + "t"))
    value = cell.find(XML_NS + "v")
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    return value.text


def sheet_header(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
    *,
    max_rows: int = 12,
    max_cols: int = 80,
) -> tuple[int | None, tuple[str, ...]]:
    candidates: list[tuple[int, int, int, tuple[str, ...]]] = []
    with archive.open(sheet_path) as stream:
        for _, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag != XML_NS + "row":
                continue
            row_num = int(elem.attrib.get("r", "0"))
            if row_num > max_rows:
                elem.clear()
                break
            values = [""] * max_cols
            for cell in elem.findall(XML_NS + "c"):
                index = column_index(cell.attrib.get("r", ""))
                if 0 <= index < max_cols:
                    values[index] = normalize_text(cell_value(cell, shared_strings))
            while values and not values[-1]:
                values.pop()
            non_empty = sum(bool(value) for value in values)
            text_cells = sum(
                bool(value) and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?%?", value)
                for value in values
            )
            if non_empty >= 2:
                candidates.append((text_cells, non_empty, row_num, tuple(values)))
            elem.clear()
    if not candidates:
        return None, ()
    qualified = [
        item for item in candidates
        if item[0] >= 3 and item[0] / max(item[1], 1) >= 0.6
    ]
    selected = min(qualified, key=lambda item: item[2]) if qualified else max(
        candidates, key=lambda item: (item[0], item[1], -item[2])
    )
    _, _, row_num, header = selected
    return row_num, header


def classify_family(headers: tuple[tuple[str, ...], ...], sheets: tuple[str, ...]) -> str:
    flat = {value for row in headers for value in row if value}
    if {"ID", "Название", "Дневной бюджет, ₽", "Заказы post-view, шт"} <= flat:
        return "ozon_media_legacy"
    if {"ID", "Название кампании", "Тип оплаты", "Продано товаров после просмотра"} <= flat:
        return "ozon_media_current"
    if {"ID кампании", "Места размещения", "Стратегия", "Добавления в корзину"} <= flat:
        return "ozon_product_advertising"
    if "Статистика кампаний" in sheets:
        return "wb_advertising"
    if "Statistics" in sheets or "Union" in sheets:
        return "ozon_product_advertising"
    if any("ворон" in value.lower() for value in flat):
        return "funnel"
    if any("остат" in value.lower() for value in flat):
        return "stock"
    return "unknown"


def inspect_xlsx_stream(stream: BinaryIO) -> tuple[str, tuple[str, ...], tuple[int | None, ...], tuple[tuple[str, ...], ...], str | None]:
    try:
        with zipfile.ZipFile(stream, "r") as archive:
            sheets = workbook_sheets(archive)
            shared_strings = read_shared_strings(archive)
            names = []
            header_rows = []
            headers = []
            for name, sheet_path in sheets:
                names.append(name)
                row_num, header = sheet_header(archive, sheet_path, shared_strings)
                header_rows.append(row_num)
                headers.append(header)
    except Exception as exc:
        return "unreadable_xlsx", (), (), (), f"{type(exc).__name__}: {str(exc)[:240]}"
    family = classify_family(tuple(headers), tuple(names))
    return family, tuple(names), tuple(header_rows), tuple(headers), None


def inspect_delimited_bytes(data: bytes, extension: str) -> tuple[str, tuple[str, ...], tuple[int | None, ...], tuple[tuple[str, ...], ...], str | None]:
    decoded = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        return "unreadable_delimited", (), (), (), "Encoding not recognized"
    sample = decoded[:10000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if extension == ".tsv" else ";"
    rows = list(csv.reader(io.StringIO(decoded), delimiter=delimiter))[:12]
    candidates = []
    for idx, row in enumerate(rows, 1):
        normalized = [normalize_text(value) for value in row]
        non_empty = sum(bool(value) for value in normalized)
        text_cells = sum(
            bool(value) and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?%?", value)
            for value in normalized
        )
        if non_empty >= 2:
            candidates.append((text_cells, non_empty, idx, row))
    if not candidates:
        return "delimited", ("data",), (None,), ((),), None
    qualified = [
        item for item in candidates
        if item[0] >= 3 and item[0] / max(item[1], 1) >= 0.6
    ]
    selected = min(qualified, key=lambda item: item[2]) if qualified else max(
        candidates, key=lambda item: (item[0], item[1], -item[2])
    )
    _, _, row_num, row = selected
    header = tuple(normalize_text(value) for value in row)
    family = classify_family((header,), ("data",))
    return family, ("data",), (row_num,), (header,), None


def inspect_file(root: SourceRoot, file_path: Path) -> FileSchema:
    extension = file_path.suffix.lower()
    issue = None
    family = "opaque"
    sheets: tuple[str, ...] = ()
    header_rows: tuple[int | None, ...] = ()
    headers: tuple[tuple[str, ...], ...] = ()
    try:
        if extension == ".xlsx":
            with file_path.open("rb") as stream:
                family, sheets, header_rows, headers, issue = inspect_xlsx_stream(stream)
        elif extension in {".csv", ".tsv"}:
            family, sheets, header_rows, headers, issue = inspect_delimited_bytes(file_path.read_bytes(), extension)
        elif extension == ".zip":
            with zipfile.ZipFile(file_path, "r") as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                xlsx_members = [name for name in members if name.lower().endswith(".xlsx")]
                csv_members = [name for name in members if name.lower().endswith((".csv", ".tsv"))]
                if xlsx_members:
                    family, sheets, header_rows, headers, issue = inspect_xlsx_stream(io.BytesIO(archive.read(xlsx_members[0])))
                elif csv_members:
                    member = csv_members[0]
                    ext = Path(member).suffix.lower()
                    family, sheets, header_rows, headers, issue = inspect_delimited_bytes(archive.read(member), ext)
                else:
                    family = "zip_other"
                    sheets = tuple(sorted(members)[:20])
        elif extension == ".xlsb":
            family = "xlsb_opaque"
            issue = "XLSB header inspection is unavailable; tracked by file size/date only"
    except Exception as exc:
        family = "unreadable"
        issue = f"{type(exc).__name__}: {str(exc)[:240]}"
    normalized_sheets = tuple(
        re.sub(r"\d+", "#", normalize_text(name).lower()) for name in sheets
    )
    normalized_headers = tuple(
        tuple(normalize_schema_label(value) for value in row)
        for row in headers
    )
    payload = {
        "extension": extension,
        "family": family,
        "sheets": normalized_sheets,
        "header_rows": header_rows,
        "headers": normalized_headers,
    }
    stat = file_path.stat()
    return FileSchema(
        client=root.client,
        source=root.path,
        import_keys=root.import_keys,
        file=str(file_path),
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        size_bytes=stat.st_size,
        extension=extension,
        fingerprint=short_hash(payload),
        family=family,
        sheets=sheets,
        header_rows=header_rows,
        headers=headers,
        issue=issue,
    )


def registry_sources() -> list[SourceRoot]:
    groups = {
        "gloria_jeans": app.ADMIN_IMPORTS,
        "sportmaster": app.SPORTMASTER_ADMIN_IMPORTS,
        "boiron": app.BOIRON_ADMIN_IMPORTS,
        "km_trade": app.KM_TRADE_ADMIN_IMPORTS,
    }
    collected: dict[tuple[str, str], set[str]] = defaultdict(set)
    for client, specs in groups.items():
        for key, spec in specs.items():
            source = str(spec.get("source") or "")
            for part in (item.strip() for item in source.split(";")):
                if not re.match(r"^[A-Za-z]:\\", part):
                    continue
                candidate = Path(part)
                if candidate.exists():
                    collected[(client, str(candidate))].add(key)
    try:
        from konstex_paths import (
            KONSTEX_WB_ADVERT_DIR,
            KONSTEX_WB_FINANCE_DIR,
            KONSTEX_WB_FUNNEL_DIR,
            KONSTEX_WB_STOCK_DIR,
        )
        for key, path in {
            "konstex_wb_advertising": KONSTEX_WB_ADVERT_DIR,
            "konstex_wb_finance": KONSTEX_WB_FINANCE_DIR,
            "konstex_wb_funnel": KONSTEX_WB_FUNNEL_DIR,
            "konstex_wb_stock": KONSTEX_WB_STOCK_DIR,
        }.items():
            if Path(path).exists():
                collected[("konstex", str(path))].add(key)
    except ImportError:
        pass
    return [
        SourceRoot(client, path, tuple(sorted(keys)))
        for (client, path), keys in sorted(collected.items())
    ]


def source_files(source: SourceRoot, limit: int) -> list[Path]:
    path = Path(source.path)
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    files = [
        item for item in path.rglob("*")
        if item.is_file()
        and item.suffix.lower() in SUPPORTED_EXTENSIONS
        and not item.name.startswith("~$")
    ]
    files.sort(key=lambda item: (item.stat().st_mtime, str(item).lower()), reverse=True)
    return files[:limit]


def change_summary(rows: list[FileSchema]) -> dict[str, object]:
    ordered = sorted(rows, key=lambda row: row.modified_at)
    transitions = []
    for previous, current in zip(ordered, ordered[1:]):
        if previous.fingerprint != current.fingerprint:
            transitions.append({
                "from_file": previous.file,
                "to_file": current.file,
                "from_family": previous.family,
                "to_family": current.family,
                "from_fingerprint": previous.fingerprint,
                "to_fingerprint": current.fingerprint,
            })
    fingerprints = defaultdict(list)
    for row in ordered:
        fingerprints[row.fingerprint].append(row)
    return {
        "files_checked": len(rows),
        "distinct_fingerprints": len(fingerprints),
        "latest_family": ordered[-1].family if ordered else None,
        "latest_fingerprint": ordered[-1].fingerprint if ordered else None,
        "transitions": transitions,
        "issues": [row.file + ": " + row.issue for row in ordered if row.issue],
    }


def markdown_report(payload: dict[str, object]) -> str:
    lines = [
        "# Аудит форматов клиентских выгрузок",
        "",
        f"Сформирован: {payload['generated_at']}",
        f"Источников: {payload['sources_checked']}; файлов: {payload['files_checked']}; ошибок чтения: {payload['read_errors']}",
        "",
        "| Клиент | Источник | Файлов | Схем | Последнее семейство | Переходов | Ошибки |",
        "|---|---|---:|---:|---|---:|---:|",
    ]
    for source in payload["sources"]:
        summary = source["summary"]
        lines.append(
            f"| {source['client']} | `{source['path']}` | {summary['files_checked']} | "
            f"{summary['distinct_fingerprints']} | {summary['latest_family'] or '—'} | "
            f"{len(summary['transitions'])} | {len(summary['issues'])} |"
        )
    lines.extend(["", "## Изменения, требующие внимания", ""])
    findings = payload["findings"]
    if not findings:
        lines.append("Несовместимых изменений семейства отчёта в проверенном окне не обнаружено.")
    else:
        for finding in findings:
            lines.append(
                f"- **{finding['client']} / {', '.join(finding['import_keys'])}**: "
                f"{finding['from_family']} → {finding['to_family']} · `{finding['to_file']}`"
            )
    compatible = payload.get("compatible_variants", [])
    lines.extend(["", "## Совместимые варианты заголовков", ""])
    if not compatible:
        lines.append("Дополнительных вариантов заголовков не обнаружено.")
    else:
        lines.append(
            f"Обнаружено {len(compatible)} переходов между вариантами заголовков внутри одного семейства; "
            "они сохранены в JSON для аудита и не считаются поломкой импортёра."
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-source", type=int, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "data-quality" / "export-schema-audit",
    )
    args = parser.parse_args()

    roots = registry_sources()
    print(
        f"ПЛАН: аудит выгрузок | источников {len(roots)} | до {args.per_source} файлов на источник | "
        "только чтение | API не используются",
        flush=True,
    )
    started = time.perf_counter()
    all_rows: list[FileSchema] = []
    source_payloads = []
    findings = []
    compatible_variants = []
    for index, root in enumerate(roots, start=1):
        files = source_files(root, args.per_source)
        rows = [inspect_file(root, file_path) for file_path in files]
        all_rows.extend(rows)
        summary = change_summary(rows)
        source_payloads.append({**asdict(root), "summary": summary})
        for transition in summary["transitions"]:
            transition_row = {"client": root.client, "import_keys": root.import_keys, **transition}
            if (
                transition["from_family"] != transition["to_family"]
                and "unknown" not in {transition["from_family"], transition["to_family"]}
                and not any("initial_load" in key for key in root.import_keys)
            ):
                findings.append(transition_row)
            elif (
                transition["from_family"] == transition["to_family"]
                and transition["from_family"] not in {"unknown", "opaque", "xlsb_opaque"}
            ):
                compatible_variants.append(transition_row)
        elapsed = time.perf_counter() - started
        eta = elapsed / index * (len(roots) - index) if index else 0
        print(
            f"ПРОГРЕСС: {index}/{len(roots)} ({index / len(roots) * 100:.1f}%) | "
            f"{root.client}:{','.join(root.import_keys)} | files {len(rows)} | "
            f"schemas {summary['distinct_fingerprints']} | errors {len(summary['issues'])} | ETA {eta:.1f}s",
            flush=True,
        )

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources_checked": len(roots),
        "files_checked": len(all_rows),
        "read_errors": sum(bool(row.issue and row.family.startswith(("unreadable", "xlsb"))) for row in all_rows),
        "sources": source_payloads,
        "findings": findings,
        "compatible_variants": compatible_variants,
        "files": [asdict(row) for row in all_rows],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "latest.json"
    md_path = args.output_dir / "latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(payload), encoding="utf-8")
    print(
        f"ИТОГ: источников {len(roots)} | файлов {len(all_rows)} | требует внимания {len(findings)} | "
        f"ошибок чтения {payload['read_errors']} | elapsed {time.perf_counter() - started:.1f}s | "
        f"JSON {json_path} | Markdown {md_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
