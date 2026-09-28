"""Draft parser: collect SEO search queries for one SEO-monitoring project.

Runs the same collection the dashboard button runs, but as a resumable CLI job:
Ozon goes through Seller API `/v1/analytics/product-queries/details` (one request
per SKU, max 15 phrases per run), WB reads the daily search-query report already
in PostgreSQL. Snapshots land in `public.seo_monitoring_keyword_snapshots`.

Usage:
    python -m ozon_category_dashboard.scripts.seo_queries_parser \
        --client gloria_jeans --project-id <uuid> \
        --date-from 2026-07-21 --date-to 2026-08-17 \
        [--snapshot-date 2026-08-18] [--limit-by-sku 15] \
        [--sort-by BY_SEARCHES] [--sort-dir DESCENDING] \
        [--batch-size 50] [--sleep 0.4] [--skus-file skus.txt] [--dry-run]

Progress follows the workspace standard: an upfront plan, `ПРОГРЕСС:` lines per
batch, per-batch completion lines and a final summary. Secrets are never printed.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "ozon_category_dashboard"
# `app.py` imports its neighbours by bare name (client_registry, ...), so both the
# repo root and the app directory have to be importable.
for candidate in (ROOT, APP_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from ozon_category_dashboard.app import (  # noqa: E402
    handle_ozon_seo_product_queries_details,
    normalize_client_key,
    read_db_config,
)
from ozon_category_dashboard.seo_projects import (  # noqa: E402
    _conn,
    collect_project_days,
    default_backfill_range,
    ensure_schema,
    latest_available_day,
    list_projects,
    refresh_project,
)

BATCH_LIMIT = 1000  # refresh_project accepts up to 1000 SKU per call; Ozon batches them internally


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Collect SEO search queries for one project")
    parser.add_argument("--client", required=True)
    parser.add_argument("--project-id", help="Omit together with --all-projects to process every project of the client")
    parser.add_argument("--all-projects", action="store_true", help="Process all projects of the client")
    parser.add_argument(
        "--mode",
        default="backfill",
        choices=["backfill", "daily", "period"],
        help="backfill: last month day by day, skipping days already stored (default for a first run); "
             "daily: only the newest available day; period: one snapshot for --date-from..--date-to",
    )
    parser.add_argument("--date-from", help="period/backfill start; defaults to the last month")
    parser.add_argument("--date-to", help="period/backfill end; defaults to the newest available day")
    parser.add_argument("--force", action="store_true", help="Re-collect days that already have a snapshot")
    parser.add_argument("--snapshot-date", default=date.today().isoformat())
    parser.add_argument("--limit-by-sku", type=int, default=15, help="Ozon returns 1..15 phrases per SKU")
    parser.add_argument(
        "--sort-by",
        default="BY_SEARCHES",
        choices=["BY_SEARCHES", "BY_VIEWS", "BY_POSITION", "BY_CONVERSION", "BY_GMV"],
        help="Each sort returns its own top-15 slice; several runs widen the phrase set",
    )
    parser.add_argument("--sort-dir", default="DESCENDING", choices=["DESCENDING", "ASCENDING"])
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--all-sorts", action="store_true", help="Run all five sorts: ~50-60 phrases per SKU instead of 15")
    parser.add_argument("--sleep", type=float, default=0.4, help="Pause between batches, seconds")
    parser.add_argument("--skus-file", help="Optional file with one SKU per line to restrict the run")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and exit without API calls")
    return parser.parse_args(argv)


def load_project(config, project_id):
    with _conn(config) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project_id, name, marketplace, date_from, date_to FROM public.seo_monitoring_projects WHERE project_id=%s",
                (project_id,),
            )
            project = cur.fetchone()
            if not project:
                raise SystemExit(f"SEO-проект {project_id} не найден")
            cur.execute(
                "SELECT sku FROM public.seo_monitoring_project_skus WHERE project_id=%s ORDER BY sku",
                (project_id,),
            )
            skus = [str(row["sku"]) for row in cur.fetchall()]
    return dict(project), skus


def restrict_skus(skus, skus_file):
    if not skus_file:
        return skus, 0
    wanted = {line.strip() for line in Path(skus_file).read_text(encoding="utf-8").splitlines() if line.strip()}
    kept = [sku for sku in skus if sku in wanted]
    return kept, len(wanted - set(kept))


def resolve_projects(config, args):
    if args.all_projects and not args.project_id:
        rows = list_projects(config).get("rows") or []
        return [str(row["project_id"]) for row in rows]
    if not args.project_id:
        raise SystemExit("Укажите --project-id или --all-projects")
    return [args.project_id]


def run_period_mode(config, args, client, project, skus, batch_size):
    """One snapshot for the whole requested period (the original behaviour)."""
    batches = [skus[index:index + batch_size] for index in range(0, len(skus), batch_size)]
    sorts = 5 if args.all_sorts else 1
    chunks = len(skus) // 100 + (1 if len(skus) % 100 else 0)
    requests_planned = chunks * sorts if project["marketplace"] != "wb" else len(batches)
    sort_label = "все пять" if args.all_sorts else args.sort_by + "/" + args.sort_dir
    print(
        f"ПЛАН: режим period | проект {project['name']} | площадка {project['marketplace']} | SKU {len(skus)} | "
        f"батчей {len(batches)} по {batch_size} | запросов к источнику ~{requests_planned} | "
        f"период {args.date_from}..{args.date_to} | снимок {args.snapshot_date} | "
        f"фраз на SKU {args.limit_by_sku} | сортировка {sort_label} | пауза между батчами {args.sleep} сек."
    )
    if args.dry_run:
        print("ИТОГ: dry-run, обращений к источнику не было")
        return 0, 0

    def fetch(request_payload):
        return handle_ozon_seo_product_queries_details({"client": client, **request_payload})

    started = time.monotonic()
    rows_total = 0
    error_total = 0
    for index, batch in enumerate(batches, start=1):
        elapsed = max(1.0, time.monotonic() - started)
        eta = round(elapsed / (index - 1) * (len(batches) - index + 1)) if index > 1 else "—"
        print(
            f"ПРОГРЕСС: {index}/{len(batches)} ({round(index * 100 / len(batches))}%) | "
            f"SKU в батче {len(batch)} | строк {rows_total} | ошибок {error_total} | ETA {eta} сек."
        )
        try:
            result = refresh_project(config, {
                "project_id": project["project_id"], "skus": batch,
                "date_from": args.date_from, "date_to": args.date_to, "snapshot_date": args.snapshot_date,
                "limit_by_sku": args.limit_by_sku, "sort_by": args.sort_by, "sort_dir": args.sort_dir,
                "all_sorts": "1" if args.all_sorts else "",
            }, fetch)
        except Exception as exc:  # a failed batch must not abort the remaining ones
            error_total += len(batch)
            print(f"ОШИБКА: батч {index}/{len(batches)} | {str(exc)[:500]}")
            continue
        rows_total += int(result.get("row_count") or 0)
        batch_errors = result.get("errors") or []
        error_total += len(batch_errors)
        for item in batch_errors:
            print(f"ОШИБКА: SKU {item.get('sku')} | сортировка {item.get('sort_by')} | {str(item.get('error'))[:300]}")
        print(
            f"[{index}/{len(batches)}] батч завершён: строк {result.get('row_count')}, "
            f"ошибок {len(batch_errors)}, сортировок {len(result.get('sorts_used') or [])}, "
            f"статус проекта {result.get('status')}"
        )
        if args.sleep and index < len(batches):
            time.sleep(args.sleep)
    return rows_total, error_total


def run_days_mode(config, args, client, project, skus=None):
    """Daily monitoring: backfill the last month, or append the newest available day."""
    if args.mode == "daily":
        day = latest_available_day()
        date_from = date_to = day.isoformat()
    else:
        default_from, default_to = default_backfill_range()
        date_from = args.date_from or default_from.isoformat()
        date_to = args.date_to or default_to.isoformat()
    sort_label = "все пять" if args.all_sorts else args.sort_by + "/" + args.sort_dir
    print(
        f"ПЛАН: режим {args.mode} | проект {project['name']} | площадка {project['marketplace']} | "
        f"дни {date_from}..{date_to} | снимок = сам день | фраз на SKU {args.limit_by_sku} | "
        f"сортировка {sort_label} | повтор уже собранных дней: {'да' if args.force else 'нет'}"
    )
    if args.dry_run:
        print("ИТОГ: dry-run, обращений к источнику не было")
        return 0, 0

    def fetch(request_payload):
        return handle_ozon_seo_product_queries_details({"client": client, **request_payload})

    started = time.monotonic()

    def progress(index, total, day, rows_done, errors_done):
        elapsed = max(1.0, time.monotonic() - started)
        eta = round(elapsed / (index - 1) * (total - index + 1)) if index > 1 else "—"
        print(
            f"ПРОГРЕСС: {index}/{total} ({round(index * 100 / max(total, 1))}%) | день {day} | "
            f"строк {rows_done} | ошибок {errors_done} | ETA {eta} сек."
        )

    result = collect_project_days(config, {
        "project_id": project["project_id"],
        "skus": list(skus or []),
        "date_from": date_from, "date_to": date_to,
        "limit_by_sku": args.limit_by_sku, "sort_by": args.sort_by, "sort_dir": args.sort_dir,
        "all_sorts": "1" if args.all_sorts else "",
        "force": "1" if args.force else "",
    }, fetch, progress)
    for item in result.get("errors") or []:
        print(f"ОШИБКА: день {item.get('day')} | SKU {item.get('sku', '')} | {str(item.get('error'))[:300]}")
    print(
        f"[{project['name']}] дней запланировано {result['days_planned']}, собрано {result['days_done']}, "
        f"пропущено уже собранных {result['days_skipped']}, строк {result['row_count']}, "
        f"последний доступный день {result['latest_available_day']}"
    )
    return int(result.get("row_count") or 0), len(result.get("errors") or [])


def main(argv=None):
    args = parse_args(argv)
    if not 1 <= args.limit_by_sku <= 15:
        raise SystemExit("--limit-by-sku должен быть от 1 до 15: Ozon отдаёт максимум 15 фраз по SKU")
    if args.mode == "period" and not (args.date_from and args.date_to):
        raise SystemExit("В режиме period укажите --date-from и --date-to")
    batch_size = max(1, min(args.batch_size, BATCH_LIMIT))
    client = normalize_client_key(args.client)
    config = read_db_config(client)
    started = time.monotonic()
    rows_total = 0
    error_total = 0
    project_ids = resolve_projects(config, args)
    print(f"ПЛАН: клиент {client} | проектов к обработке {len(project_ids)} | режим {args.mode}")
    for project_id in project_ids:
        project, skus = load_project(config, project_id)
        skus, unknown = restrict_skus(skus, args.skus_file)
        if unknown:
            print(f"ВНИМАНИЕ: {unknown} SKU из файла нет в группе проекта {project['name']}, пропущены")
        if not skus:
            print(f"ПРОПУСК: в группе проекта {project['name']} нет SKU")
            continue
        project = {**project, "project_id": project_id}
        if args.mode == "period":
            rows, errors = run_period_mode(config, args, client, project, skus, batch_size)
        else:
            rows, errors = run_days_mode(config, args, client, project, skus)
        rows_total += rows
        error_total += errors
        if args.sleep:
            time.sleep(args.sleep)

    print(
        f"ИТОГ: клиент {client} | проектов {len(project_ids)} | строк снимков {rows_total} | "
        f"ошибок {error_total} | {round(time.monotonic() - started)} сек. | "
        "таблица public.seo_monitoring_keyword_snapshots"
    )
    return 1 if error_total and not rows_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
