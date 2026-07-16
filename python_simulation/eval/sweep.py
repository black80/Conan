"""Threshold / confidence sweep over an existing agent run (offline, free).

The v4 agent emits probabilities and the v3 agent emits a recommendation +
confidence, so we can re-derive the action under ANY threshold WITHOUT
re-calling the model. This maps the whole operating frontier (recall vs
specificity vs auto-close purity vs workload) from one paid run.

    python -m eval.sweep --cases out/cases_ab_v4.jsonl --mode v4
    python -m eval.sweep --cases out/cases_ab_v3.jsonl --mode v3conf

Metrics per operating point:
  recall      = real cases kept (escalate+block) / all real          [catch fraud]
  specificity = false auto-closed (approve) / all false              [clear noise]
  purity      = false among auto-closed / all auto-closed            [auto-close safety]
  workload    = auto-closed / all                                     [work removed]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import polars as pl

KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()


def load(cases_path):
    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    la = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(10).alias("il")).filter(pl.col("il") == 1).collect())
    laund = (set(la.select("sa", "day").iter_rows())
             | set(la.select("ra", "day").iter_rows()))
    rows = []
    for c in (json.loads(l) for l in open(cases_path)):
        d = date.fromisoformat(c["alert"]["txn"]["timestamp"][:10])
        real = (c["alert"]["subject_account"], d) in laund
        m = c["_meta"]
        rows.append({
            "real": real,
            "rec": c["recommendation"], "conf": c.get("confidence", 0.0),
            "p_legit": m.get("p_legitimate", 0.0),
            "p_laund": m.get("p_laundering", 0.0),
            "traced": m.get("traced_structure", False),
            "ran_trace": any(t.startswith(("trace_funds", "get_pass_through"))
                             for t in c["reasoning_trace"]),
        })
    return rows


def score(actions, rows):
    """actions: list of 'approve'|'escalate'|'block' aligned with rows."""
    n = len(rows)
    n_real = sum(r["real"] for r in rows); n_false = n - n_real
    appr = [(a, r) for a, r in zip(actions, rows) if a == "approve"]
    kept_real = sum(1 for a, r in zip(actions, rows)
                    if a != "approve" and r["real"])
    appr_false = sum(1 for a, r in appr if not r["real"])
    appr_real = sum(1 for a, r in appr if r["real"])
    n_appr = len(appr)
    return {
        "recall": kept_real / n_real if n_real else 0,
        "specificity": appr_false / n_false if n_false else 0,
        "purity": appr_false / n_appr if n_appr else float("nan"),
        "workload": n_appr / n,
        "n_approve": n_appr, "misses": appr_real,
    }


def sweep_v4(rows):
    print("v4 -- approve if p_legit>=TA ; block if p_laund>=TB and a trace ran ; "
          "else escalate\n")
    print(f"  {'TA':>4} {'TB':>4} | {'recall':>6} {'spec':>5} {'purity':>6} "
          f"{'workload':>8} {'#close(miss)':>12}")
    for ta in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
        for tb in (0.70, 0.80, 0.90):
            acts = []
            for r in rows:
                if r["p_laund"] >= tb and r["traced"] and r["ran_trace"]:
                    acts.append("block")
                elif r["p_legit"] >= ta:
                    acts.append("approve")
                else:
                    acts.append("escalate")
            s = score(acts, rows)
            print(f"  {ta:>4.2f} {tb:>4.2f} | {s['recall']:>6.0%} "
                  f"{s['specificity']:>5.0%} {s['purity']:>6.0%} "
                  f"{s['workload']:>8.0%} {s['n_approve']:>7}({s['misses']})")


def sweep_v3conf(rows):
    """Start from the model's recommendation, then only TRUST an auto-close
    (approve) if its confidence >= gamma; low-confidence approves are demoted
    to escalate (safer). Same idea optionally on block."""
    print("v3 -- keep the model's action, but DEMOTE approve->escalate when "
          "confidence < gamma\n")
    print(f"  {'gamma':>5} | {'recall':>6} {'spec':>5} {'purity':>6} "
          f"{'workload':>8} {'#close(miss)':>12}")
    for g in (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        acts = [("approve" if (r["rec"] == "approve" and r["conf"] >= g)
                 else ("escalate" if r["rec"] == "approve" else r["rec"]))
                for r in rows]
        s = score(acts, rows)
        print(f"  {g:>5.2f} | {s['recall']:>6.0%} {s['specificity']:>5.0%} "
              f"{s['purity']:>6.0%} {s['workload']:>8.0%} "
              f"{s['n_approve']:>7}({s['misses']})")


def auc(rows, key, positive_is_false=True):
    """Ranking quality of a signal for separating false(legit) from real(fraud).
    positive_is_false: higher key -> more likely legit."""
    hi = [r[key] for r in rows if (not r["real"]) == positive_is_false]
    lo = [r[key] for r in rows if (not r["real"]) != positive_is_false]
    if not hi or not lo:
        return None
    wins = sum(1 for a in hi for b in lo if a > b) + 0.5 * sum(
        1 for a in hi for b in lo if a == b)
    return wins / (len(hi) * len(lo))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--mode", choices=["v4", "v3conf"], required=True)
    args = ap.parse_args()
    rows = load(args.cases)
    nr = sum(r["real"] for r in rows)
    print(f"=== sweep {args.cases}  (n={len(rows)}, {nr} real / {len(rows)-nr} "
          f"false) ===\n")
    sig = "p_legit" if args.mode == "v4" else "conf"
    a = auc(rows, sig)
    print(f"ranking quality of '{sig}' (separates legit from fraud): "
          f"AUC={a:.2f}  (0.5=no signal)\n" if a else "")
    (sweep_v4 if args.mode == "v4" else sweep_v3conf)(rows)


if __name__ == "__main__":
    main()
