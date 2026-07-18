"""Batch twin of the streaming detector: calibration, evaluation, tuning.

Rules are a sampler, not a detector: the target is a small, high-yield alert
stream for the agent, not F1. This module computes the calibrated thresholds
the live system loads (out/eval_report.json) and measures precision / lift /
typology coverage. The live path is system/feature_store.py + system/rules.py
+ run_system.py; eval/parity.py proves the two compute identical features
and scores.

Pipeline:
    load()      scan_csv -> rename dup Account -> parse ts -> ts_u = ts + us(2*txn_id)
    features()  ONE combined event stream (each txn -> sender-out + receiver-in event)
                -> expanding distinct counterparties -> rolling 1d sums/counts
                -> hub-invariant ratios
    calibrate() thresholds from the ACCOUNT-level quantile, never the txn-level one
    score()     weighted rules -> score + rules_fired + subject_account
    dedup()     one case per (subject_account, day), typology quota, top budget

Regression guards (see HANDOFF.md section 4):
    #1 all windows/joins keyed on unique ts_u/te, never raw minute-resolution ts
    #2 dedup to (subject_account, day)
    #3 calibration on group_by(account).max() then quantile
    #4 thresholds on collect_ratio/spray_ratio (hub-invariant), floors for tiny accounts
    #5 typology weights (80/80/70/50) must outrank any stack of generic 10-15s
    #6 both sides of every ratio come from the SAME account (single event stream)
    #7 expanding distinct count via is_first_distinct + cum_sum, never rolling n_unique

Usage (from the repo root):
    python -m system.engine HI-Small_Trans.csv HI-Small_Patterns.txt --budget 500
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import polars as pl

from .rules import QUOTA_RULES, RULES, TYPOLOGY_RULES

CALIBRATION_Q = 0.99
OUT_DIR = Path(__file__).parent.parent / "out"

CANONICAL = [
    "ts_raw", "sender_bank", "sender_account", "receiver_bank", "receiver_account",
    "amount_received", "receiving_currency", "amount_paid", "payment_currency",
    "payment_format", "is_laundering",
]

TYPOLOGIES = ["FAN-IN", "FAN-OUT", "GATHER-SCATTER", "SCATTER-GATHER", "CYCLE",
              "RANDOM", "BIPARTITE", "STACK"]


# --------------------------------------------------------------------------- load

def load(path: str, rows: int | None = None) -> pl.LazyFrame:
    lf = pl.scan_csv(path, infer_schema_length=1000)
    names = lf.collect_schema().names()
    if len(names) != len(CANONICAL):
        raise SystemExit(f"expected {len(CANONICAL)} columns, got {len(names)}: {names}")
    # The CSV has 'Account' twice (sender then receiver); polars renames the second
    # to 'Account_duplicated_0'. Rename by POSITION so we don't care what it picked.
    lf = lf.rename(dict(zip(names, CANONICAL)))
    if rows:
        lf = lf.head(rows)
    lf = (
        lf.with_row_index("txn_id")
        .with_columns(
            pl.col("ts_raw").str.to_datetime("%Y/%m/%d %H:%M").alias("ts"),
            pl.col("sender_bank").cast(pl.Utf8),
            pl.col("receiver_bank").cast(pl.Utf8),
            pl.col("amount_paid").cast(pl.Float32),
            pl.col("amount_received").cast(pl.Float32),
            pl.col("is_laundering").cast(pl.Int8),
        )
        .drop("ts_raw")
        # Bug #1: minute-resolution timestamps tie by the thousands. Every window
        # and join keys on a unique, monotonic timestamp. 2*txn_id leaves room for
        # the +1us sender event offset in the event frame (self-transfers collide
        # otherwise). Max offset ~10s << the 60s resolution, so order is preserved.
        .with_columns(
            (pl.col("ts") + pl.duration(microseconds=2 * pl.col("txn_id"))).alias("ts_u")
        )
    )
    n = lf.select(pl.len()).collect().item()
    if 2 * n + 1 >= 60_000_000:
        # past ~30M rows the us offsets spill over the 60s resolution and ts_u
        # can collide across minutes on an unsorted file (HI-Med/Large territory)
        raise RuntimeError(f"{n} rows: ts_u offset would exceed the 60s "
                           "timestamp resolution; shrink or shard the file")
    return lf


# ----------------------------------------------------------------------- features

def features(df: pl.DataFrame) -> pl.DataFrame:
    """As-of-transaction account state. No future leakage: every value at event t
    is computed from events <= t only (expanding counts include the current event,
    matching 'state as of this transaction')."""
    # Bug #6: ONE combined event stream so an account's in-fan and out-fan always
    # come from the same account. dir=1 sender-outbound, dir=0 receiver-inbound.
    ev = pl.concat([
        df.select(
            "txn_id",
            pl.col("sender_account").alias("account"),
            pl.col("receiver_account").alias("cp"),
            pl.lit(1, pl.Int8).alias("dir"),
            pl.col("amount_paid").alias("amount"),
            (pl.col("ts_u") + pl.duration(microseconds=1)).alias("te"),
        ),
        df.select(
            "txn_id",
            pl.col("receiver_account").alias("account"),
            pl.col("sender_account").alias("cp"),
            pl.lit(0, pl.Int8).alias("dir"),
            pl.col("amount_received").alias("amount"),
            pl.col("ts_u").alias("te"),
        ),
    ]).sort("te")

    # Aggregations run in f64: hub accounts accumulate 1e10-scale rolling sums,
    # where f32 (~7 significant digits) silently drops individual 1e3-1e4 txns.
    out_flag = (pl.col("dir") == 1).cast(pl.Float64)
    in_flag = (pl.col("dir") == 0).cast(pl.Float64)

    ev = ev.with_columns(
        # Bug #7: expanding distinct counterparty count in ONE pass. Never
        # rolling n_unique (quadratic on hub accounts).
        (pl.struct("account", "cp", "dir").is_first_distinct() & (pl.col("dir") == 1))
        .alias("first_out"),
        (pl.struct("account", "cp", "dir").is_first_distinct() & (pl.col("dir") == 0))
        .alias("first_in"),
        (out_flag * pl.col("amount")).alias("out_amt"),
        (in_flag * pl.col("amount")).alias("in_amt"),
        out_flag.alias("out_flag"),
        in_flag.alias("in_flag"),
    ).with_columns(
        pl.col("first_out").cast(pl.UInt32).cum_sum().over("account").alias("fan_out"),
        pl.col("first_in").cast(pl.UInt32).cum_sum().over("account").alias("fan_in"),
    )

    # Rolling state per account over {24h, 7d}, on the interleaved stream: at
    # ANY event we know both the account's inbound and outbound window totals
    # (pass-through needs both at the sender). Keyed on te (unique), grouped by
    # account. fs_* = inbound restricted to FIRST-SEEN senders (the first_in
    # flag): "money arriving from new counterparties" -- the mule signature.
    fs_in_amt = pl.col("first_in").cast(pl.Float64) * pl.col("in_amt")
    ev = ev.with_columns(
        pl.col("out_flag").rolling_sum_by("te", "1d").over("account").alias("n_out_1d"),
        pl.col("out_flag").rolling_sum_by("te", "7d").over("account").alias("n_out_7d"),
        pl.col("out_amt").rolling_sum_by("te", "1d").over("account").alias("amt_out_1d"),
        pl.col("out_amt").rolling_sum_by("te", "7d").over("account").alias("amt_out_7d"),
        pl.col("out_amt").rolling_max_by("te", "1d").over("account").alias("max_out_1d"),
        pl.col("in_flag").rolling_sum_by("te", "1d").over("account").alias("n_in_1d"),
        pl.col("in_flag").rolling_sum_by("te", "7d").over("account").alias("n_in_7d"),
        pl.col("in_amt").rolling_sum_by("te", "1d").over("account").alias("amt_in_1d"),
        pl.col("in_amt").rolling_sum_by("te", "7d").over("account").alias("amt_in_7d"),
        pl.col("first_in").cast(pl.Float64).rolling_sum_by("te", "1d")
        .over("account").alias("fs_in_cnt_1d"),
        pl.col("first_in").cast(pl.Float64).rolling_sum_by("te", "7d")
        .over("account").alias("fs_in_cnt_7d"),
        fs_in_amt.rolling_sum_by("te", "1d").over("account").alias("fs_in_sum_1d"),
        fs_in_amt.rolling_sum_by("te", "7d").over("account").alias("fs_in_sum_7d"),
        # new distinct payees in the last 24h (burst_out numerator)
        pl.col("first_out").cast(pl.Float64).rolling_sum_by("te", "1d")
        .over("account").alias("new_out_1d"),
        # lifetime mean outbound BEFORE this event (a first-ever payment has no
        # baseline -> null -> AMOUNT_DEVIATION cannot fire on it)
        ((pl.col("out_amt").cum_sum().over("account") - pl.col("out_amt"))
         / (pl.col("out_flag").cum_sum().over("account") - pl.col("out_flag")))
        .alias("prior_avg"),
        # recency: te of the account's last out/in event BEFORE this one
        pl.when(pl.col("dir") == 1).then(pl.col("te")).otherwise(None)
        .forward_fill().shift(1).over("account").alias("_last_out_te"),
        pl.when(pl.col("dir") == 0).then(pl.col("te")).otherwise(None)
        .forward_fill().shift(1).over("account").alias("_last_in_te"),
        # the (account, cp, direction) pair: nth txn + relationship age
        (pl.int_range(pl.len()) + 1).over(["account", "cp", "dir"])
        .cast(pl.UInt32).alias("pair_n_txns"),
        pl.col("te").cum_min().over(["account", "cp", "dir"]).alias("_pair_first_te"),
    ).with_columns(
        # Burstiness: share of the account's lifetime distinct counterparties
        # acquired in the last 24h. A mule assembles its fan inside the attempt
        # window (-> 1.0); a merchant accumulates it over the whole file (-> ~0).
        # Hub-invariant AND time-aware, no threshold needed: used for ranking.
        (pl.col("new_out_1d") / pl.col("fan_out").clip(lower_bound=1)).alias("burst_out"),
        (pl.col("fs_in_cnt_1d") / pl.col("fan_in").clip(lower_bound=1)).alias("burst_in"),
        ((pl.col("te") - pl.col("_last_out_te")).dt.total_microseconds() / 86_400_000_000)
        .alias("days_since_last_out"),
        ((pl.col("te") - pl.col("_last_in_te")).dt.total_microseconds() / 86_400_000_000)
        .alias("days_since_last_in"),
        ((pl.col("te") - pl.col("_pair_first_te")).dt.total_microseconds() / 86_400_000_000)
        .alias("pair_age_days"),
    )

    sender_state = ev.filter(pl.col("dir") == 1).select(
        "txn_id", "fan_in", "fan_out",
        pl.col("n_out_1d").cast(pl.UInt32), pl.col("n_out_7d").cast(pl.UInt32),
        "amt_out_1d", "amt_out_7d", "max_out_1d",
        pl.col("n_in_1d").cast(pl.UInt32), pl.col("n_in_7d").cast(pl.UInt32),
        pl.col("amt_in_1d").alias("sender_inflow_1d"),
        pl.col("amt_in_7d").alias("sender_inflow_7d"),
        pl.col("fs_in_cnt_1d").cast(pl.UInt32), pl.col("fs_in_cnt_7d").cast(pl.UInt32),
        "fs_in_sum_1d", "fs_in_sum_7d",
        "days_since_last_out", "days_since_last_in",
        "burst_out",
        pl.when(pl.col("prior_avg").is_finite()).then(pl.col("prior_avg"))
        .alias("sender_avg"),
        pl.col("first_out").alias("is_first_seen_pair"),
        "pair_n_txns", "pair_age_days",
    )
    recv_state = ev.filter(pl.col("dir") == 0).select(
        "txn_id",
        pl.col("fan_in").alias("r_fan_in"),
        pl.col("fan_out").alias("r_fan_out"),
        pl.col("n_in_1d").cast(pl.UInt32).alias("r_n_in_1d"),
        pl.col("n_in_7d").cast(pl.UInt32).alias("r_n_in_7d"),
        pl.col("amt_in_1d").alias("r_amt_in_1d"),
        pl.col("amt_in_7d").alias("r_amt_in_7d"),
        pl.col("n_out_1d").cast(pl.UInt32).alias("r_n_out_1d"),
        pl.col("amt_out_1d").alias("r_amt_out_1d"),
        pl.col("fs_in_cnt_1d").cast(pl.UInt32).alias("r_fs_in_cnt_1d"),
        pl.col("fs_in_cnt_7d").cast(pl.UInt32).alias("r_fs_in_cnt_7d"),
        pl.col("fs_in_sum_1d").alias("r_fs_in_sum_1d"),
        pl.col("fs_in_sum_7d").alias("r_fs_in_sum_7d"),
        pl.col("days_since_last_in").alias("r_days_since_last_in"),
        pl.col("burst_in").alias("r_burst_in"),
    )

    n = df.height
    feat = df.join(sender_state, on="txn_id", how="inner").join(
        recv_state, on="txn_id", how="inner"
    )
    # Bug #1 guard: state joins are 1:1 hash joins on txn_id. Any explosion here
    # means a keying regression. (RuntimeError, not assert: must survive -O.)
    if feat.height != n:
        raise RuntimeError(f"join changed row count: {n} -> {feat.height}")

    return feat.with_columns(
        # Bug #4: hub-invariant ratios. A bank intermediates (ratio ~ 1);
        # a mule collects or sprays (ratio >> 1). Levels find banks, ratios find mules.
        (pl.col("r_fan_in") / (pl.col("r_fan_out") + 1)).alias("collect_ratio"),
        (pl.col("fan_out") / (pl.col("fan_in") + 1)).alias("spray_ratio"),
        pl.when(pl.max_horizontal("sender_inflow_1d", "amt_out_1d") > 0)
        .then(pl.min_horizontal("sender_inflow_1d", "amt_out_1d")
              / pl.max_horizontal("sender_inflow_1d", "amt_out_1d"))
        .otherwise(0.0)
        .alias("passthrough_ratio"),
        # accumulating (mule collecting) vs passing (layering) vs spending
        (pl.col("sender_inflow_1d") - pl.col("amt_out_1d")).alias("net_flow_1d"),
        (pl.col("sender_inflow_7d") - pl.col("amt_out_7d")).alias("net_flow_7d"),
    )


# ---------------------------------------------------------------------- calibrate

def _account_quantile(feat: pl.DataFrame, col: str, acct: str, q: float) -> float:
    # Bug #3: quantile of the ACCOUNT distribution (one row per account), never the
    # transaction distribution (hubs dominate it because txns are activity-weighted).
    return (
        feat.group_by(acct).agg(pl.col(col).max())
        .select(pl.col(col).quantile(q)).item()
    )


def calibrate(feat: pl.DataFrame, q: float = CALIBRATION_Q) -> dict[str, float]:
    thr = {
        "collect_ratio": _account_quantile(feat, "collect_ratio", "receiver_account", q),
        "spray_ratio": _account_quantile(feat, "spray_ratio", "sender_account", q),
        "r_n_in_1d": _account_quantile(feat, "r_n_in_1d", "receiver_account", q),
        "amount_paid": _account_quantile(feat, "amount_paid", "sender_account", q),
        "n_out_1d": _account_quantile(feat, "n_out_1d", "sender_account", q),
        "amt_out_1d": _account_quantile(feat, "amt_out_1d", "sender_account", q),
    }
    return {k: float(v) for k, v in thr.items()}


# -------------------------------------------------------------------------- score

def rule_exprs(thr: dict[str, float]) -> list[tuple[str, int, str, pl.Expr]]:
    """(name, weight, subject_side, condition) -- metadata comes from rules.RULES;
    only the polars conditions live here. rules.py implements the same
    conditions scalar-wise: keep them in sync (eval/parity.py enforces it)."""
    conds = {
        "FAN_IN_SPIKE":
            (pl.col("collect_ratio") > thr["collect_ratio"]) & (pl.col("r_fan_in") >= 5),
        "FAN_OUT_SPRAY":
            (pl.col("spray_ratio") > thr["spray_ratio"]) & (pl.col("fan_out") >= 5),
        # self-transfers excluded: a txn's own inbound leg lands 1us before its
        # outbound leg, so a lone Reinvestment row scores as a perfect
        # pass-through otherwise (5 benign alerts in the top-500 before the gate)
        "PASS_THROUGH":
            (pl.col("passthrough_ratio") > 0.90)
            & (pl.col("sender_inflow_1d") > 10_000) & (pl.col("n_out_1d") <= 5)
            & (pl.col("sender_account") != pl.col("receiver_account")),
        "STRUCTURING":
            (pl.col("n_out_1d") >= 5) & (pl.col("amt_out_1d") > 30_000)
            & (pl.col("max_out_1d") < 10_000),
        "RAPID_INFLOW":
            (pl.col("r_n_in_1d") > thr["r_n_in_1d"]) & (pl.col("collect_ratio") >= 2),
        "HIGH_AMOUNT": pl.col("amount_paid") > thr["amount_paid"],
        "HIGH_VELOCITY": pl.col("n_out_1d") > thr["n_out_1d"],
        "LARGE_AGG_OUT": pl.col("amt_out_1d") > thr["amt_out_1d"],
        "AMOUNT_DEVIATION":
            pl.col("sender_avg").is_not_null() & (pl.col("sender_avg") > 0)
            & (pl.col("amount_paid") > 10 * pl.col("sender_avg")),
        "CROSS_CURRENCY":
            pl.col("payment_currency") != pl.col("receiving_currency"),
    }
    assert set(conds) == {n for n, _, _, _ in RULES}, "rules.RULES and conditions diverged"
    return [(n, w, side, conds[n]) for n, w, side, _ in RULES]


def score(feat: pl.DataFrame, thr: dict[str, float]) -> pl.DataFrame:
    rules = rule_exprs(thr)
    fired = [cond.fill_null(False).alias(f"_r_{name}") for name, _, _, cond in rules]
    max_w = {
        side: pl.max_horizontal(
            pl.lit(0),
            *[pl.col(f"_r_{n}").cast(pl.Int32) * w for n, w, s, _ in rules if s == side],
        )
        for side in ("sender", "receiver")
    }
    scored = feat.with_columns(fired).with_columns(
        pl.sum_horizontal(
            *[pl.col(f"_r_{name}").cast(pl.Int32) * w for name, w, _, _ in rules]
        ).alias("score"),
        pl.concat_list(
            *[pl.when(pl.col(f"_r_{name}")).then(pl.lit(name)) for name, _, _, _ in rules]
        ).list.drop_nulls().alias("rules_fired"),
        pl.max_horizontal(
            pl.lit(0),
            *[pl.col(f"_r_{n}").cast(pl.Int32) * w
              for n, w, _, _ in rules if n in TYPOLOGY_RULES],
        ).alias("typology_score"),
        # dominant rule: highest-weight fired rule (rules listed weight-desc)
        pl.coalesce(
            *[pl.when(pl.col(f"_r_{name}")).then(pl.lit(name)) for name, _, _, _ in rules],
            pl.lit("NONE"),
        ).alias("dominant_rule"),
        (max_w["receiver"] > max_w["sender"]).alias("_subj_recv"),
    ).with_columns(
        pl.when(pl.col("_subj_recv")).then(pl.col("receiver_account"))
        .otherwise(pl.col("sender_account")).alias("subject_account"),
        pl.when(pl.col("_subj_recv")).then(pl.lit("receiver"))
        .otherwise(pl.lit("sender")).alias("subject_side"),
        pl.when(pl.col("_subj_recv")).then(pl.col("r_burst_in"))
        .otherwise(pl.col("burst_out")).alias("subject_burst"),
        pl.when(pl.col("_subj_recv")).then(pl.col("r_fan_in"))
        .otherwise(pl.col("fan_out")).alias("subject_fan"),
    )
    return scored.drop([f"_r_{name}" for name, _, _, _ in rules] + ["_subj_recv"])


# -------------------------------------------------------------------------- dedup

RANK = ["score", "subject_burst"]   # burst breaks ties inside a score band


def dedup(scored: pl.DataFrame, budget: int, stratify: bool = True,
          reserve: float = 0.5) -> pl.DataFrame:
    """One case per (subject_account, day), then the budget cut.

    Bug #2: an analyst investigates an ACCOUNT, not each payment. Keyed on the
    account the rules indict, so a hot receiver tripping FAN_IN_SPIKE for 22
    payers is ONE case, not 22.

    stratify=True (shipped config): rules are a sampler -- `reserve` of the
    budget is round-robined across QUOTA_RULES dominants (one loud rule cannot
    monopolize the queue), the rest filled by plain score. stratify=False is
    kept for budget-curve evaluation (prefixes of one ranked stream)."""
    alerts = (
        scored.filter(pl.col("score") > 0)
        .with_columns(pl.col("ts").dt.date().alias("day"))
        .sort(RANK + ["txn_id"], descending=[True, True, False])
        .group_by(["subject_account", "day"], maintain_order=True)
        .first()
        .sort(RANK + ["txn_id"], descending=[True, True, False])
    )
    if not stratify:
        return alerts.head(budget)
    n_reserved = int(budget * reserve)
    typ = (
        alerts.filter(pl.col("dominant_rule").is_in(QUOTA_RULES))
        .with_columns(pl.int_range(pl.len()).over("dominant_rule").alias("_rr"))
        .sort(["_rr"] + RANK + ["txn_id"], descending=[False, True, True, False])
        .drop("_rr")
        .head(n_reserved)
    )
    fill = alerts.join(typ.select("txn_id"), on="txn_id", how="anti")
    out = pl.concat([typ, fill.head(budget - typ.height)])
    return out.sort(RANK + ["txn_id"], descending=[True, True, False])


# --------------------------------------------------------------------- evaluation

def parse_patterns(path: str) -> pl.DataFrame:
    """Patterns.txt -> one row per (attempt_id, typology, account)."""
    rows = []
    attempt_id, typology = -1, None
    for line in Path(path).read_text().splitlines():
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            attempt_id += 1
            m = re.search(r"BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)", line)
            typology = m.group(1).rstrip("-") if m else "UNKNOWN"
        elif line.startswith("END LAUNDERING ATTEMPT"):
            typology = None
        elif typology is not None and line.strip():
            parts = line.split(",")
            if len(parts) >= 5:
                rows.append((attempt_id, typology, parts[2]))
                rows.append((attempt_id, typology, parts[4]))
    return pl.DataFrame(
        rows, schema=["attempt_id", "typology", "account"], orient="row"
    ).unique()


def evaluate(
    scored: pl.DataFrame,
    alerts_full: pl.DataFrame,
    patterns: pl.DataFrame | None,
    budgets: list[int],
) -> dict:
    """alerts_full: dedup'd alert stream at the LARGEST budget, score-desc.
    Smaller budgets are its prefixes, so one dedup serves every budget."""
    n_txn = scored.height
    n_laund = int(scored["is_laundering"].sum())
    base_rate = n_laund / n_txn

    # (account, day) pairs touched by laundering, from either side of the txn.
    laund = scored.filter(pl.col("is_laundering") == 1).with_columns(
        pl.col("ts").dt.date().alias("day")
    )
    laund_days = set(
        laund.select(pl.col("sender_account").alias("a"), "day").iter_rows()
    ) | set(laund.select(pl.col("receiver_account").alias("a"), "day").iter_rows())
    laund_accounts = set(laund["sender_account"]) | set(laund["receiver_account"])

    report: dict = {
        "n_txn": n_txn, "n_laundering_txn": n_laund, "base_rate": base_rate,
        "budgets": {},
    }

    for b in budgets:
        al = alerts_full.head(b)
        n = al.height
        txn_hits = int(al["is_laundering"].sum())
        day_hits = sum(
            (a, d) in laund_days
            for a, d in al.select("subject_account", "day").iter_rows()
        )
        acct_hits = sum(a in laund_accounts for a in al["subject_account"])
        report["budgets"][b] = {
            "alerts": n,
            "txn_precision": txn_hits / n if n else 0.0,
            "case_precision_same_day": day_hits / n if n else 0.0,
            "account_precision_any_day": acct_hits / n if n else 0.0,
            "lift_txn": (txn_hits / n) / base_rate if n and base_rate else 0.0,
        }

    # per-rule stats on the shipped (largest-budget) alert stream
    per_rule = {}
    for name, w, _, _ in rule_exprs({k: 0 for k in
                                     ["collect_ratio", "spray_ratio", "r_n_in_1d",
                                      "amount_paid", "n_out_1d", "amt_out_1d"]}):
        sub = alerts_full.filter(pl.col("rules_fired").list.contains(name))
        if sub.height:
            day_hits = sum(
                (a, d) in laund_days
                for a, d in sub.select("subject_account", "day").iter_rows()
            )
            per_rule[name] = {
                "weight": w, "alerts": sub.height,
                "txn_precision": float(sub["is_laundering"].mean()),
                "case_precision_same_day": day_hits / sub.height,
            }
    report["per_rule"] = per_rule

    if patterns is not None:
        alerted_subjects = set(alerts_full["subject_account"])
        alerted_any = (alerted_subjects | set(alerts_full["sender_account"])
                       | set(alerts_full["receiver_account"]))
        cov = {}
        for typ in TYPOLOGIES:
            p = patterns.filter(pl.col("typology") == typ)
            attempts = p["attempt_id"].unique().to_list()
            hit_strict = hit_lenient = 0
            for aid in attempts:
                accs = set(p.filter(pl.col("attempt_id") == aid)["account"])
                hit_strict += bool(accs & alerted_subjects)
                hit_lenient += bool(accs & alerted_any)
            cov[typ] = {
                "attempts": len(attempts),
                "hit_as_subject": hit_strict,
                "hit_any_side": hit_lenient,
            }
        report["typology_coverage"] = cov

    return report


def print_report(report: dict, thr: dict) -> None:
    print("\n=== calibrated thresholds (account-level q) ===")
    for k, v in thr.items():
        print(f"  {k:>16}: {v:,.2f}")

    print(f"\n=== dataset ===\n  {report['n_txn']:,} txns | "
          f"{report['n_laundering_txn']:,} laundering "
          f"({report['base_rate']:.4%} base rate)")

    print("\n=== alert stream ===")
    print(f"  {'budget':>7} | {'txn prec':>9} | {'case prec (same day)':>20} | "
          f"{'acct prec (any day)':>19} | {'txn lift':>8}")
    for b, m in report["budgets"].items():
        print(f"  {b:>7} | {m['txn_precision']:>9.2%} | "
              f"{m['case_precision_same_day']:>20.2%} | "
              f"{m['account_precision_any_day']:>19.2%} | "
              f"{m['lift_txn']:>7.0f}x")

    print("\n=== per-rule (within shipped alerts) ===")
    for name, s in sorted(report["per_rule"].items(),
                          key=lambda kv: -kv[1]["case_precision_same_day"]):
        print(f"  {name:>17} (w={s['weight']:>2}): {s['alerts']:>4} alerts | "
              f"txn {s['txn_precision']:>7.2%} | case {s['case_precision_same_day']:>7.2%}")

    if "typology_coverage" in report:
        print("\n=== typology coverage (attempts with >=1 involved account alerted) ===")
        for typ, c in report["typology_coverage"].items():
            print(f"  {typ:>15}: {c['hit_as_subject']:>3}/{c['attempts']:<3} as subject | "
                  f"{c['hit_any_side']:>3}/{c['attempts']:<3} any side")


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trans")
    ap.add_argument("patterns", nargs="?")
    ap.add_argument("--budget", type=int, default=500)
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--q", type=float, default=CALIBRATION_Q)
    ap.add_argument("--no-stratify", action="store_true",
                    help="plain score ranking (used for budget curves); default "
                         "is the typology quota")
    ap.add_argument("--reserve", type=float, default=0.5,
                    help="budget fraction reserved for typology-rule alerts")
    ap.add_argument("--out", default=str(OUT_DIR / "alerts.parquet"))
    ap.add_argument("--cache", default=None,
                    help="feature parquet; reused if it exists (threshold sweeps "
                         "then skip the expensive feature build)")
    ap.add_argument("--report-json", default=str(OUT_DIR / "eval_report.json"),
                    help="metrics + thresholds; run_system.py loads thresholds "
                         "from here")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    if args.cache and Path(args.cache).exists():
        print(f"loading cached features from {args.cache}", file=sys.stderr)
        feat = pl.read_parquet(args.cache)
    else:
        df = load(args.trans, args.rows).collect(engine="streaming")
        print(f"loaded {df.height:,} txns", file=sys.stderr)
        feat = features(df)
        del df
        if args.cache:
            feat.write_parquet(args.cache)
            print(f"cached features to {args.cache}", file=sys.stderr)

    thr = calibrate(feat, args.q)
    scored = score(feat, thr)

    # smaller budgets are prefixes of the ranked stream only when unstratified;
    # a quota scales with the budget, so stratified runs evaluate one budget
    budgets = (sorted({100, 200, 500, 1000, args.budget})
               if args.no_stratify else [args.budget])
    alerts_full = dedup(scored, max(budgets), stratify=not args.no_stratify,
                        reserve=args.reserve)
    alerts = alerts_full.head(args.budget)

    patterns = parse_patterns(args.patterns) if args.patterns else None
    report = evaluate(scored, alerts_full, patterns, budgets)
    report["calibration_q"] = args.q
    report["config"] = {"stratify": not args.no_stratify,
                        "reserve": args.reserve, "budget": args.budget}
    report["thresholds"] = thr
    print_report(report, thr)

    # is_laundering stays in the parquet for EVALUATION ONLY -- strip it before
    # the agent ever reads a case.
    alerts.drop("day").write_parquet(args.out)
    print(f"\nwrote {alerts.height} alerts -> {args.out}", file=sys.stderr)
    Path(args.report_json).write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote report -> {args.report_json}", file=sys.stderr)


if __name__ == "__main__":
    main()
