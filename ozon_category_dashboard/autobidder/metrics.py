"""Read-only adaptation of the existing WB campaign snapshots."""
from __future__ import annotations

from contextlib import closing


def wb_objects(get_conn, *, client: str, limit: int = 100) -> dict:
    """Only exact campaign/nm settings; historical cluster bids are not current bids."""
    if not 1 <= limit <= 300:
        raise ValueError("Некорректный размер страницы")
    with closing(get_conn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.wb_api_entities') AS relation")
        if not cur.fetchone()["relation"]:
            return {"rows": [], "source_status": "missing", "reason": "Снимки рекламных кампаний WB не загружены"}
        cur.execute("""SELECT count(*) AS rows_count, max(record_date) AS latest_date,
            max(captured_at) AS captured_at FROM public.wb_api_entities
            WHERE source_key='promotion.fullstats'""")
        stats = cur.fetchone()
        cur.execute("SELECT to_regclass('public.wb_stock_api_current') AS relation")
        stock_available = bool(cur.fetchone()["relation"])
        stock_date = None
        if stock_available:
            cur.execute("SELECT max(snapshot_date) AS latest_date FROM public.wb_stock_api_current")
            stock_date = cur.fetchone()["latest_date"]
        evidence = {"fullstats_rows": stats["rows_count"],
                    "fullstats_latest_date": stats["latest_date"].isoformat() if stats["latest_date"] else None,
                    "fullstats_captured_at": stats["captured_at"].isoformat() if stats["captured_at"] else None,
                    "stock_latest_date": stock_date.isoformat() if stock_date else None}
        cur.execute("""SELECT DISTINCT ON (payload->>'id') payload, captured_at
            FROM public.wb_api_entities WHERE source_key='promotion.campaigns'
            AND payload->>'id' IS NOT NULL ORDER BY payload->>'id', captured_at DESC""")
        rows = []
        for item in cur.fetchall():
            raw, captured = item["payload"], item["captured_at"]
            campaign = raw.get("id")
            payment = str((raw.get("settings") or {}).get("payment_type") or "").lower()
            unit = {"cpc": "kopecks_cpc", "cpm": "kopecks_cpm"}.get(payment)
            for setting in raw.get("nm_settings") or []:
                if len(rows) >= limit:
                    break
                nm_id = setting.get("nm_id")
                for placement, value in (setting.get("bids_kopecks") or {}).items():
                    if len(rows) >= limit: break
                    if value is None: continue
                    rows.append({"client": client, "marketplace": "wb", "account": None,
                                 "campaign": str(campaign), "sku": str(nm_id), "placement": str(placement),
                                 "unit": unit or "unverified", "current_bid": value,
                                 "captured_at": captured.isoformat() if captured else None,
                                 "campaign_status": raw.get("status"), "payment_type": payment or None,
                                 "bid_type": raw.get("bid_type"),
                                 "source": "wb_api_entities:promotion.campaigns"})
            if len(rows) >= limit: break
    source_reason = ("История fullstats на уровне кампания×SKU×день доступна, но её зрелость, "
                     "актуальность остатков и идентичность рекламного кабинета не подтверждены"
                     if evidence["fullstats_rows"] else
                     "История fullstats отсутствует; актуальность остатков и идентичность рекламного кабинета не подтверждены")
    return {"rows": rows, "source_status": "partial", "source_evidence": evidence,
            "reason": source_reason}


def capabilities() -> list[dict]:
    return [
        {"marketplace": "wb", "observe": "partial", "recommend": "blocked_data", "write": "disabled", "unit": "kopecks_cpc/kopecks_cpm"},
        {"marketplace": "yandex_market", "observe": "unverified_account", "recommend": "blocked_data", "write": "disabled_activation", "unit": "percent_hundredths"},
        {"marketplace": "ozon", "observe": "traffic_only", "recommend": "blocked_unit", "write": "unverified", "unit": None},
    ]
