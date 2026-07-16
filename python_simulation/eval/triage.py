"""Proof #1 scorecard: did the agent's triage beat the queue it was handed?

Joins the agent's Case files against ground truth (labels the agent never saw)
and reports the numbers that matter:

  - confusion matrix: recommendation x truth
  - auto-close rate on FALSE alerts (workload removed)
  - retention of REAL cases (recall of escalate/block)
  - precision of the queue the human sees AFTER the agent
  - typology accuracy on confirmed-real cases (vs Patterns.txt)

    python -m eval.triage [--cases out/cases.jsonl]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import polars as pl

from system.engine import parse_patterns

KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()
REPO = Path(__file__).resolve().parent.parent      # eval/ -> repo root


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default=str(REPO / "out" / "cases.jsonl"))
    args = ap.parse_args()

    cases = [json.loads(l) for l in open(args.cases)]
    if not cases:
        raise SystemExit("no cases to score")

    # ground truth: (account, day) pairs touched by laundering, either side
    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    laund = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                       pl.nth(10).alias("il"))
             .filter(pl.col("il") == 1).collect())
    laund_days = (set(laund.select("sa", "day").iter_rows())
                  | set(laund.select("ra", "day").iter_rows()))
    patterns = parse_patterns(str(KAGGLE / "HI-Small_Patterns.txt"))
    acct_typology: dict[str, set] = {}
    for aid, typ, acc in patterns.iter_rows():
        acct_typology.setdefault(acc, set()).add(typ)

    # ---- score each case
    matrix = {r: {"real": 0, "false": 0}
              for r in ("approve", "escalate", "block")}
    typ_right = typ_total = 0
    for c in cases:
        subj = c["alert"]["subject_account"]
        d = date.fromisoformat(c["alert"]["txn"]["timestamp"][:10])
        real = (subj, d) in laund_days
        matrix[c["recommendation"]]["real" if real else "false"] += 1
        if real and subj in acct_typology:
            typ_total += 1
            if c["typology"] in acct_typology[subj]:
                typ_right += 1

    n = len(cases)
    n_real = sum(v["real"] for v in matrix.values())
    n_false = n - n_real
    closed_false = matrix["approve"]["false"]
    missed_real = matrix["approve"]["real"]
    flagged = {r: matrix[r]["real"] + matrix[r]["false"]
               for r in ("escalate", "block")}
    kept = flagged["escalate"] + flagged["block"]
    kept_real = matrix["escalate"]["real"] + matrix["block"]["real"]

    print(f"=== agent triage scorecard ({n} cases; queue precision in: "
          f"{n_real}/{n} = {n_real/n:.1%}) ===\n")
    print(f"  {'recommendation':>14} | {'real':>5} | {'false':>5}")
    for r in ("approve", "escalate", "block"):
        print(f"  {r:>14} | {matrix[r]['real']:>5} | {matrix[r]['false']:>5}")

    n_closed = closed_false + missed_real            # everything auto-closed
    if n_closed:
        print(f"\n  AUTO-CLOSE PURITY: {closed_false}/{n_closed} = "
              f"{closed_false/n_closed:.1%} of auto-closes were safe "
              f"({missed_real} real case(s) wrongly closed) "
              f"-- the automated action's quality")
    if n_false:
        print(f"  auto-closed {closed_false}/{n_false} false alerts "
              f"({closed_false/n_false:.1%} of the noise removed = specificity)")
    if n_real:
        print(f"  retained {kept_real}/{n_real} real cases "
              f"({kept_real/n_real:.1%} recall = fraud reaching a human)")
    else:
        print("\n  (this sample contains no real laundering -- "
              "measure noise-clearing above; draw a larger sample for recall)")
    if kept and n_real:
        rich = (kept_real / kept) / (n_real / n)
        print(f"  human queue after agent: {kept} cases at "
              f"{kept_real/kept:.1%} precision "
              f"(was {n} at {n_real/n:.1%}) -> "
              f"{n/kept:.1f}x smaller, {rich:.1f}x richer")
    if missed_real:
        print(f"  !! {missed_real} real case(s) auto-closed -- inspect these:")
        for c in cases:
            subj = c["alert"]["subject_account"]
            d = date.fromisoformat(c["alert"]["txn"]["timestamp"][:10])
            if c["recommendation"] == "approve" and (subj, d) in laund_days:
                print(f"     {c['case_id']} subject={subj} conf={c['confidence']}")
    if typ_total:
        print(f"\n  typology named correctly on {typ_right}/{typ_total} "
              f"labeled-attempt cases ({typ_right/typ_total:.0%})")

    # ---- project the probe's class-conditional rates onto the real 500/day queue
    if n_real and n_false:
        _project(kept_real, n_real, closed_false, n_false, laund_days)


def _project(kept_real, n_real, closed_false, n_false, laund_days):
    """Apply recall (on real) and specificity (on false), both measured on the
    balanced probe, to the actual realistic-regime stream. The probe's class mix
    is artificial (50/50); its per-class RATES are what transfer."""
    stream = REPO / "out" / "alerts_stream.jsonl"
    if not stream.exists():
        return
    cut = date(2022, 9, 11)
    N = R = 0
    for line in open(stream):
        a = json.loads(line)
        d = date.fromisoformat(a["txn"]["timestamp"][:10])
        if d >= cut:
            continue                       # realistic regime only (see RESULTS.md)
        N += 1
        R += (a["subject_account"], d) in laund_days
    if not N:
        return
    base = R / N

    def project(recall, spec):
        kept_r = recall * R
        kept_f = (1 - spec) * (N - R)
        q = kept_r + kept_f
        return q, (kept_r / q if q else 0), 1 - q / N, recall

    recall = kept_real / n_real
    spec = closed_false / n_false
    q, prec, cut_pct, rec = project(recall, spec)

    print(f"\n=== projected onto the real 500/day queue "
          f"({N:,} realistic-regime alerts, {base:.1%} precision) ===")
    print(f"  agent rates (from probe): recall {recall:.0%} on real, "
          f"specificity {spec:.0%} on false")
    print(f"  -> human reviews ~{q:,.0f} cases (escalate+block) at "
          f"~{prec:.1%} precision, was {N:,} at {base:.1%}")
    print(f"  -> {cut_pct:.0%} less analyst workload, "
          f"{rec:.0%} of real fraud still reaches a human, "
          f"~{(1-recall)*R:,.0f} real cases wrongly auto-closed")
    # error band: probe is n=8 per class, so +/-1 case moves each rate ~12pts
    lo = project(max(0, (kept_real - 1) / n_real), max(0, (closed_false - 1) / n_false))
    hi = project(min(1, (kept_real + 1) / n_real), min(1, (closed_false + 1) / n_false))
    print(f"  (small-sample band, +/-1 probe case: precision "
          f"{min(lo[1], hi[1]):.0%}-{max(lo[1], hi[1]):.0%}, "
          f"workload cut {min(lo[2], hi[2]):.0%}-{max(lo[2], hi[2]):.0%}, "
          f"recall {min(lo[3], hi[3]):.0%}-{max(lo[3], hi[3]):.0%})")


if __name__ == "__main__":
    main()
