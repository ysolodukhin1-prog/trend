#!/usr/bin/env python3
"""Create an idempotent KOKOC BI client workspace and runnable API pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLIENTS_ROOT = Path(
    os.environ.get(
        "BI_CLIENTS_ROOT",
        r"G:\Общие диски\Kokoc Marketplaces\Clients",
    )
)
CLIENT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")

COMMON_DIRS = (
    Path("Аналитика/Дашборды/Logs"),
    Path("Аналитика/Дашборды/Exports"),
    Path("Аналитика/Дашборды/Data/Avito/Ads"),
)

MARKETPLACE_DIRS = {
    "ozon": (
        Path("Аналитика/Дашборды/Data/Ozon/API"),
        Path("Аналитика/Дашборды/Data/Ozon/Fin"),
        Path("Аналитика/Дашборды/Data/Ozon/Funel"),
        Path("Аналитика/Дашборды/Data/Ozon/Prod_adv"),
        Path("Аналитика/Дашборды/Data/Ozon/Product_catigories"),
        Path("Аналитика/Дашборды/Data/Ozon/Serp"),
        Path("Аналитика/Дашборды/Data/Ozon/Stock"),
    ),
    "wb": (
        Path("Аналитика/Дашборды/Data/WB/API"),
        Path("Аналитика/Дашборды/Data/WB/Adv"),
        Path("Аналитика/Дашборды/Data/WB/Entrance"),
        Path("Аналитика/Дашборды/Data/WB/Funel"),
        Path("Аналитика/Дашборды/Data/WB/Media_adv"),
        Path("Аналитика/Дашборды/Data/WB/SEO"),
        Path("Аналитика/Дашборды/Data/WB/Serp"),
        Path("Аналитика/Дашборды/Data/WB/Stock"),
    ),
    "avito": (Path("Аналитика/Дашборды/Data/Avito/API"),),
    "lamoda": (Path("Аналитика/Дашборды/Data/Lamoda/API"),),
    "yandex_market": (Path("Аналитика/Дашборды/Data/YandexMarket/API"),),
}

BOOTSTRAP_SQL = '''-- Generated KOKOC BI bootstrap for {label} ({key}).
BEGIN;
CREATE TABLE IF NOT EXISTS public.bi_import_runs (
    id bigserial PRIMARY KEY,
    source_kind text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    rows_processed bigint NOT NULL DEFAULT 0,
    errors_count integer NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE TABLE IF NOT EXISTS public.marketplace_raw_files (
    marketplace text NOT NULL,
    source_path text NOT NULL,
    source_sha256 text NOT NULL,
    report_date date,
    payload jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (marketplace, source_sha256)
);
CREATE TABLE IF NOT EXISTS public.marketplace_api_raw (
    marketplace text NOT NULL,
    entity text NOT NULL,
    external_id text NOT NULL,
    report_date date NOT NULL,
    payload jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (marketplace, entity, external_id, report_date)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_api_runs (
    id bigserial PRIMARY KEY,
    step text NOT NULL DEFAULT 'advertising',
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text NOT NULL DEFAULT 'running',
    requests_made integer NOT NULL DEFAULT 0,
    rows_loaded bigint NOT NULL DEFAULT 0,
    errors_count integer NOT NULL DEFAULT 0,
    api_point_balance integer,
    details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS public.avito_ads_stat_windows (
    account_id bigint NOT NULL,
    campaign_id bigint NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text NOT NULL DEFAULT 'ok',
    rows_loaded bigint NOT NULL DEFAULT 0,
    run_id bigint,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, campaign_id, date_from, date_to)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_account_snapshots (
    account_id bigint NOT NULL,
    snapshot_date date NOT NULL,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, snapshot_date)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_balances_daily (
    account_id bigint NOT NULL,
    snapshot_date date NOT NULL,
    balance_kopeks bigint,
    bonus_balance_kopeks bigint,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, snapshot_date)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_campaigns (
    account_id bigint NOT NULL,
    campaign_id bigint NOT NULL,
    name text,
    status text,
    advertiser_id bigint,
    contract_id bigint,
    campaign_type text,
    payment_model text,
    budget_kopeks bigint,
    source_updated_at timestamptz,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, campaign_id)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_groups (
    account_id bigint NOT NULL,
    group_id bigint NOT NULL,
    campaign_id bigint NOT NULL,
    name text,
    status text,
    budget_kopeks bigint,
    price_kopeks bigint,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, group_id)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_creatives (
    account_id bigint NOT NULL,
    creative_id bigint NOT NULL,
    campaign_id bigint NOT NULL DEFAULT 0,
    group_id bigint NOT NULL DEFAULT 0,
    name text,
    status text,
    preview_url text,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, creative_id)
);
CREATE TABLE IF NOT EXISTS public.avito_ads_stats_daily (
    account_id bigint NOT NULL,
    report_date date NOT NULL,
    entity_level text NOT NULL CHECK (entity_level IN ('campaign', 'group', 'creative')),
    campaign_id bigint NOT NULL,
    group_id bigint NOT NULL DEFAULT 0,
    creative_id bigint NOT NULL DEFAULT 0,
    views bigint,
    clicks bigint,
    ctr numeric,
    spend_kopeks bigint,
    spend_bonus_kopeks bigint,
    cpm numeric,
    cpc numeric,
    video_views_25 bigint,
    video_views_50 bigint,
    video_views_75 bigint,
    video_views_100 bigint,
    q25 numeric,
    q50 numeric,
    q75 numeric,
    vtr numeric,
    payload jsonb NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, report_date, entity_level, campaign_id, group_id, creative_id)
);
CREATE INDEX IF NOT EXISTS avito_ads_stats_daily_campaign_date_idx
    ON public.avito_ads_stats_daily (campaign_id, report_date);
CREATE OR REPLACE VIEW public.v_avito_ads_stats_daily AS
SELECT s.*, c.name AS campaign_name, g.name AS group_name, cr.name AS creative_name
FROM public.avito_ads_stats_daily s
LEFT JOIN public.avito_ads_campaigns c
  ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
LEFT JOIN public.avito_ads_groups g
  ON g.account_id = s.account_id AND g.group_id = s.group_id AND s.group_id <> 0
LEFT JOIN public.avito_ads_creatives cr
  ON cr.account_id = s.account_id AND cr.creative_id = s.creative_id AND s.creative_id <> 0;
COMMIT;
'''

PIPELINE_WRAPPER = '''#!/usr/bin/env python3
"""Generated KOKOC BI {mode_label} runner for {label}."""
from __future__ import annotations
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def main() -> int:
    yesterday = date.today() - timedelta(days=1)
    command = [
        sys.executable, "-X", "utf8", "-u",
        str(PROJECT_ROOT / "ozon_category_dashboard" / "scripts" / "run_client_pipeline.py"),
        "--client-key", "{key}",
        "--database-name", "{db_name}",
        "--marketplaces", "{marketplaces}",
        "--mode", "{mode}",
        "--date-from", yesterday.isoformat(),
        "--date-to", yesterday.isoformat(),
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
'''


def validate_inputs(key: str, label: str, db_name: str, clients_root: Path) -> None:
    if not CLIENT_KEY_RE.fullmatch(key):
        raise ValueError("client-key must match ^[a-z][a-z0-9_]{1,47}$")
    if not label.strip():
        raise ValueError("display-name is required")
    if label in {".", ".."} or any(char in label for char in '<>:"/\\|?*'):
        raise ValueError("display-name contains a character forbidden in Windows folder names")
    if not CLIENT_KEY_RE.fullmatch(db_name):
        raise ValueError("database-name must match ^[a-z][a-z0-9_]{1,47}$")
    if not clients_root.is_absolute() or clients_root.drive.lower() != "g:":
        raise ValueError("clients-root must be an absolute path on G:\\")


def build_plan(
    key: str,
    label: str,
    db_name: str,
    clients_root: Path,
    marketplaces: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[Path, str | None]]:
    marketplaces = list(dict.fromkeys(marketplaces or ("ozon", "wb")))
    unknown = sorted(set(marketplaces) - set(MARKETPLACE_DIRS))
    if unknown:
        raise ValueError("unsupported marketplaces: " + ", ".join(unknown))
    client_root = clients_root / label
    code_root = PROJECT_ROOT / "scripts" / "clients" / key
    relative_dirs = list(COMMON_DIRS)
    for marketplace in marketplaces:
        relative_dirs.extend(MARKETPLACE_DIRS[marketplace])
    plan: list[tuple[Path, str | None]] = [
        (client_root / relative, None) for relative in relative_dirs
    ]
    manifest = {
        "key": key,
        "label": label,
        "db_name": db_name,
        "client_root": str(client_root),
        "data_root": str(client_root / "Аналитика/Дашборды/Data"),
        "credential_source": "public.bi_client_credentials",
        "marketplaces": marketplaces,
        "daily_runner": str(code_root / "run_daily.py"),
        "views_runner": str(code_root / "rebuild_views.py"),
    }
    plan.extend(
        [
            (code_root, None),
            (code_root / "migrations", None),
            (code_root / "client_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"),
            (code_root / "migrations" / "001_bootstrap.sql", BOOTSTRAP_SQL.format(key=key, label=label, db_name=db_name)),
            (code_root / "run_daily.py", PIPELINE_WRAPPER.format(
                mode_label="daily", label=label, key=key, db_name=db_name,
                marketplaces=",".join(marketplaces), mode="daily",
            )),
            (code_root / "rebuild_views.py", PIPELINE_WRAPPER.format(
                mode_label="views", label=label, key=key, db_name=db_name,
                marketplaces=",".join(marketplaces), mode="views",
            )),
        ]
    )
    return plan


def apply_plan(plan: list[tuple[Path, str | None]], apply: bool) -> dict:
    started = time.monotonic()
    created = skipped = errors = 0
    total = len(plan)
    print(f"ПЛАН: объектов {total} | режим={'apply' if apply else 'dry-run'} | существующие файлы не перезаписываются", flush=True)
    for index, (path, content) in enumerate(plan, start=1):
        action = "каталог" if content is None else "файл"
        try:
            if path.exists():
                skipped += 1
                state = "пропущен: уже существует"
            elif apply:
                if content is None:
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                created += 1
                state = "создан"
            else:
                state = "будет создан"
            elapsed = time.monotonic() - started
            eta = elapsed / index * (total - index) if index else 0
            print(f"ПРОГРЕСС: {index}/{total} ({index / total:.0%}) | {action} {path} | created={created} skipped={skipped} errors={errors} | ETA {eta:.1f}s", flush=True)
            print(f"[{index}/{total}] {path}: {state}", flush=True)
        except OSError as exc:
            errors += 1
            print(f"[{index}/{total}] {path}: ошибка {exc}", flush=True)
    elapsed = time.monotonic() - started
    print(f"ИТОГ: created={created} skipped={skipped} errors={errors} total={total} elapsed={elapsed:.1f}s mode={'apply' if apply else 'dry-run'}", flush=True)
    return {"total": total, "created": created, "skipped": skipped, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", required=True)
    parser.add_argument("--display-name", "--client-name", dest="display_name", required=True)
    parser.add_argument("--database-name", "--db-name", dest="database_name")
    parser.add_argument("--clients-root", type=Path, default=DEFAULT_CLIENTS_ROOT)
    parser.add_argument("--marketplaces", default="ozon,wb")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    key = args.client_key.strip().lower()
    label = args.display_name.strip()
    db_name = (args.database_name or key).strip().lower()
    marketplaces = [value.strip().lower() for value in args.marketplaces.split(",") if value.strip()]
    try:
        validate_inputs(key, label, db_name, args.clients_root)
        result = apply_plan(
            build_plan(key, label, db_name, args.clients_root, marketplaces=marketplaces),
            args.apply,
        )
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
