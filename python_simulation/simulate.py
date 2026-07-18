"""Simulate one day of the detection system in the terminal.

Replays the chosen day's transactions through the live detector at an
accelerated pace: a ticker shows the stream flowing, alert lines fire as
rules trip, and at end-of-day the sampler promotes the analyst queue.

    python simulate.py                        # default day, ~45s show
    python simulate.py --day 2022-09-03 --duration 30
    python simulate.py --duration 0           # no pacing, as fast as it runs

State is warmed by streaming all prior days through detect() first (silent,
full speed), so the day's features carry real history. Read-only: writes
nothing, labels appear nowhere.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from run_system import replay
from system.detector import detect
from system.rules import RULES, TYPOLOGY_RULES, load_thresholds
from system.sampler import sample_day

_SIDES = {n: side for n, _, side, _ in RULES}

KAGGLE = ("~/.cache/kagglehub/datasets/ealtman2019/"
          "ibm-transactions-for-anti-money-laundering-aml/versions/8/"
          "HI-Small_Trans.csv")

# ANSI
DIM, RED, YEL, GRN, CYN, BLD, RST = ("\033[2m", "\033[31m", "\033[33m",
                                     "\033[32m", "\033[36m", "\033[1m", "\033[0m")
CLR = "\r\033[K"


def lead_rule(c: dict) -> str:
    """The strongest fired rule on the SUBJECT's side. A tie can make the
    subject the sender while dominant_rule is receiver-side; everything we
    display must belong to the account we show."""
    return next((n for n in c["rules_fired"] if _SIDES[n] == c["subject_side"]),
                c["dominant_rule"])


def context(c: dict) -> str:
    """One human line saying WHY, describing the subject's side."""
    lead = lead_rule(c)
    if lead == "FAN_IN_SPIKE":
        return (f"{c['r_fan_in']} senders ({c['r_fs_in_cnt_1d']} new in 24h) | "
                f"{c['r_amt_in_1d']:,.0f} in, {c['r_n_out_1d']} txns out")
    if lead == "RAPID_INFLOW":
        return (f"{c['r_n_in_1d']} payments in 24h from {c['r_fan_in']} senders"
                f" | {c['r_amt_in_1d']:,.0f} in")
    if lead == "FAN_OUT_SPRAY":
        return (f"{c['fan_out']} receivers, {c['burst_out']:.0%} new in 24h | "
                f"{c['amt_out_1d']:,.0f} out")
    if lead == "PASS_THROUGH":
        return (f"{c['sender_inflow_1d']:,.0f} in -> {c['amt_out_1d']:,.0f} out "
                f"({c['passthrough_ratio']:.0%} pass-through, "
                f"{c['n_out_1d']} payments)")
    if lead == "STRUCTURING":
        return (f"{c['n_out_1d']} payments totalling {c['amt_out_1d']:,.0f}, "
                f"every one under {c['max_out_1d']:,.0f}")
    return (f"{c['n_out_1d']} txns / {c['amt_out_1d']:,.0f} out in 24h, "
            f"{c['sender_inflow_1d']:,.0f} in")


def alert_line(c: dict, repeats: int = 0) -> str:
    color = RED if c["typology_score"] >= 70 else YEL
    t = c["ts"].strftime("%H:%M")
    again = f" {DIM}(x{repeats} today){RST}" if repeats > 1 else ""
    pair = (f" {DIM}first-ever pair{RST}" if c.get("is_first_seen_pair") else "")
    return (f"{DIM}{t}{RST}  {color}{BLD}⚠ {lead_rule(c):<16}{RST}"
            f" score {color}{c['score']:>3}{RST}"
            f"  subject {BLD}{c['subject_account']}{RST} ({c['subject_side']})"
            f"  {context(c)}{pair}{again}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("trans", nargs="?", default=None)
    ap.add_argument("--day", default="2022-09-05")
    ap.add_argument("--duration", type=float, default=45.0,
                    help="wall seconds the day should take (0 = no pacing)")
    ap.add_argument("--min-print-score", type=int, default=80,
                    help="only print candidates at or above this score")
    ap.add_argument("--budget", type=int, default=500)
    args = ap.parse_args()

    from pathlib import Path
    trans = args.trans or str(Path(KAGGLE).expanduser())
    target = date.fromisoformat(args.day)
    tty = sys.stdout.isatty()
    pace = args.duration if tty or args.duration == 0 else 0

    thr = load_thresholds()
    print(f"{CYN}{BLD}== AML detection: live replay of {target} =={RST}")
    print(f"{DIM}thresholds (account-level q99): "
          f"{ {k: round(v, 1) for k, v in thr.items()} }{RST}\n")

    store: dict = {}
    warm = 0
    t0 = time.time()
    day_start_us = None
    candidates: list[dict] = []
    printed: dict[str, int] = {}       # subject -> times seen today
    n_day = 0
    last_tick = 0.0
    suppressed = 0
    # alert lines are rate-limited so the stream stays readable: a token
    # bucket lets ~1.5 lines/s through when pacing, 80 lines total when not
    tokens, last_refill = 3.0, time.time()
    RATE, CAP = 1.5, 3.0
    hard_cap = 80 if not pace else 10 ** 9

    for txn in replay(trans, None):
        d = txn["ts"].date()
        if d < target:
            detect(store, txn, thr)
            warm += 1
            if warm % 500_000 == 0:
                print(f"{CLR}{DIM}warming state: {warm:,} prior txns "
                      f"({time.time() - t0:.0f}s)...{RST}", end="", flush=True)
            continue
        if d > target:
            break

        if day_start_us is None:
            print(f"{CLR}{DIM}state warm: {warm:,} txns, "
                  f"{len(store):,} accounts tracked "
                  f"({time.time() - t0:.0f}s){RST}\n"
                  f"{BLD}--- {target} 00:00 · market open "
                  f"-------------------------------------------{RST}")
            day_start_us = txn["ts_us"]
            t0 = time.time()

        # pace: this txn's position in the day -> its wall-clock slot
        if pace:
            due = (txn["ts_us"] - day_start_us) / 86_400_000_000 * pace
            lag = due - (time.time() - t0)
            if lag > 0.005:
                time.sleep(min(lag, 0.25))

        cand = detect(store, txn, thr)
        n_day += 1
        if cand is not None:
            candidates.append(cand)
            if cand["score"] >= args.min_print_score:
                subj = cand["subject_account"]
                printed[subj] = printed.get(subj, 0) + 1
                now = time.time()
                tokens = min(CAP, tokens + (now - last_refill) * RATE)
                last_refill = now
                if (printed[subj] <= 2 and tokens >= 1.0
                        and sum(printed.values()) - suppressed <= hard_cap):
                    tokens -= 1.0
                    print(f"{CLR}{alert_line(cand, printed[subj])}")
                else:
                    suppressed += 1

        now = time.time()
        if tty and now - last_tick > 0.1:
            sim = txn["ts"].strftime("%H:%M")
            print(f"{CLR}{DIM}[{sim}] {n_day:>7,} txns | "
                  f"{len(candidates):>6,} candidates | "
                  f"{n_day / max(now - t0, 1e-9):,.0f} txn/s{RST}",
                  end="", flush=True)
            last_tick = now

    dt = time.time() - t0
    print(f"{CLR}{BLD}--- {target} 23:59 · day closed "
          f"------------------------------------------{RST}")
    print(f"  {n_day:,} transactions | {len(candidates):,} rule candidates"
          f" ({suppressed:,} alert lines suppressed for readability) | "
          f"{dt:.0f}s\n")

    picks = sample_day(candidates, args.budget)
    n_typ = sum(1 for c in picks if c["dominant_rule"] in TYPOLOGY_RULES)
    print(f"{CYN}{BLD}== END-OF-DAY SAMPLER: {len(picks)} cases promoted to the "
          f"analyst queue ({n_typ} typology-led) =={RST}")
    for i, c in enumerate(picks[:30], 1):
        color = RED if c["typology_score"] >= 70 else YEL
        print(f"  {DIM}{i:>2}.{RST} {color}{lead_rule(c):<16}{RST}"
              f" score {c['score']:>3}  {BLD}{c['subject_account']}{RST}"
              f" ({c['subject_side']}) {DIM}{context(c)}{RST}")
    if len(picks) > 30:
        print(f"  {DIM}... and {len(picks) - 30} more cases in the queue{RST}")
    print(f"\n{DIM}{len(candidates):,} candidates -> {len(picks)} cases: "
          f"the queue a human team can actually read. "
          f"Next stop: the agent investigates each one.{RST}")


if __name__ == "__main__":
    main()
