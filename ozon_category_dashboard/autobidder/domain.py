"""Native-unit bid contracts and immutable decision inputs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

Market = Literal["wb", "ozon", "yandex_market"]
Unit = Literal["kopecks_cpc", "kopecks_cpm", "percent_hundredths", "unverified"]


@dataclass(frozen=True)
class BidObject:
    client: str
    marketplace: Market
    account: str
    campaign: str
    sku: str
    placement: str
    unit: Unit

    def __post_init__(self):
        if not all((self.client, self.account, self.campaign, self.sku, self.placement)):
            raise ValueError("Неполная идентичность рекламного объекта")
        expected = {"wb": {"kopecks_cpc", "kopecks_cpm"},
                    "yandex_market": {"percent_hundredths"}, "ozon": {"unverified"}}
        if self.unit not in expected[self.marketplace]:
            raise ValueError("Единица ставки не соответствует площадке")


@dataclass(frozen=True)
class MetricSnapshot:
    object: BidObject
    captured_at: datetime
    current_bid: int | None
    observations: int | None
    spend: Decimal | None
    attributed_revenue: Decimal | None
    stock: int | None
    source: str
    coverage: str = "partial"
    maturity: bool = False
    last_change_at: datetime | None = None
    changes_today: int = 0

    def __post_init__(self):
        if self.captured_at.tzinfo is None:
            raise ValueError("Дата снимка должна иметь часовой пояс")
        if self.current_bid is not None and (type(self.current_bid) is not int or self.current_bid < 0):
            raise ValueError("Ставка должна быть неотрицательным целым")


@dataclass(frozen=True)
class Policy:
    revision: str
    target_drr: Decimal
    min_bid: int
    max_bid: int
    max_step: int
    min_observations: int
    max_age_seconds: int
    cooldown_seconds: int
    max_changes_daily: int
    mode: Literal["observe", "recommend"] = "recommend"

    def __post_init__(self):
        if not self.revision or self.mode not in ("observe", "recommend"):
            raise ValueError("Недопустимая версия или режим")
        if (self.min_bid < 1 or self.max_bid < self.min_bid or self.max_step < 1
                or self.min_observations < 1 or self.max_age_seconds < 1
                or self.cooldown_seconds < 0 or self.max_changes_daily < 1
                or not Decimal("0") < self.target_drr < Decimal("1")):
            raise ValueError("Некорректные пределы политики")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
