"""Run the agent over the alert queue: alerts in, Case files out.

    # Claude (Anthropic API):
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_agent.py --limit 20                 # ~$0.15-0.40 each on Opus
    python run_agent.py --limit 100 --model claude-sonnet-5

    # gpt-oss on Groq (free tier at console.groq.com):
    export GROQ_API_KEY=gsk_...
    python run_agent.py --limit 100 --model openai/gpt-oss-120b

Reads out/alerts_stream.jsonl, investigates a deterministic sample, appends
Case JSONs to out/cases.jsonl. Safe to re-run: already-investigated alerts are
skipped, so a crashed or rate-limited run just resumes.

Sampling is stratified by hash of alert_id (uniform across days and rules) and
restricted to the realistic regime (Sep 1-10) by default -- the wind-down tail
would flatter the numbers. Score the output with: python -m eval.triage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from agent import tools
from agent.agent import GROQ_BASE_URL, MODEL, investigate

OUT_DIR = Path(__file__).parent / "out"
KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()
REALISTIC_CUTOFF = date(2022, 9, 11)


def _hash(alert_id: str) -> str:
    return hashlib.md5(alert_id.encode()).hexdigest()


def _label_lookup() -> set:
    """(account, day) pairs touched by laundering -- for SELECTING a balanced
    probe set only. Never passed to the agent."""
    import polars as pl
    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    day = pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").dt.date().alias("day")
    la = (lf.select(day, pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(10).alias("il")).filter(pl.col("il") == 1).collect())
    return (set(la.select("sa", "day").iter_rows())
            | set(la.select("ra", "day").iter_rows()))


def _is_real(alert: dict, laund_days: set) -> bool:
    d = date.fromisoformat(alert["txn"]["timestamp"][:10])
    return (alert["subject_account"], d) in laund_days


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alerts", default=str(OUT_DIR / "alerts_stream.jsonl"))
    ap.add_argument("--out", default=str(OUT_DIR / "cases.jsonl"))
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--include-tail", action="store_true",
                    help="also sample the degenerate Sep 11-18 wind-down days")
    ap.add_argument("--balanced", type=int, default=0, metavar="N",
                    help="probe set of N real + N false alerts (labels used for "
                         "SELECTION only, never shown to the agent) so the "
                         "confusion matrix is populated on a small budget")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="cases investigated in parallel. Each case is "
                         "independent; bound this to stay under API rate limits "
                         "(the SDK retries 429s with backoff).")
    ap.add_argument("--prompt-version", choices=["v3", "v4"], default="v4",
                    help="v4 = probabilities + harness threshold (default); "
                         "v3 = model picks the action. The controlled-A/B knob.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    alerts = [json.loads(l) for l in open(args.alerts)]
    if not args.include_tail:
        alerts = [a for a in alerts
                  if date.fromisoformat(a["txn"]["timestamp"][:10]) < REALISTIC_CUTOFF]

    if args.balanced:
        laund_days = _label_lookup()
        real = sorted((a for a in alerts if _is_real(a, laund_days)),
                      key=lambda a: _hash(a["alert_id"]))
        false = sorted((a for a in alerts if not _is_real(a, laund_days)),
                       key=lambda a: _hash(a["alert_id"]))
        n = args.balanced
        alerts = real[:n] + false[:n]
        alerts.sort(key=lambda a: _hash(a["alert_id"]))   # interleave for a fair run
        print(f"balanced probe: {min(n, len(real))} real + {min(n, len(false))} "
              f"false (labels used for selection only)", file=sys.stderr)
        args.limit = len(alerts)
    else:
        # deterministic uniform sample: order by hash of alert_id
        alerts.sort(key=lambda a: _hash(a["alert_id"]))

    done = set()
    out_path = Path(args.out)
    if out_path.exists():
        done = {json.loads(l)["alert"]["alert_id"] for l in open(out_path)}
        print(f"resuming: {len(done)} cases already on disk", file=sys.stderr)

    todo = [a for a in alerts if a["alert_id"] not in done][: args.limit]
    if not todo:
        print("nothing to do", file=sys.stderr)
        return

    print(f"loading transaction data for tools...", file=sys.stderr)
    tools.init()

    # one shared, thread-safe client with generous retries -- parallel cases
    # will occasionally 429 on a low tier; the SDK backs off and retries.
    if args.model.startswith("claude"):
        import anthropic
        client = anthropic.Anthropic(max_retries=8)
    else:
        from openai import OpenAI
        import os
        client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", GROQ_BASE_URL),
                        api_key=os.environ.get("GROQ_API_KEY")
                        or os.environ.get("OPENAI_API_KEY"),
                        max_retries=8)

    conc = max(1, args.concurrency)
    print(f"investigating {len(todo)} alerts with {args.model} "
          f"({conc}-way parallel)", file=sys.stderr)

    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    write_lock = threading.Lock()
    t_start = time.time()
    done_n = 0
    f = open(out_path, "a")

    def work(alert):
        return alert, investigate(alert, model=args.model, client=client,
                                  verbose=args.verbose,
                                  version=args.prompt_version)

    with ThreadPoolExecutor(max_workers=conc) as pool:
        futures = [pool.submit(work, a) for a in todo]
        for fut in as_completed(futures):
            done_n += 1
            try:
                alert, case = fut.result()
            except Exception as e:                            # noqa: BLE001
                print(f"  [{done_n}/{len(todo)}] FAILED: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
                continue
            with write_lock:
                f.write(json.dumps(case) + "\n")
                f.flush()
                u = case["_meta"]["usage"]
                for k in totals:
                    totals[k] += u.get(k, 0)
            print(f"  [{done_n}/{len(todo)}] {alert['alert_id']} "
                  f"subject={alert['subject_account']} -> "
                  f"{case['recommendation']:<8} conf={case['confidence']:.2f} "
                  f"typology={case['typology'] or '-':<14} "
                  f"({case['_meta']['tool_calls']} tools)", file=sys.stderr)
    f.close()

    print(f"\n{len(todo)} cases in {time.time()-t_start:.0f}s | tokens: "
          f"{totals['input_tokens']:,} in "
          f"(+{totals['cache_read_input_tokens']:,} cached) / "
          f"{totals['output_tokens']:,} out", file=sys.stderr)
    print(f"cases -> {out_path}. Score them: python -m eval.triage", file=sys.stderr)


if __name__ == "__main__":
    main()
