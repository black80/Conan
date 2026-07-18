"""Parity check: the streaming detector must produce EXACTLY the batch
engine's features and scores on the same transactions. Run on a prefix:

    python -m eval.parity [--rows 300000]

Any drift here means the live path (system/feature_store.py + system/rules.py)
and the batch twin (system/engine.py) have diverged -- fix before trusting
stream output.
"""

from __future__ import annotations

import argparse
import sys

import polars as pl

from run_system import replay
from system import feature_store, rules
from system.engine import calibrate, features, load, score

KAGGLE = ("~/.cache/kagglehub/datasets/ealtman2019/"
          "ibm-transactions-for-anti-money-laundering-aml/versions/8/"
          "HI-Small_Trans.csv")

COMPARE_F = ["collect_ratio", "spray_ratio", "n_out_1d", "r_n_in_1d",
             "amt_out_1d", "max_out_1d", "sender_inflow_1d",
             "passthrough_ratio", "burst_out", "r_burst_in",
             "amt_out_7d", "sender_inflow_7d", "net_flow_1d", "net_flow_7d",
             "fs_in_sum_1d", "fs_in_sum_7d", "r_amt_in_1d", "r_amt_in_7d",
             "r_amt_out_1d", "r_fs_in_sum_1d", "r_fs_in_sum_7d",
             "pair_age_days"]
COMPARE_I = ["fan_in", "fan_out", "r_fan_in", "r_fan_out", "score",
             "n_out_7d", "n_in_1d", "n_in_7d", "fs_in_cnt_1d", "fs_in_cnt_7d",
             "r_n_in_7d", "r_n_out_1d", "r_fs_in_cnt_1d", "r_fs_in_cnt_7d",
             "pair_n_txns", "is_first_seen_pair"]
COMPARE_NULLABLE = ["days_since_last_out", "days_since_last_in",
                    "r_days_since_last_in"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trans", default=None)
    ap.add_argument("--rows", type=int, default=300_000)
    args = ap.parse_args()
    from pathlib import Path
    trans = args.trans or str(Path(KAGGLE).expanduser())

    print(f"batch pipeline on {args.rows:,} rows...", file=sys.stderr)
    df = load(trans, args.rows).collect(engine="streaming")
    feat = features(df)
    thr = calibrate(feat, 0.99)
    batch = score(feat, thr).sort("txn_id")

    print("streaming pipeline on the same rows...", file=sys.stderr)
    store: dict = {}
    stream_rows = []
    for txn in replay(trans, args.rows):
        f = feature_store.update(store, txn)
        res = {**f, **rules.score(txn, f, thr)}
        stream_rows.append({"txn_id": txn["txn_id"],
                            **{k: res[k] for k in COMPARE_F + COMPARE_I},
                            **{k: res[k] for k in COMPARE_NULLABLE},
                            "s_dominant": res["dominant_rule"],
                            "s_subject": res["subject_account"],
                            "s_avg": res["sender_avg"]})
    stream = pl.DataFrame(stream_rows).sort("txn_id")

    assert batch.height == stream.height, "row count mismatch"
    bad = 0
    for col in COMPARE_I:
        diff = int((batch[col].cast(pl.Int64) != stream[col].cast(pl.Int64)).sum())
        if diff:
            print(f"MISMATCH {col}: {diff} rows")
            bad += diff
    for col in COMPARE_F:
        b, s = batch[col].cast(pl.Float64), stream[col].cast(pl.Float64)
        rel = ((b - s).abs() / (b.abs().clip(lower_bound=1e-9))).fill_null(0.0)
        diff = int((rel > 1e-6).sum())
        if diff:
            worst = float(rel.max())
            print(f"MISMATCH {col}: {diff} rows (worst rel err {worst:.2e})")
            bad += diff
    d = int((batch["dominant_rule"] != stream["s_dominant"]).sum())
    if d:
        print(f"MISMATCH dominant_rule: {d} rows")
        bad += d
    d = int((batch["subject_account"] != stream["s_subject"]).sum())
    if d:
        print(f"MISMATCH subject_account: {d} rows")
        bad += d
    for b_col, s_col in ([("sender_avg", "s_avg")]
                         + [(c, c) for c in COMPARE_NULLABLE]):
        b = batch[b_col].cast(pl.Float64)
        s = stream[s_col].cast(pl.Float64)
        d = int((b.is_null() != s.is_null()).sum())
        d += int(((b - s).abs() / b.abs().clip(lower_bound=1e-9) > 1e-6)
                 .fill_null(False).sum())
        if d:
            print(f"MISMATCH {b_col}: {d} rows")
            bad += d

    if bad:
        print(f"\nFAIL: {bad} mismatched cells over {batch.height:,} txns")
        sys.exit(1)
    print(f"PASS: batch and stream agree on all {batch.height:,} txns "
          f"({len(COMPARE_I) + len(COMPARE_F) + 3} columns)")


if __name__ == "__main__":
    main()
