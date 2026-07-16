"""Targeted feature-store re-test.

Re-investigate ONLY the alerts Haiku ESCALATED in the n=100 run, now with the
get_account_features tool active. The question: does letting the agent read a
neighbour's mule-shape convert nervous escalates into confident, correct
verdicts -- false-escalates -> approve (specificity up) without real cases
flipping to approve (recall preserved)?

    python -m eval.escalate_retest [--model claude-haiku-4-5] [--concurrency 4]

Reads the escalated alerts straight out of out/cases_n100.jsonl (each case
carries its full alert), so no re-sampling. Writes new cases to
out/cases_escalate_retest.jsonl and prints the before/after.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import polars as pl

from agent import tools
from agent.agent import GROQ_BASE_URL, MODEL, investigate

REPO = Path(__file__).resolve().parent.parent      # eval/ -> repo root
KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=str(REPO / "out" / "cases_n100.jsonl"))
    ap.add_argument("--out", default=str(REPO / "out" / "cases_escalate_retest.jsonl"))
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    cases = [json.loads(l) for l in open(args.source)]
    escalated = [c["alert"] for c in cases if c["recommendation"] == "escalate"]
    print(f"re-investigating {len(escalated)} escalated alerts with "
          f"get_account_features active", file=sys.stderr)

    # ground truth (labels used only for scoring, never shown to the agent)
    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    la = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(10).alias("il")).filter(pl.col("il") == 1).collect())
    laund = (set(la.select("sa", "day").iter_rows())
             | set(la.select("ra", "day").iter_rows()))

    def is_real(alert):
        d = date.fromisoformat(alert["txn"]["timestamp"][:10])
        return (alert["subject_account"], d) in laund

    tools.init()
    if args.model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(max_retries=8)
    else:
        import os
        from openai import OpenAI
        client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", GROQ_BASE_URL),
                        api_key=os.environ.get("GROQ_API_KEY")
                        or os.environ.get("OPENAI_API_KEY"), max_retries=8)

    lock = threading.Lock()
    results = []
    f = open(args.out, "w")

    def work(alert):
        return alert, investigate(alert, model=args.model, client=client)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for fut in as_completed([pool.submit(work, a) for a in escalated]):
            try:
                alert, case = fut.result()
            except Exception as e:                            # noqa: BLE001
                print(f"  FAILED: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            real = is_real(alert)
            with lock:
                f.write(json.dumps(case) + "\n")
                f.flush()
                results.append((real, case["recommendation"], case["confidence"]))
            print(f"  {alert['alert_id']} ({'REAL ' if real else 'false'}) "
                  f"escalate -> {case['recommendation']:<8} "
                  f"conf={case['confidence']:.2f}", file=sys.stderr)
    f.close()

    # ---- before/after: all were 'escalate'; where did they move?
    print(f"\n=== the 38 escalates, re-judged with feature-store neighbour "
          f"context ===")
    for truth in (True, False):
        grp = [r for r in results if r[0] is truth]
        if not grp:
            continue
        moves = {a: sum(1 for _, rec, _ in grp if rec == a)
                 for a in ("approve", "escalate", "block")}
        label = "REAL cases" if truth else "FALSE cases"
        print(f"  {label} ({len(grp)}): approve {moves['approve']}, "
              f"escalate {moves['escalate']}, block {moves['block']}")

    n_false = sum(1 for r in results if not r[0])
    n_real = sum(1 for r in results if r[0])
    false_cleared = sum(1 for real, rec, _ in results
                        if not real and rec == "approve")
    real_lost = sum(1 for real, rec, _ in results if real and rec == "approve")
    print(f"\n  false-escalates now confidently APPROVED (specificity gain): "
          f"{false_cleared}/{n_false}")
    print(f"  real-escalates wrongly flipped to approve (recall cost): "
          f"{real_lost}/{n_real}")
    print("\n  -> feed these back through eval.triage by merging with the "
          "non-escalate n=100 cases to see the net specificity change.")


if __name__ == "__main__":
    main()
