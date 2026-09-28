import argparse
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ozon_category_dashboard.app import read_db_config, infer_seo_product_intents_with_openrouter
from ozon_category_dashboard.seo_projects import generate_project_intents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default="gloria_jeans")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    batch_size = max(1, min(10, args.batch_size))
    config = read_db_config(args.client)
    with psycopg2.connect(**config) as conn, conn.cursor() as cur:
        cur.execute("""SELECT ps.sku FROM public.seo_monitoring_project_skus ps
                       LEFT JOIN public.seo_generation_product_intents i
                         ON i.project_id=ps.project_id AND i.sku=ps.sku
                       WHERE ps.project_id=%s AND (%s OR i.sku IS NULL)
                       ORDER BY ps.sku""", (args.project_id, args.force))
        skus = [str(row[0]) for row in cur.fetchall()]
    batches = [skus[i:i + batch_size] for i in range(0, len(skus), batch_size)]
    print(f"ПЛАН: {len(skus)} SKU · {len(batches)} батчей по {batch_size} SKU · AI с локальным резервом · существующие интенты {'перезаписываются' if args.force else 'пропускаются'}", flush=True)
    started = time.monotonic()
    generated = ai = fallback = errors = 0
    workers = max(1, min(8, args.workers))
    def run_batch(batch):
        return generate_project_intents(config, {
            "project_id": args.project_id,
            "project_kind": "generation",
            "skus": batch,
        }, infer_seo_product_intents_with_openrouter)

    with ThreadPoolExecutor(max_workers=workers) as executor:
      pending = {executor.submit(run_batch, batch): (index, batch) for index, batch in enumerate(batches, 1)}
      completed = 0
      for future in as_completed(pending):
        index, batch = pending[future]
        completed += 1
        item_error = 0
        try:
            result = future.result()
            generated += int(result.get("generated") or 0)
            ai += int(result.get("ai_generated") or 0)
            fallback += int(result.get("fallback_generated") or 0)
            item_error = 0
        except Exception as exc:
            errors += 1
            item_error = 1
            print(f"ОШИБКА: батч {index}/{len(batches)} · {type(exc).__name__}: {exc}", flush=True)
        elapsed = max(0.1, time.monotonic() - started)
        eta = round(elapsed / completed * (len(batches) - completed) / workers) if batches else 0
        print(f"ПРОГРЕСС: {completed}/{len(batches)} ({completed * 100 / max(1, len(batches)):.1f}%) | батч {index}, SKU {len(batch)} | готово {generated} | AI {ai} | резерв {fallback} | ошибок {errors} | ETA {eta} сек.", flush=True)
        if not item_error:
            print(f"[{index}/{len(batches)}] батч: интентов {result.get('generated', 0)}, ошибок 0", flush=True)
    elapsed = round(time.monotonic() - started)
    print(f"ИТОГ: запланировано {len(skus)} SKU | сохранено {generated} | AI {ai} | резерв {fallback} | ошибок батчей {errors} | {elapsed} сек. | статус {'partial' if errors else 'complete'}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
