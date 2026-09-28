"""Simulation of the outbox transitions. A production sender is intentionally absent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class FakeJob:
    decision_id: str
    client: str
    account: str
    expected_bid: int
    proposed_bid: int
    expires_at: datetime
    approved_client: str = ""
    approved_account: str = ""
    approved_bid_min: int = 0
    approved_bid_max: int = 0
    approved: bool = False
    revoked: bool = False
    state: str = "queued"
    lease_token: int = 0
    lease_until: datetime | None = None


class FakeOutbox:
    """Deterministic fault-injection harness, not a production queue or sender."""
    def __init__(self):
        self.jobs: dict[str, FakeJob] = {}
        self.paused: set[tuple[str, str]] = set()

    def enqueue(self, job: FakeJob) -> FakeJob:
        prior = self.jobs.setdefault(job.decision_id, job)
        if prior != job:
            raise ValueError("Конфликт повторной команды")
        return prior

    def pause(self, client: str, account: str):
        self.paused.add((client, account))

    def claim(self, key: str, *, now: datetime, observed_bid: int) -> int:
        job = self.jobs[key]
        if job.state != "queued" or job.lease_until and job.lease_until > now:
            raise RuntimeError("Очередь уже занята или не готова")
        if any(other is not job and other.client == job.client and other.account == job.account
               and other.state == "sending" for other in self.jobs.values()):
            raise RuntimeError("Другой исполнитель аккаунта уже отправляет изменение")
        if (job.client, job.account) in self.paused:
            job.state = "blocked"; raise RuntimeError("Отправка приостановлена")
        if (not job.approved or job.revoked or job.approved_client != job.client
                or job.approved_account != job.account
                or not job.approved_bid_min <= job.proposed_bid <= job.approved_bid_max):
            job.state = "blocked"; raise PermissionError("Нет действующего разрешения на точный аккаунт и диапазон")
        if job.expires_at <= now:
            job.state = "expired"; raise RuntimeError("Разрешение истекло")
        if observed_bid != job.expected_bid:
            job.state = "conflict"; raise RuntimeError("Ставка изменена вне PULSE")
        job.lease_token += 1
        job.lease_until = now + timedelta(seconds=30)
        job.state = "sending"
        return job.lease_token

    def sent(self, key: str, token: int, *, uncertain: bool = False):
        job = self.jobs[key]
        if job.state != "sending" or token != job.lease_token:
            raise RuntimeError("Просроченный исполнитель")
        job.state = "uncertain" if uncertain else "awaiting_readback"

    def readback(self, key: str, observed_bid: int):
        job = self.jobs[key]
        if job.state not in ("awaiting_readback", "uncertain"):
            raise RuntimeError("Нельзя подтверждать без отправки")
        job.state = "confirmed" if observed_bid == job.proposed_bid else "conflict"
        return job.state

    def expire_inflight(self, *, now: datetime):
        """A lost send is uncertain, never put back on a blindly retryable queue."""
        for job in self.jobs.values():
            if job.state == "sending" and job.lease_until and job.lease_until <= now:
                job.state = "uncertain"
