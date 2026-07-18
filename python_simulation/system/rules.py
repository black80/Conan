"""THE RULES.

Ten weighted rules, each a plain function of (txn, features, thresholds).
This file is the single place to read, add, or change a rule. Metadata
(weights, subject sides, quota policy) lives in the RULES table below;
engine.py mirrors the conditions as polars expressions for the batch path --
eval/parity.py proves the two never drift.

Weight design (docs/HANDOFF.md bug #5): a single typology rule must outrank
any stack of the correlated generic rules (full generic stack = 65), or
busy-legit accounts crowd out mules. Deliberate exception: STRUCTURING (50)
sits below a full stack -- on HI-Small it is the noisiest rule and the quota
guarantees its queue presence anyway (see docs/RESULTS.md).

subject_side is the account a rule INDICTS: FAN_IN_SPIKE describes the
receiver collecting, so a case opened off it must be about the receiver, not
the payer who happened to trip it.

Thresholds marked thr[...] are CALIBRATED, not hardcoded: the batch engine
computes them as the account-level 99th percentile (python -m system.engine)
and run_system.py loads them from out/eval_report.json.
"""

from __future__ import annotations

import json
from pathlib import Path


# --------------------------------------------------------------- the 10 rules

def fan_in_spike(txn, f, thr):
    """Mule collection: many distinct senders feed this receiver, almost
    nothing leaves. collect_ratio is hub-invariant -- a bank intermediates
    (ratio ~ 1); a mule collects (ratio >> 1). Floor avoids 1-payment noise."""
    return f["collect_ratio"] > thr["collect_ratio"] and f["r_fan_in"] >= 5


def fan_out_spray(txn, f, thr):
    """Distribution: this sender sprays many distinct receivers while barely
    receiving. The sender-side twin of FAN_IN_SPIKE."""
    return f["spray_ratio"] > thr["spray_ratio"] and f["fan_out"] >= 5


def pass_through(txn, f, thr):
    """Layering middle-man: >=90% of the last 24h's inflow went straight back
    out, in few payments. Self-transfers excluded -- a lone Reinvestment row
    would otherwise score as a perfect pass-through."""
    return (f["passthrough_ratio"] > 0.90
            and f["sender_inflow_1d"] > 10_000
            and f["n_out_1d"] <= 5
            and txn["sender_account"] != txn["receiver_account"])


def structuring(txn, f, thr):
    """Staying under the reporting threshold: >=5 payments in 24h totalling
    >30k with every single one under 10k."""
    return (f["n_out_1d"] >= 5 and f["amt_out_1d"] > 30_000
            and f["max_out_1d"] < 10_000)


def rapid_inflow(txn, f, thr):
    """Burst of inbound payments to an account that skews collector-shaped."""
    return f["r_n_in_1d"] > thr["r_n_in_1d"] and f["collect_ratio"] >= 2


def high_amount(txn, f, thr):
    return txn["amount_paid"] > thr["amount_paid"]


def high_velocity(txn, f, thr):
    return f["n_out_1d"] > thr["n_out_1d"]


def large_agg_out(txn, f, thr):
    return f["amt_out_1d"] > thr["amt_out_1d"]


def amount_deviation(txn, f, thr):
    """Payment >10x this sender's lifetime average (no baseline -> no fire)."""
    avg = f["sender_avg"]
    return avg is not None and avg > 0 and txn["amount_paid"] > 10 * avg


def cross_currency(txn, f, thr):
    return txn["payment_currency"] != txn["receiving_currency"]


# (name, weight, subject_side, check) -- keep weight-descending: the first
# FIRED rule in this order is the alert's dominant_rule.
RULES = [
    ("FAN_IN_SPIKE", 80, "receiver", fan_in_spike),
    ("FAN_OUT_SPRAY", 80, "sender", fan_out_spray),
    ("PASS_THROUGH", 70, "sender", pass_through),
    ("STRUCTURING", 50, "sender", structuring),
    ("RAPID_INFLOW", 15, "receiver", rapid_inflow),
    ("HIGH_AMOUNT", 10, "sender", high_amount),
    ("HIGH_VELOCITY", 10, "sender", high_velocity),
    ("LARGE_AGG_OUT", 10, "sender", large_agg_out),
    ("AMOUNT_DEVIATION", 10, "sender", amount_deviation),
    ("CROSS_CURRENCY", 10, "sender", cross_currency),
]

TYPOLOGY_RULES = {"FAN_IN_SPIKE", "FAN_OUT_SPRAY", "PASS_THROUGH", "STRUCTURING"}

# Budget quota is round-robined across these (docs/RESULTS.md: STRUCTURING
# excluded -- it maps to no HI-Small typology; it still competes in the fill).
QUOTA_RULES = sorted(TYPOLOGY_RULES - {"STRUCTURING"})

# feature-store fields shipped inside Alert.features (the agent's opening view)
FEATURE_KEYS = [
    # sender volumes, 24h + 7d
    "fan_in", "fan_out", "n_out_1d", "n_out_7d", "amt_out_1d", "amt_out_7d",
    "max_out_1d", "n_in_1d", "n_in_7d", "sender_inflow_1d", "sender_inflow_7d",
    "net_flow_1d", "net_flow_7d",
    # sender first-seen inflow (money from NEW senders) + recency + baseline
    "fs_in_cnt_1d", "fs_in_sum_1d", "fs_in_cnt_7d", "fs_in_sum_7d",
    "days_since_last_out", "days_since_last_in", "sender_avg", "burst_out",
    # receiver
    "r_fan_in", "r_fan_out", "r_n_in_1d", "r_n_in_7d", "r_amt_in_1d",
    "r_amt_in_7d", "r_n_out_1d", "r_amt_out_1d",
    "r_fs_in_cnt_1d", "r_fs_in_sum_1d", "r_fs_in_cnt_7d", "r_fs_in_sum_7d",
    "r_days_since_last_in", "r_burst_in",
    # ratios + the pair relationship
    "collect_ratio", "spray_ratio", "passthrough_ratio",
    "is_first_seen_pair", "pair_n_txns", "pair_age_days",
    # subject view (filled by score())
    "subject_burst", "subject_fan",
]

_WEIGHTS = {n: w for n, w, _, _ in RULES}
_SIDES = {n: sd for n, _, sd, _ in RULES}


def load_thresholds(path: str | None = None) -> dict[str, float]:
    p = Path(path) if path else Path(__file__).parent.parent / "out" / "eval_report.json"
    doc = json.loads(p.read_text())
    return doc["thresholds"] if "thresholds" in doc else doc


def score(txn: dict, f: dict, thr: dict) -> dict:
    """Run every rule against one transaction's feature row. Returns the
    scoring verdict: what fired, the weighted score, and WHO is indicted
    (subject = the side of the strongest fired rule; ties go to the sender)."""
    fired = [name for name, _, _, check in RULES if check(txn, f, thr)]
    max_w = {"sender": 0, "receiver": 0}
    for n in fired:
        if _WEIGHTS[n] > max_w[_SIDES[n]]:
            max_w[_SIDES[n]] = _WEIGHTS[n]
    subj_recv = max_w["receiver"] > max_w["sender"]
    return {
        "score": sum(_WEIGHTS[n] for n in fired),
        "rules_fired": fired,
        "dominant_rule": fired[0] if fired else "NONE",
        "typology_score": max(
            (_WEIGHTS[n] for n in fired if n in TYPOLOGY_RULES), default=0),
        "subject_account": txn["receiver_account"] if subj_recv else txn["sender_account"],
        "subject_side": "receiver" if subj_recv else "sender",
        "subject_burst": f["r_burst_in"] if subj_recv else f["burst_out"],
        "subject_fan": f["r_fan_in"] if subj_recv else f["fan_out"],
    }
