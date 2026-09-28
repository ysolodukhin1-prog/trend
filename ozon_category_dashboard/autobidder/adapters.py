"""Offline request builders. No HTTP client or credential access is provided here."""
from .domain import BidObject


def wb_request(obj: BidObject, bid: int, *, campaign_status: int, bid_type: str, min_bid: int) -> dict:
    if obj.marketplace != "wb" or obj.unit not in ("kopecks_cpc", "kopecks_cpm"):
        raise ValueError("Объект WB и модель оплаты обязательны")
    if obj.placement not in ("combined", "search", "recommendations"):
        raise ValueError("Недопустимое место показа WB")
    if campaign_status not in (4, 9, 11) or bid_type not in ("unified", "manual"):
        raise ValueError("Статус или стратегия кампании не подтверждены")
    if (bid_type == "unified" and obj.placement != "combined") or (bid_type == "manual" and obj.placement == "combined"):
        raise ValueError("Показ не соответствует стратегии кампании")
    if type(min_bid) is not int or min_bid <= 0 or bid < min_bid:
        raise ValueError("Минимальная ставка не подтверждена")
    if not obj.campaign.isdecimal() or not obj.sku.isdecimal() or type(bid) is not int or bid <= 0:
        raise ValueError("Неподтверждённые идентификаторы или ставка WB")
    return {"bids": [{"advert_id": int(obj.campaign), "nm_bids": [{"nm_id": int(obj.sku), "bid_kopecks": bid, "placement": obj.placement}]}]}


def wb_readback(campaign: dict, obj: BidObject) -> int | None:
    if obj.marketplace != "wb" or str(campaign.get("id")) != obj.campaign:
        raise ValueError("Не тот рекламный объект WB")
    for item in campaign.get("nm_settings") or []:
        if str(item.get("nm_id")) == obj.sku:
            value = (item.get("bids_kopecks") or {}).get(obj.placement)
            if value is None: return None
            if type(value) is not int or value < 0: raise ValueError("Ставка readback недостоверна")
            return value
    return None


def yandex_request(obj: BidObject, bid: int, *, campaign_activation_approved: bool, api_campaign_verified: bool) -> dict:
    if obj.marketplace != "yandex_market" or obj.unit != "percent_hundredths" or not obj.account.isdecimal():
        raise ValueError("Требуется бизнес-аккаунт Яндекс Маркета")
    if not campaign_activation_approved or not api_campaign_verified:
        raise PermissionError("PUT может создать или повторно включить API-кампанию")
    if type(bid) is not int or bid != 0 and not 50 <= bid <= 9999:
        raise ValueError("Ставка Яндекс Маркета вне допустимых границ")
    return {"bids": [{"sku": obj.sku, "bid": bid}]}


def yandex_info_spec(business_id: str, *, skus: list[str] | None = None, limit: int = 250) -> dict:
    """Read-only POST; no network is performed by this builder."""
    if not business_id.isdecimal() or int(business_id) < 1:
        raise ValueError("Некорректный businessId")
    if not 1 <= limit <= 500 or skus is not None and (not 1 <= len(skus) <= 500 or len(set(skus)) != len(skus)):
        raise ValueError("Некорректная страница ставок")
    return {"method": "POST", "path": f"/v2/businesses/{business_id}/bids/info",
            "query": {"limit": limit} if skus is None else {},
            "body": {} if skus is None else {"skus": skus}}


def parse_yandex_info(response: dict) -> tuple[dict[str, int], str | None]:
    if response.get("status") != "OK" or not isinstance(response.get("result"), dict):
        raise ValueError("Нет подтверждённого ответа bids/info")
    result = response["result"]
    bids = result.get("bids")
    if not isinstance(bids, list):
        raise ValueError("Неизвестная схема ставок")
    parsed: dict[str, int] = {}
    for item in bids:
        if not isinstance(item, dict) or not isinstance(item.get("sku"), str) or type(item.get("bid")) is not int:
            raise ValueError("Неизвестная строка ставки")
        if item["sku"] in parsed:
            raise ValueError("Дубликат SKU в ответе")
        parsed[item["sku"]] = item["bid"]
    paging = result.get("paging") or {}
    if not isinstance(paging, dict):
        raise ValueError("Неизвестная схема пагинации")
    return parsed, paging.get("nextPageToken")


def ozon_request(*_args, **_kwargs):
    raise NotImplementedError("Ozon Performance write contract не подтверждён")
