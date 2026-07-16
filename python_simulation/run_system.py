"""The base detection system: replay transactions in time order through the
streaming detector, promote a human-sized alert queue at each day boundary,
write the queue as Alert-contract JSONL for the agent to consume.

    python run_system.py HI-Small_Trans.csv [HI-Small_Patterns.txt] \
        --daily-budget 30 --out alerts_stream.jsonl

The label column is used ONLY in the post-run evaluation block (and only when
a patterns file is given); it never reaches the detector or the output file.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

import polars as pl

from system.detector import detect
from system.engine import TYPOLOGIES, load, parse_patterns
from system.rules import load_thresholds
from system.sampler import sample_day, to_alert_json

CHUNK = 250_000
TXN_FIELDS = ["txn_id", "ts", "sender_bank", "sender_account", "receiver_bank",
              "receiver_account", "amount_paid", "payment_currency",
              "amount_received", "receiving_currency", "payment_format"]


def replay(trans_csv: str, rows: int | None):
    """Yield txn dicts in (ts, txn_id) order -- the wire order of the batch
    engine's event stream. txn_id stays the original CSV row index."""
    df = (load(trans_csv, rows)
          .select(TXN_FIELDS)
          .sort(["ts", "txn_id"])
          .collect(engine="streaming")
          .with_columns((pl.col("ts").dt.epoch("us")).alias("ts_us")))
    for offset in range(0, df.height, CHUNK):
        chunk = df.slice(offset, CHUNK)
        cols = {name: chunk[name].to_list() for name in chunk.columns}
        for i in range(chunk.height):
            yield {name: cols[name][i] for name in cols}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trans")
    ap.add_argument("patterns", nargs="?")
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--daily-budget", type=int, default=500)
    ap.add_argument("--reserve", type=float, default=0.5)
    ap.add_argument("--thresholds", default=None,
                    help="JSON with calibrated thresholds (default: eval_report.json "
                         "from the batch run)")
    ap.add_argument("--cooldown-days", type=int, default=0,
                    help="suppress re-promoting a subject for N days after it was "
                         "promoted (0 = off)")
    ap.add_argument("--out", default="out/alerts_stream.jsonl")
    ap.add_argument("--dump-candidates", default=None,
                    help="also write every candidate (minimal columns) to this "
                         "parquet, for offline sampler-policy sweeps")
    args = ap.parse_args()

    thr = load_thresholds(args.thresholds)
    print(f"thresholds: { {k: round(v, 2) for k, v in thr.items()} }", file=sys.stderr)

    store: dict = {}
    day_candidates: list[dict] = []
    promoted: list[dict] = []
    last_promoted: dict[str, object] = {}   # subject -> day of last promotion
    dump: list[dict] = []
    current_day = None
    n_txn = n_cand = 0
    t0 = time.time()

    def flush(day):
        if args.cooldown_days:
            eligible = [c for c in day_candidates
                        if c["subject_account"] not in last_promoted
                        or (day - last_promoted[c["subject_account"]]).days
                        >= args.cooldown_days]
        else:
            eligible = day_candidates
        picks = sample_day(eligible, args.daily_budget, args.reserve)
        promoted_at = datetime.combine(day, datetime.min.time()) + timedelta(days=1)
        for c in picks:
            c["_promoted_at"] = promoted_at
            last_promoted[c["subject_account"]] = day
        promoted.extend(picks)
        print(f"  {day}: {len(day_candidates):>6} candidates -> "
              f"{len(picks):>3} promoted", file=sys.stderr)
        day_candidates.clear()

    DUMP_COLS = ["txn_id", "sender_account", "receiver_account", "subject_account",
                 "subject_side", "dominant_rule", "score", "typology_score",
                 "subject_burst"]
    for txn in replay(args.trans, args.rows):
        day = txn["ts"].date()
        if current_day is None:
            current_day = day
        elif day != current_day:
            flush(current_day)
            current_day = day
        cand = detect(store, txn, thr)
        n_txn += 1
        if cand is not None:
            n_cand += 1
            day_candidates.append(cand)
            if args.dump_candidates:
                dump.append({**{k: cand[k] for k in DUMP_COLS}, "day": day})
    if current_day is not None:
        flush(current_day)

    if args.dump_candidates:
        pl.DataFrame(dump).write_parquet(args.dump_candidates)
        print(f"dumped {len(dump):,} candidates -> {args.dump_candidates}",
              file=sys.stderr)

    dt = time.time() - t0
    print(f"\nreplayed {n_txn:,} txns in {dt:.0f}s ({n_txn / dt:,.0f} txn/s) | "
          f"{n_cand:,} candidates -> {len(promoted)} promoted alerts",
          file=sys.stderr)

    with open(args.out, "w") as f:
        for c in promoted:
            f.write(json.dumps(to_alert_json(c, c["_promoted_at"])) + "\n")
    print(f"wrote {len(promoted)} alerts -> {args.out}", file=sys.stderr)

    # ------------------------------------------------- evaluation (labels enter
    # here and only here, after the stream is written)
    if not args.patterns:
        return
    lab = (pl.scan_csv(args.trans)
           .select(pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day"),
                   pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                   pl.nth(10).alias("il"))
           .filter(pl.col("il") == 1).collect())
    laund_days = set(lab.select("sa", "day").iter_rows()) | \
        set(lab.select("ra", "day").iter_rows())
    laund_accounts = set(lab["sa"]) | set(lab["ra"])

    hits_day = sum((c["subject_account"], c["ts"].date()) in laund_days
                   for c in promoted)
    hits_acct = sum(c["subject_account"] in laund_accounts for c in promoted)
    n = len(promoted)
    print(f"\n=== stream metrics ({n} alerts, {args.daily_budget}/day) ===")
    print(f"  case precision (same day): {hits_day / n:.1%}")
    print(f"  account precision (any day): {hits_acct / n:.1%}")

    patterns = parse_patterns(args.patterns)
    subjects = {c["subject_account"] for c in promoted}
    any_side = subjects | {c["sender_account"] for c in promoted} | \
        {c["receiver_account"] for c in promoted}
    print("  typology coverage:")
    for typ in TYPOLOGIES:
        p = patterns.filter(pl.col("typology") == typ)
        hit_s = hit_a = 0
        for aid in p["attempt_id"].unique():
            accs = set(p.filter(pl.col("attempt_id") == aid)["account"])
            hit_s += bool(accs & subjects)
            hit_a += bool(accs & any_side)
        n_att = p["attempt_id"].n_unique()
        print(f"    {typ:>15}: {hit_s:>2}/{n_att:<3} as subject | {hit_a:>2} any side")


if __name__ == "__main__":
    main()
