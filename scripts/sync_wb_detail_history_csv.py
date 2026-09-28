from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from psycopg2.extras import execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ozon_category_dashboard"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    import pulse_vps_admin

    pulse_vps_admin.configure_scope()
except (ImportError, FileNotFoundError, RuntimeError):
    pass

import app  # noqa: E402
import sync_km_wb_api as wb  # noqa: E402


def _report(value: Any, report_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if str(value.get("id") or value.get("downloadId") or "") == report_id:
            return value
        for child in value.values():
            if found := _report(child, report_id):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _report(child, report_id):
                return found
    return None


def _download(token: str, start: date, end: date, report_id: str | None) -> tuple[str, bytes]:
    report_id = report_id or str(uuid.uuid4())
    if not report_id:
        raise ValueError("report id is empty")
    if report_id and not _report_id_exists(token, report_id):
        body = {
            "id": report_id,
            "reportType": "DETAIL_HISTORY_REPORT",
            "userReportName": "PULSE WB detail history",
            "params": {
                "startDate": start.isoformat(), "endDate": end.isoformat(),
                "timezone": "Europe/Moscow", "aggregationLevel": "day",
                "skipDeletedNm": False, "nmIds": [], "brandNames": [],
                "subjectIds": [], "tagIds": [],
            },
        }
        wb_response = app.wb_analytics_request("/api/v2/nm-report/downloads", token, body=body)
        del wb_response
        print(f"ПРОГРЕСС: отчёт создан | id={report_id}", flush=True)
    for attempt in range(1, 61):
        query = f"/api/v2/nm-report/downloads?filter%5BdownloadIds%5D%5B%5D={report_id}"
        row = _report(app.wb_analytics_request(query, token, method="GET"), report_id) or {}
        status = str(row.get("status") or row.get("state") or "unknown").upper()
        print(f"ПРОГРЕСС: статус {attempt}/60 | {status}", flush=True)
        if status in {"SUCCESS", "COMPLETED", "DONE", "READY"}:
            payload, _ = app.wb_analytics_request(
                f"/api/v2/nm-report/downloads/file/{report_id}", token,
                method="GET", binary=True,
            )
            return report_id, payload
        if status in {"FAILED", "ERROR", "CANCELLED"}:
            raise RuntimeError(f"WB CSV report failed: {status}")
        time.sleep(10)
    raise TimeoutError("WB CSV report did not become ready in 10 minutes")


def _report_id_exists(token: str, report_id: str) -> bool:
    query = f"/api/v2/nm-report/downloads?filter%5BdownloadIds%5D%5B%5D={report_id}"
    return _report(app.wb_analytics_request(query, token, method="GET"), report_id) is not None


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"Expected one CSV in WB archive, got {len(names)}")
        raw = archive.read(names[0])
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("Unsupported WB CSV encoding")
    return list(csv.DictReader(io.StringIO(text)))


def _num(row: dict[str, str], key: str) -> float | None:
    raw = str(row.get(key) or "").strip().replace(" ", "").replace(",", ".")
    return float(raw) if raw else None


def _records(cur: Any, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    cur.execute("""
        SELECT product_id::text, artikul_prodavtsa, naimenovanie,
               coalesce(seller_category_name, kategoriya_prodavtsa), brend, category_id
        FROM public.products
    """)
    products = {str(row[0]): row[1:] for row in cur.fetchall()}
    result = []
    for source in rows:
        nmid = str(source.get("nmID") or "").strip()
        if not nmid:
            continue
        meta = products.get(nmid, (None, None, None, None, None))
        record = {column: None for column in wb.DB_COLUMNS}
        record.update({
            "report_date": date.fromisoformat(str(source["dt"])[:10]),
            "wb_nmid": nmid,
            "seller_article": meta[0], "product_name": meta[1],
            "category_name": meta[2], "brand": meta[3], "subject_id": meta[4],
            "card_visits": _num(source, "openCardCount"),
            "cart_adds": _num(source, "addToCartCount"),
            "ordered_units": _num(source, "ordersCount"),
            "ordered_amount_rub": _num(source, "ordersSumRub"),
            "bought_units": _num(source, "buyoutsCount"),
            "bought_amount_rub": _num(source, "buyoutsSumRub"),
            "cancelled_units": _num(source, "cancelCount"),
            "cancelled_amount_rub": _num(source, "cancelSumRub"),
            "favorites_adds": _num(source, "addToWishlist"),
            "buyout_pct": _num(source, "buyoutPercent"),
            "card_to_cart_pct": _num(source, "addToCartConversion"),
            "cart_to_order_pct": _num(source, "cartToOrderConversion"),
        })
        units = record["ordered_units"]
        amount = record["ordered_amount_rub"]
        record["avg_price_rub"] = amount / units if amount is not None and units else None
        result.append(record)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--report-id")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    if start > end:
        raise ValueError("date_from is after date_to")
    print(f"ПЛАН: WB DETAIL_HISTORY_REPORT | {start}..{end} | скачать, проверить, атомарно заменить диапазон, перестроить витрины", flush=True)
    token = wb.token_value()
    report_id, archive = _download(token, start, end, args.report_id)
    source_rows = _csv_rows(archive)
    print(f"ПРОГРЕСС: CSV прочитан | rows={len(source_rows)} | bytes={len(archive)}", flush=True)
    writer_token = pulse_vps_admin._USE_WRITER_CONFIG.set(True) if "pulse_vps_admin" in globals() else None
    try:
        with wb.db_connection() as conn, conn.cursor() as cur:
            wb.ensure_schema(cur)
            records = _records(cur, source_rows)
            if not records:
                raise RuntimeError("WB CSV is empty; existing data kept")
            source_file = f"wb-csv:DETAIL_HISTORY_REPORT:{report_id}:{start}:{end}"
            # The backup schema is provisioned once by infrastructure. The
            # application writer deliberately has no database-level CREATE.
            cur.execute("DROP TABLE IF EXISTS pulse_qa_backup.wb_funnel_daily_before_detail_csv_20260916")
            cur.execute("""
                CREATE TABLE pulse_qa_backup.wb_funnel_daily_before_detail_csv_20260916 AS
                SELECT * FROM public.wb_funnel_daily WHERE report_date BETWEEN %s AND %s
            """, (start, end))
            cur.execute("DELETE FROM public.wb_funnel_daily WHERE report_date BETWEEN %s AND %s", (start, end))
            columns = ["report_date", *wb.DB_COLUMNS, "source_file", "source_inner_file", "source_row_num"]
            values = [(
                row["report_date"], *(row.get(column) for column in wb.DB_COLUMNS),
                source_file, "DETAIL_HISTORY_REPORT", index,
            ) for index, row in enumerate(records, 1)]
            execute_values(cur, f"INSERT INTO public.wb_funnel_daily ({', '.join(columns)}) VALUES %s", values, page_size=2000)
            cur.execute("""
                INSERT INTO public.wb_funnel_import_files
                    (source_file, report_date, rows_imported, file_size_bytes, imported_at, status, error)
                VALUES (%s, %s, %s, %s, now(), 'ok', NULL)
                ON CONFLICT (source_file) DO UPDATE SET rows_imported=excluded.rows_imported,
                    file_size_bytes=excluded.file_size_bytes, imported_at=now(), status='ok', error=NULL
            """, (source_file, start, len(values), len(archive)))
            wb.rebuild_views(cur)
            conn.commit()
            cur.execute("""
                SELECT date_trunc('month', report_date)::date, sum(ordered_units), sum(ordered_amount_rub),
                       sum(bought_units), sum(bought_amount_rub)
                FROM public.wb_funnel_daily
                WHERE report_date BETWEEN %s AND %s
                GROUP BY 1 ORDER BY 1
            """, (start, end))
            monthly = cur.fetchall()
    finally:
        if writer_token is not None:
            pulse_vps_admin._USE_WRITER_CONFIG.reset(writer_token)
    for month, orders, amount, buyouts, sales in monthly:
        print(f"МЕСЯЦ: {month:%Y-%m} | orders={int(orders or 0)} | order_sum={float(amount or 0):.2f} | buyouts={int(buyouts or 0)} | buyout_sum={float(sales or 0):.2f}", flush=True)
    print(f"ИТОГ: imported={len(records)} | months={len(monthly)} | backup=pulse_qa_backup.wb_funnel_daily_before_detail_csv_20260916 | errors=0", flush=True)


if __name__ == "__main__":
    main()
