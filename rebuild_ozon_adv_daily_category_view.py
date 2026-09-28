from __future__ import annotations

import os
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import import_ozon_adv_daily_reports as importer  # noqa: E402


def main() -> None:
    work_mem = os.environ.get("OZON_ADV_REBUILD_WORK_MEM", "128MB")
    maintenance_work_mem = os.environ.get("OZON_ADV_REBUILD_MAINTENANCE_WORK_MEM", "512MB")
    print(
        "ПЛАН: Ozon adv materialized view | "
        f"work_mem={work_mem} | maintenance_work_mem={maintenance_work_mem} | "
        "сначала totals, затем materialized view и индексы",
        flush=True,
    )
    with importer.app.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('work_mem', %s, true)", (work_mem,))
        cur.execute("SELECT set_config('maintenance_work_mem', %s, true)", (maintenance_work_mem,))
        # Daily API sync can create/upgrade the raw advertising table without
        # running the XLSX importer. Keep the standalone view rebuild compatible
        # with that schema before selecting optional columns such as promoted_sku.
        importer.create_schema(cur)
        print(
            "ПРОГРЕСС: 1/3 (0%) | обновление Ozon Prod_adv totals из funnel",
            flush=True,
        )
        importer.update_prod_adv_totals_from_funnel(cur)
        print(
            "ПРОГРЕСС: 2/3 (33%) | создание public.mv_ozon_adv_daily_by_article_category",
            flush=True,
        )
        cur.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_ozon_adv_daily_by_article_category")
        importer.create_indexes(cur)
        importer.create_materialized_view(cur)
        print("ПРОГРЕСС: 3/3 (67%) | проверки и фиксация результата", flush=True)
        importer.validate_daily_match_quality(cur)
        importer.print_checks(cur)
        conn.commit()
        print("ИТОГ: Ozon adv materialized view | 3/3 | ошибок 0", flush=True)


if __name__ == "__main__":
    main()

