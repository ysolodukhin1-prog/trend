"""Pure, conservative policy evaluator; no network, storage or bid execution."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json

from .domain import MetricSnapshot, Policy


def evaluate(snapshot: MetricSnapshot, policy: Policy, *, now: datetime) -> dict:
    obj = snapshot.object
    reasons: list[str] = []
    if now.tzinfo is None:
        raise ValueError("Текущее время должно иметь часовой пояс")
    age = (now - snapshot.captured_at).total_seconds()
    if age < 0 or age > policy.max_age_seconds: reasons.append("stale_snapshot")
    if snapshot.coverage != "complete": reasons.append("partial_coverage")
    if obj.unit == "unverified": reasons.append("unit_unverified")
    if snapshot.current_bid is None: reasons.append("current_bid_missing")
    if snapshot.observations is None or snapshot.observations < policy.min_observations: reasons.append("insufficient_observations")
    if snapshot.stock is None: reasons.append("stock_missing")
    elif snapshot.stock <= 0: reasons.append("out_of_stock")
    if snapshot.spend is None or snapshot.attributed_revenue is None: reasons.append("attribution_missing")
    elif snapshot.spend < 0 or snapshot.attributed_revenue <= 0: reasons.append("attribution_invalid")
    if not snapshot.maturity: reasons.append("attribution_immature")
    if snapshot.last_change_at and (now - snapshot.last_change_at).total_seconds() < policy.cooldown_seconds:
        reasons.append("cooldown")
    if snapshot.changes_today >= policy.max_changes_daily: reasons.append("daily_change_limit")
    if snapshot.current_bid is not None and not policy.min_bid <= snapshot.current_bid <= policy.max_bid:
        reasons.append("current_bid_outside_policy")
    if obj.marketplace == "yandex_market": reasons.append("activation_requires_separate_approval")
    drr = (snapshot.spend / snapshot.attributed_revenue) if snapshot.spend is not None and snapshot.attributed_revenue and snapshot.attributed_revenue > 0 else None
    proposed = None
    if not reasons and snapshot.current_bid is not None and drr is not None:
        if drr > policy.target_drr:
            proposed = max(policy.min_bid, snapshot.current_bid - policy.max_step)
            reasons.append("drr_above_target")
        elif drr < policy.target_drr / Decimal("2"):
            proposed = min(policy.max_bid, snapshot.current_bid + policy.max_step)
            reasons.append("drr_below_half_target")
        else:
            reasons.append("within_target_band")
        if proposed == snapshot.current_bid: proposed = None
    result = {"object": asdict(obj), "policy_revision": policy.revision,
              "snapshot_at": snapshot.captured_at.isoformat(), "source": snapshot.source,
              "coverage": snapshot.coverage, "current_bid": snapshot.current_bid,
              "proposed_bid": proposed, "drr": str(drr) if drr is not None else None,
              "status": "blocked" if proposed is None and any(r not in ("within_target_band", "drr_above_target", "drr_below_half_target") for r in reasons) else "proposal" if proposed is not None else "hold",
              "reason_codes": reasons, "mode": policy.mode}
    result["decision_id"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result
