"""THE SAMPLER: turn a day's pile of candidates into a human-sized queue.

Rules fire ~65k times on a busy day; an analyst team reads ~30 cases. The
sampler is that funnel, and it is where "rules are a sampler, not a detector"
becomes code:

  1. dedup  -- one case per subject_account (its best-scoring txn that day)
  2. quota  -- `reserve` of the budget round-robined across QUOTA_RULES
               dominants, so one loud rule cannot monopolize the queue
  3. fill   -- the rest by plain (score, burst) rank

to_alert_json() serializes a promoted candidate into the frozen Alert
contract (contracts.py). The label appears nowhere in this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .rules import FEATURE_KEYS, QUOTA_RULES


def sample_day(candidates: list[dict], budget: int = 30,
               reserve: float = 0.5) -> list[dict]:
    best: dict[str, dict] = {}
    for c in candidates:
        cur = best.get(c["subject_account"])
        if cur is None or (-c["score"], c["txn_id"]) < (-cur["score"], cur["txn_id"]):
            best[c["subject_account"]] = c
    ranked = sorted(best.values(),
                    key=lambda c: (-c["score"], -c["subject_burst"], c["txn_id"]))

    n_reserved = int(budget * reserve)
    by_rule = {rule: [c for c in ranked if c["dominant_rule"] == rule]
               for rule in QUOTA_RULES}
    quota, i = [], 0
    while len(quota) < n_reserved and any(len(v) > i for v in by_rule.values()):
        for rule in QUOTA_RULES:
            if i < len(by_rule[rule]) and len(quota) < n_reserved:
                quota.append(by_rule[rule][i])
        i += 1
    taken = {c["txn_id"] for c in quota}
    fill = [c for c in ranked if c["txn_id"] not in taken]
    return quota + fill[: budget - len(quota)]


def to_alert_json(c: dict, promoted_at: datetime) -> dict:
    """Candidate dict -> frozen Alert contract shape (contracts.Alert)."""
    feats = {}
    for k in FEATURE_KEYS:
        v = c.get(k)
        if v is not None:
            feats[k] = float(v)
    return {
        "alert_id": f"AL-{c['txn_id']:08d}",
        "txn": {
            "txn_id": str(c["txn_id"]),
            "timestamp": c["ts"].isoformat(),
            "sender_bank": c["sender_bank"],
            "sender_account": c["sender_account"],
            "receiver_bank": c["receiver_bank"],
            "receiver_account": c["receiver_account"],
            "amount_paid": float(c["amount_paid"]),
            "payment_currency": c["payment_currency"],
            "amount_received": float(c["amount_received"]),
            "receiving_currency": c["receiving_currency"],
            "payment_format": c["payment_format"],
        },
        "subject_account": c["subject_account"],
        "subject_side": c["subject_side"],
        "rules_fired": c["rules_fired"],
        "score": c["score"],
        "features": feats,
        "created_at": promoted_at.replace(tzinfo=timezone.utc).isoformat(),
    }
