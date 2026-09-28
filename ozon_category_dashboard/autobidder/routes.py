"""Read-only PULSE endpoint. No policy/save/approve/send HTTP route exists."""
from __future__ import annotations

from urllib.parse import parse_qs

from .metrics import capabilities, wb_objects


def get_payload(app, parsed):
    params = parse_qs(parsed.query)
    client = (params.get("client") or [""])[0].strip().lower()
    if not app.review_client_allowed(client) or not app.client_supports_report(client, "adv"):
        return 403, {"ok": False, "error": "Нет доступа к выбранному аккаунту"}
    market = (params.get("marketplace") or ["wb"])[0]
    if market not in app.ADMIN_CLIENTS[client].get("marketplaces", []):
        return 400, {"ok": False, "error": "Площадка недоступна для выбранного клиента"}
    result = {"ok": True, "client": client, "marketplace": market,
              "mode": "observe", "writer_enabled": False, "capabilities": capabilities(),
              "rows": [], "source_status": "missing", "reason": "Источник ставок не подключён"}
    if market == "wb":
        token = app.CURRENT_CLIENT.set(client)
        try:
            result.update(wb_objects(app.get_conn, client=client))
        finally:
            app.CURRENT_CLIENT.reset(token)
    elif market == "ozon":
        result.update(source_status="partial", reason="Отчёт по фразам содержит трафик без подтверждённых SKU-ставок и расходов")
    elif market == "yandex_market":
        result.update(source_status="missing", reason="API-кампания и ставки этого бизнеса ещё не сверены")
    return 200, result
