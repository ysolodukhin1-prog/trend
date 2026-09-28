"""Clean and rank collected SEO-generation keywords with visible batch progress."""

from __future__ import annotations

import argparse
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ozon_category_dashboard.app import analyze_seo_keywords_with_openrouter, read_db_config
from ozon_category_dashboard.seo_projects import _conn, analyze_project_keywords, ensure_schema


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default="gloria_jeans")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--ai-limit", type=int, default=3000)
    parser.add_argument("--sku", action="append", dest="skus")
    parser.add_argument("--resume", action="store_true", help="Skip SKU whose raw key pairs are fully analyzed.")
    return parser.parse_args()


def project_skus(config, project_id, requested=None, resume=False):
    with _conn(config) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if resume:
                requested_filter = "AND project_skus.sku=ANY(%s)" if requested else ""
                cur.execute(
                    f"""WITH raw AS (
                            SELECT sku, count(DISTINCT (search_query, CASE WHEN source LIKE 'mpstats_%%' THEN 'mpstats' ELSE 'api' END)) AS row_count
                            FROM public.seo_monitoring_keyword_snapshots WHERE project_id=%s GROUP BY sku
                        ), analyzed AS (
                            SELECT sku, count(*) AS row_count FROM public.seo_generation_keyword_analysis
                            WHERE project_id=%s GROUP BY sku
                        )
                        SELECT project_skus.sku
                        FROM public.seo_monitoring_project_skus project_skus
                        JOIN raw ON raw.sku=project_skus.sku
                        LEFT JOIN analyzed ON analyzed.sku=project_skus.sku
                        WHERE project_skus.project_id=%s {requested_filter}
                          AND coalesce(analyzed.row_count, 0) < raw.row_count
                        ORDER BY project_skus.sku""",
                    [project_id, project_id, project_id, requested] if requested else [project_id, project_id, project_id],
                )
            elif requested:
                cur.execute(
                    "SELECT sku FROM public.seo_monitoring_project_skus WHERE project_id=%s AND sku=ANY(%s) ORDER BY sku",
                    (project_id, requested),
                )
            else:
                cur.execute(
                    "SELECT sku FROM public.seo_monitoring_project_skus WHERE project_id=%s ORDER BY sku",
                    (project_id,),
                )
            return [str(row["sku"]) for row in cur.fetchall()]


def main():
    args = parse_args()
    batch_size = max(1, min(args.batch_size, 10))
    config = read_db_config(args.client)
    skus = project_skus(config, args.project_id, args.skus, args.resume)
    batches = [skus[index:index + batch_size] for index in range(0, len(skus), batch_size)]
    workers = max(1, min(args.workers, 6))
    print(
        f"ПЛАН: SKU {len(skus)} | батчей {len(batches)} по {batch_size} | параллельных батчей {workers} | "
        "локальные hard-gates -> AI для неоднозначных -> ВЧ/СЧ/НЧ по источнику -> приоритет",
        flush=True,
    )
    started = time.monotonic()
    totals = {"analyzed": 0, "kept": 0, "rejected": 0, "review": 0, "ai_checked": 0, "priority": 0, "errors": 0}
    def run_batch(index, batch):
        return index, batch, analyze_project_keywords(
            config,
            {
                "project_id": args.project_id,
                "project_kind": "generation",
                "skus": batch,
                "ai_limit": args.ai_limit,
            },
            analyze_seo_keywords_with_openrouter,
        )

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_batch, index, batch): (index, batch) for index, batch in enumerate(batches, 1)}
        for future in as_completed(futures):
            index, batch = futures[future]
            completed += 1
            try:
                _, _, result = future.result()
                for key in ("analyzed", "kept", "rejected", "review", "ai_checked", "priority"):
                    totals[key] += int(result.get(key) or 0)
                print(
                    f"[{index}/{len(batches)}] SKU {len(batch)}: проверено {result.get('analyzed', 0)}, "
                    f"релевантных {result.get('kept', 0)}, отклонено {result.get('rejected', 0)}, "
                    f"проверить {result.get('review', 0)}, AI {result.get('ai_checked', 0)}, "
                    f"приоритетных {result.get('priority', 0)}",
                    flush=True,
                )
            except Exception as exc:  # one failed batch must not hide the remaining project
                totals["errors"] += 1
                print(f"ОШИБКА: батч {index}/{len(batches)} | {type(exc).__name__}: {exc}", flush=True)
            elapsed = time.monotonic() - started
            eta = elapsed / completed * (len(batches) - completed) if completed else None
            print(
                f"ПРОГРЕСС: {completed}/{len(batches)} ({completed * 100 / max(1, len(batches)):.1f}%) | "
                f"проверено {totals['analyzed']} | ошибок {totals['errors']} | "
                f"ETA {math.ceil(eta) if eta is not None else '—'} сек.",
                flush=True,
            )
    elapsed = time.monotonic() - started
    print(
        f"ИТОГ: SKU {len(skus)} | проверено {totals['analyzed']} | релевантных {totals['kept']} | "
        f"отклонено {totals['rejected']} | проверить {totals['review']} | AI {totals['ai_checked']} | "
        f"приоритетных {totals['priority']} | ошибок батчей {totals['errors']} | {elapsed:.1f} сек.",
        flush=True,
    )
    raise SystemExit(1 if totals["errors"] else 0)


if __name__ == "__main__":
    main()
