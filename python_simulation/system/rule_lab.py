"""RULE LAB: victim report -> agent-proposed rule -> real backtest.

The agent PROPOSES a rule as constrained JSON (a conjunction of feature
comparisons -- the exact shape of the existing 10 rules); a validator gates it;
the backtest runs the compiled predicate over the full compact feature frame
(5M txns, in RAM) and reports what it would catch and at what cost. The model
never executes code and never computes a metric; every number comes from the
frame (REQUIREMENTS.md F3, D9).

Ground-truth labels are used ONLY here, offline, to score candidate rules --
exactly like the batch engine's calibration. The investigation agent never
sees them.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import polars as pl

from agent import tools

# the DSL's feature space = the compact frame's feature columns (txn-grain,
# already in RAM -- backtests take seconds). On one txn row the r_*/collect
# features describe the RECEIVER and the rest describe the SENDER -- a rule
# must stay on one side (the existing 10 rules all do), else its conjuncts
# talk about two different accounts.
SIDE_OF = {
    "collect_ratio": "receiver", "r_fan_in": "receiver", "r_fan_out": "receiver",
    "r_burst_in": "receiver", "r_n_in_1d": "receiver",
    "spray_ratio": "sender", "passthrough_ratio": "sender", "fan_in": "sender",
    "fan_out": "sender", "burst_out": "sender", "n_out_1d": "sender",
    "days_since_last_out": "sender", "days_since_last_in": "sender",
}
FEATURES = list(SIDE_OF)
OPS = {">": pl.Expr.gt, ">=": pl.Expr.ge, "<": pl.Expr.lt, "<=": pl.Expr.le}
STREAM_DAYS = 17

_QUANTILES: dict | None = None      # feature -> {q50, q90, q99, q999}; computed once

PROPOSE_TOOL = {
    "name": "propose_rule",
    "description": "Propose one candidate detection rule as constrained DSL.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "SCREAMING_SNAKE rule name, e.g. FS_INFLOW_BURST"},
            "subject_side": {"type": "string", "enum": ["sender", "receiver"],
                             "description": "which side of the txn the rule indicts"},
            "weight": {"type": "integer", "minimum": 10, "maximum": 100},
            "conjuncts": {
                "type": "array", "minItems": 1, "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "feature": {"type": "string", "enum": FEATURES},
                        "op": {"type": "string", "enum": [">", ">=", "<", "<="]},
                        "threshold": {"type": "number"},
                    },
                    "required": ["feature", "op", "threshold"],
                    "additionalProperties": False,
                },
            },
            "rationale": {"type": "string",
                          "description": "your full reasoning, 5-10 sentences: "
                                         "what stands out in the case's rows vs "
                                         "the population quantiles; which sample "
                                         "row you anchored on; why you picked "
                                         "each feature and each threshold; what "
                                         "legitimate activity could also trigger "
                                         "this rule; and the alert-volume vs "
                                         "precision tradeoff you expect the "
                                         "analyst to weigh"},
        },
        "required": ["name", "subject_side", "weight", "conjuncts", "rationale"],
        "additionalProperties": False,
    },
}

PROPOSE_SYSTEM = """You are an AML detection engineer. A fraud case was reported by a \
victim -- the rule engine never alerted on it. Your job: propose ONE new rule, \
in the constrained DSL, that would have caught this case without flooding the \
queue.

The case data is split BY SIDE: the case's behavior as a RECEIVER (features \
collect_ratio, r_fan_in, r_fan_out, r_burst_in, r_n_in_1d) and as a SENDER \
(spray_ratio, passthrough_ratio, fan_in, fan_out, burst_out, n_out_1d, \
days_since_last_*). First choose subject_side = the side where the case looks \
most abnormal vs the population quantiles. Then use ONLY that side's features.

HARD CONSTRAINTS (the validator rejects violations):
1. Every conjunct's feature must belong to your chosen side -- mixed-side \
conjuncts describe two different accounts on the same transaction.
2. All conjuncts must hold SIMULTANEOUSLY on at least one of that side's \
sample rows (given below). Marginal peaks happen on different transactions. \
Pick ONE sample row and derive every threshold from that row's values \
(threshold <= that row's value for '>=').

Aim thresholds between q99 and the row's value -- low enough to catch the \
case, high enough to exclude the population; if the case is not separable at \
q99, use the row's 2-3 most extreme features at lower thresholds and let the \
backtest price the volume cost honestly.

Avoid duplicating an existing rule's condition. You never compute metrics -- \
the engine simulates your rule over the full stream and reports precision \
(share of new alerts that are real) and overlap_rate with existing rules; a \
human analyst then decides whether the tradeoff is worth it. Aim to catch the \
case with the fewest new alerts per day. In your rationale, write your full \
reasoning out loud -- the analyst reads it verbatim when judging the rule. \
Call propose_rule exactly once."""


def quantiles() -> dict:
    """Population quantiles per DSL feature, computed once from the frame."""
    global _QUANTILES
    if _QUANTILES is None:
        feat = tools._FEAT
        qs = {}
        for f in FEATURES:
            col = feat[f].drop_nulls()
            qs[f] = {"q50": col.quantile(0.5), "q90": col.quantile(0.9),
                     "q99": col.quantile(0.99), "q999": col.quantile(0.999)}
        _QUANTILES = qs
    return _QUANTILES


def validate(rule: dict) -> list[str]:
    """Gate a proposed rule: structure, known features, sane thresholds, and
    SIDE-PURITY (every conjunct's feature must describe the rule's subject
    side). Joint seed-satisfiability is checked separately (satisfiable())."""
    problems = []
    side = rule.get("subject_side")
    if side not in ("sender", "receiver"):
        problems.append("subject_side must be sender|receiver")
    if not isinstance(rule.get("weight"), int) or not 10 <= rule["weight"] <= 100:
        problems.append("weight must be an int in [10, 100]")
    conjuncts = rule.get("conjuncts") or []
    if not 1 <= len(conjuncts) <= 3:
        problems.append("need 1-3 conjuncts")
    qs = quantiles()
    for c in conjuncts:
        f = c.get("feature")
        if f not in FEATURES:
            problems.append(f"unknown feature {f!r}")
            continue
        if side in ("sender", "receiver") and SIDE_OF[f] != side:
            problems.append(f"{f} describes the {SIDE_OF[f]}, but the rule "
                            f"indicts the {side} -- mixed-side conjuncts "
                            f"describe two different accounts")
        if c.get("op") not in OPS:
            problems.append(f"bad op {c.get('op')!r}")
            continue
        thr = c.get("threshold")
        if not isinstance(thr, (int, float)):
            problems.append(f"{f}: threshold must be a number")
            continue
        # sanity band: a lower-bound threshold below the median matches half the
        # bank; far above q99.9 matches nothing. (upper-bound ops exempt.)
        if c["op"] in (">", ">=") and not (
                qs[f]["q50"] <= thr <= qs[f]["q999"] * 100):
            problems.append(f"{f}: threshold {thr} outside sane band "
                            f"[{qs[f]['q50']:.3g}, {qs[f]['q999'] * 100:.3g}]")
    return problems


def _subject_window(report: dict) -> pl.DataFrame:
    """The reported account's txn-grain feature rows inside the window."""
    acc = report["subject_account"]
    lo, hi = report["window_start"], report["window_end"]
    feat = tools._FEAT
    return feat.filter(
        ((pl.col("sender_account") == acc) | (pl.col("receiver_account") == acc))
        & (pl.col("ts") >= pl.lit(lo).str.to_datetime("%Y-%m-%d"))
        & (pl.col("ts") < pl.lit(hi).str.to_datetime("%Y-%m-%d") + timedelta(days=1))
    )


def contrast_pack(report: dict) -> dict:
    """What the agent sees, split BY SIDE: for each of sender/receiver, the
    case's peak values on that side's features (only over rows where the case
    IS that side) plus its most extreme actual rows. Conjunctions must hold on
    ONE row of one side -- marginal peaks land on different transactions and
    mixed-side conjuncts describe two different accounts (both learned the
    hard way)."""
    acc = report["subject_account"]
    sub = _subject_window(report)
    if sub.is_empty():
        return {"n_txns": 0, "sides": {}}
    qs = quantiles()
    sides = {}
    for side in ("receiver", "sender"):
        rows_side = sub.filter(pl.col(f"{side}_account") == acc)
        if rows_side.is_empty():
            continue
        feats = [f for f in FEATURES if SIDE_OF[f] == side]
        marginal = {}
        for f in feats:
            peak = rows_side[f].drop_nulls().max()
            if peak is None:
                continue
            marginal[f] = {"case_peak": round(float(peak), 3),
                           **{k: round(float(v), 3) for k, v in qs[f].items()}}
        extremeness = sorted(marginal, key=lambda f: -(marginal[f]["case_peak"]
                                                       / (qs[f]["q99"] or 1)))
        samples, seen = [], set()
        for f in extremeness[:3]:
            row = rows_side.sort(f, descending=True, nulls_last=True).head(1)
            key = str(row["ts"][0])
            if key in seen:
                continue
            seen.add(key)
            samples.append({c: round(float(v), 3)
                            for c, v in row.select(feats).to_dicts()[0].items()
                            if v is not None})
        sides[side] = {"n_txns": rows_side.height, "features": marginal,
                       "sample_rows": samples}
    return {"n_txns": sub.height, "sides": sides}


def satisfiable(rule: dict, report: dict) -> bool:
    """Does one row WHERE THE CASE IS THE RULE'S SUBJECT SIDE satisfy all
    conjuncts?"""
    sub = _subject_window(report).filter(
        pl.col(f"{rule['subject_side']}_account") == report["subject_account"])
    if sub.is_empty():
        return False
    mask = pl.lit(True)
    for c in rule["conjuncts"]:
        mask = mask & OPS[c["op"]](pl.col(c["feature"]), c["threshold"])
    return sub.filter(mask).height > 0


def propose(report: dict, existing_rules: list[dict], client, model: str,
            feedback: str | None = None) -> dict:
    """One Claude call -> rule DSL dict (validated upstream). `feedback` is
    the validator's rejection of a prior draft -- one retry, no more."""
    pack = contrast_pack(report)
    prompt = (
        f"VICTIM REPORT: account {report['subject_account']}, window "
        f"{report['window_start']}..{report['window_end']}. "
        f"Investigator's description: {report.get('description') or '(none)'}\n\n"
        f"CASE vs POPULATION, BY SIDE ({pack['n_txns']} txns in window; "
        f"case_peak = the account's worst value on that side; sample_rows = "
        f"actual rows to derive thresholds from):\n"
        f"```json\n{json.dumps(pack['sides'], indent=1)}\n```\n\n"
        f"EXISTING RULES (do not duplicate):\n"
        f"```json\n{json.dumps(existing_rules, indent=1)}\n```\n\n"
        + (f"YOUR PREVIOUS DRAFT WAS REJECTED BY THE VALIDATOR:\n{feedback}\n"
           f"Fix exactly these problems (drop a conjunct rather than bend a "
           f"threshold below its sane band) and propose again.\n\n"
           if feedback else "")
        + "Propose the rule now."
    )
    response = client.messages.create(
        model=model, max_tokens=2_048,
        system=PROPOSE_SYSTEM,
        tools=[PROPOSE_TOOL],
        tool_choice={"type": "tool", "name": "propose_rule"},
        messages=[{"role": "user", "content": prompt}],
    )
    tu = next(b for b in response.content if b.type == "tool_use")
    return dict(tu.input)


def backtest(rule: dict, laund: set, existing_case_days: set,
             report: dict | None = None) -> dict:
    """Score a validated rule over the FULL frame. Every number here is
    computed from data -- the model never touches this path."""
    feat = tools._FEAT
    mask = pl.lit(True)
    for c in rule["conjuncts"]:
        mask = mask & OPS[c["op"]](pl.col(c["feature"]), c["threshold"])
    subject = ("receiver_account" if rule["subject_side"] == "receiver"
               else "sender_account")
    hits = (feat.filter(mask)
            .select(pl.col(subject).alias("subject"),
                    pl.col("ts").dt.date().alias("day")))
    case_days = set(hits.unique().iter_rows())
    n_txn = hits.height

    incremental = case_days - existing_case_days
    union = case_days | existing_case_days
    jacc = (len(case_days & existing_case_days) / len(union)) if union else 0.0
    incr_real = sum(1 for cd in incremental if cd in laund)
    incr_prec = incr_real / len(incremental) if incremental else 0.0

    caught = False
    if report is not None:
        lo = date.fromisoformat(report["window_start"])
        hi = date.fromisoformat(report["window_end"])
        caught = any(s == report["subject_account"] and lo <= d <= hi
                     for s, d in case_days)

    # metrics only -- no verdict. The analyst reads these and decides.
    return {
        "catches_case": caught,
        "new_alerts_per_day": round(len(incremental) / STREAM_DAYS, 1),
        "precision": round(incr_prec, 4),           # share of new alerts that are real
        "real_in_new": incr_real,
        "new_cases": len(incremental),
        "overlap_rate": round(jacc, 4),             # duplication vs existing rules
        "txn_matches": n_txn,
        "case_days_total": len(case_days),
    }
