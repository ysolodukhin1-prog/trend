"""Small in-process token-bucket limiter for WB API methods."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Hashable


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[Hashable, _Bucket] = {}

    def acquire(
        self,
        key: Hashable,
        *,
        capacity: int,
        period_seconds: float,
        wait: Callable[[float], None] = time.sleep,
    ) -> float:
        if capacity <= 0 or period_seconds <= 0:
            raise ValueError("capacity and period_seconds must be positive")

        rate = capacity / period_seconds
        now = self._clock()
        bucket = self._buckets.setdefault(key, _Bucket(float(capacity), now))
        bucket.tokens = min(float(capacity), bucket.tokens + (now - bucket.updated_at) * rate)
        bucket.updated_at = now

        waited = 0.0
        if bucket.tokens < 1.0:
            waited = (1.0 - bucket.tokens) / rate
            wait(waited)
            now = self._clock()
            bucket.tokens = min(float(capacity), bucket.tokens + (now - bucket.updated_at) * rate)
            bucket.updated_at = now

        bucket.tokens = max(0.0, bucket.tokens - 1.0)
        return waited
