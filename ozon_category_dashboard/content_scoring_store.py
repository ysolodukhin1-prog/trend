import json
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.marketplace_content_scoring_results (
    client_key text NOT NULL,
    marketplace text NOT NULL,
    sku text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    input_fingerprint text,
    source_image_count integer,
    attached_image_count integer,
    overall_score numeric(6, 2),
    confidence_pct numeric(6, 2),
    result_payload jsonb NOT NULL,
    scored_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_key, marketplace, sku)
);
CREATE INDEX IF NOT EXISTS marketplace_content_scoring_results_scored_at_idx
    ON public.marketplace_content_scoring_results (scored_at DESC);
"""


def _normalized_key(client: str, marketplace: str, sku: str) -> tuple[str, str, str]:
    client_key = str(client or "").strip().lower()
    marketplace_key = str(marketplace or "").strip().lower()
    sku_key = str(sku or "").strip()
    if not client_key or marketplace_key not in {"wb", "ozon"} or not sku_key:
        raise ValueError("Некорректный ключ сохранённого скоринга")
    return client_key, marketplace_key, sku_key


def ensure_content_scoring_store(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL)


def save_content_scoring(
    db_config: dict[str, Any], client: str, marketplace: str, sku: str, result: dict[str, Any]
) -> dict[str, Any]:
    client_key, marketplace_key, sku_key = _normalized_key(client, marketplace, sku)
    run = result.get("run") or {}
    provider = str(run.get("provider") or "").strip()
    model = str(run.get("model") or "").strip()
    if not provider or not model:
        raise ValueError("Результат скоринга не содержит провайдера или модель")
    with psycopg2.connect(**db_config, cursor_factory=RealDictCursor) as connection:
        ensure_content_scoring_store(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.marketplace_content_scoring_results (
                    client_key, marketplace, sku, provider, model, input_fingerprint,
                    source_image_count, attached_image_count, overall_score, confidence_pct,
                    result_payload, scored_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now()
                )
                ON CONFLICT (client_key, marketplace, sku) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model = EXCLUDED.model,
                    input_fingerprint = EXCLUDED.input_fingerprint,
                    source_image_count = EXCLUDED.source_image_count,
                    attached_image_count = EXCLUDED.attached_image_count,
                    overall_score = EXCLUDED.overall_score,
                    confidence_pct = EXCLUDED.confidence_pct,
                    result_payload = EXCLUDED.result_payload,
                    scored_at = now(),
                    updated_at = now()
                RETURNING scored_at
                """,
                (
                    client_key,
                    marketplace_key,
                    sku_key,
                    provider,
                    model,
                    str(run.get("input_fingerprint") or "") or None,
                    run.get("source_image_count"),
                    run.get("attached_image_count", run.get("image_count")),
                    result.get("overall_score"),
                    result.get("confidence_pct"),
                    Json(result, dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str)),
                ),
            )
            row = cursor.fetchone() or {}
    return {
        "client": client_key,
        "marketplace": marketplace_key,
        "sku": sku_key,
        "scored_at": row.get("scored_at").isoformat() if row.get("scored_at") else None,
    }


def load_content_scoring(db_config: dict[str, Any], client: str, marketplace: str, sku: str) -> dict[str, Any]:
    client_key, marketplace_key, sku_key = _normalized_key(client, marketplace, sku)
    with psycopg2.connect(**db_config, cursor_factory=RealDictCursor) as connection:
        ensure_content_scoring_store(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result_payload, provider, model, scored_at, updated_at
                  FROM public.marketplace_content_scoring_results
                 WHERE client_key = %s AND marketplace = %s AND sku = %s
                """,
                (client_key, marketplace_key, sku_key),
            )
            row = cursor.fetchone()
    if not row:
        return {"ok": True, "found": False, "client": client_key, "marketplace": marketplace_key, "sku": sku_key}
    result = row.get("result_payload") or {}
    if isinstance(result, str):
        result = json.loads(result)
    return {
        "ok": True,
        "found": True,
        "client": client_key,
        "marketplace": marketplace_key,
        "sku": sku_key,
        "scored_at": row["scored_at"].isoformat() if row.get("scored_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "provider": row.get("provider"),
        "model": row.get("model"),
        "result": result,
    }
