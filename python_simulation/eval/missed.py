"""DEMO PREP (REQUIREMENTS.md D9): which laundering attempts did the rule
engine miss entirely? Victim reports filed on these accounts correspond to
REAL missed fraud, so the Rule Lab demo is honest.

This is the only place Patterns.txt meets the alert stream, and it is offline:
its output guides the demo operator's typing; it feeds no agent and no API.

    python -m eval.missed          # -> out/missed_attempts.jsonl + a summary
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import polars as pl

from system.engine import parse_patterns

KAGGLE = Path("~/.cache/kagglehub/datasets/ealtman2019/"
              "ibm-transactions-for-anti-money-laundering-aml/versions/8").expanduser()
REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    patterns = parse_patterns(str(KAGGLE / "HI-Small_Patterns.txt"))
    subjects = {json.loads(l)["subject_account"]
                for l in open(REPO / "out" / "alerts_stream.jsonl")}

    # laundering rows give each attempt's true activity window + the facts a
    # victim would report (amounts, counts, counterparties)
    lf = pl.scan_csv(str(KAGGLE / "HI-Small_Trans.csv"))
    la = (lf.select(pl.nth(0).str.to_datetime("%Y/%m/%d %H:%M").alias("ts"),
                    pl.nth(2).alias("sa"), pl.nth(4).alias("ra"),
                    pl.nth(5).alias("amount"), pl.nth(6).alias("currency"),
                    pl.nth(10).alias("il"))
          .filter(pl.col("il") == 1).collect())

    out_path = REPO / "out" / "missed_attempts.jsonl"
    n_missed = 0
    with open(out_path, "w") as f:
        for aid in patterns["attempt_id"].unique().sort():
            p = patterns.filter(pl.col("attempt_id") == aid)
            accounts = set(p["account"])
            if accounts & subjects:
                continue                       # somebody in it was alerted on
            rows = la.filter(pl.col("sa").is_in(accounts)
                             | pl.col("ra").is_in(accounts))
            if rows.is_empty():
                continue
            # the collector (most-frequent receiver) is the natural subject
            top = (rows.group_by("ra").len().sort("len", descending=True)
                   .filter(pl.col("ra").is_in(accounts)))
            subject = top["ra"][0] if top.height else sorted(accounts)[0]
            lo, hi = rows["ts"].min(), rows["ts"].max()

            # the victim's story: what actually moved through the subject --
            # facts a complainant would report; no typology word reaches the agent
            inb = rows.filter(pl.col("ra") == subject)
            out = rows.filter(pl.col("sa") == subject)
            days = max((hi - lo).days, 1)

            def money(df):
                """Sum in the direction's dominant currency only -- raw sums
                across currencies produce nonsense totals."""
                cur = df["currency"].mode()[0]
                amt = df.filter(pl.col("currency") == cur)["amount"].sum()
                return f"{cur} {amt:,.0f}"

            parts = []
            if inb.height:
                parts.append(f"{money(inb)} arrived in {inb.height} "
                             f"transfer(s) from {inb['sa'].n_unique()} "
                             f"counterpart(ies)")
            if out.height:
                parts.append(f"{money(out)} left in {out.height} "
                             f"transfer(s) to {out['ra'].n_unique()} account(s)")
            context = (f"Victim reports: {'; '.join(parts)} over ~{days} day(s). "
                       f"No alert was raised at the time.")

            n_missed += 1
            f.write(json.dumps({
                "attempt_id": int(aid),
                "typology": p["typology"][0],
                "suggested_subject": subject,
                "window_start": str(lo.date()),
                "window_end": str((hi + timedelta(days=1)).date()),
                "n_accounts": len(accounts),
                "accounts": sorted(accounts)[:6],
                "n_txns": rows.height,
                "context": context,
            }, default=str) + "\n")

    total = patterns["attempt_id"].n_unique()
    print(f"{n_missed}/{total} attempts fully missed -> {out_path}")
    print("file a victim report on any 'suggested_subject' + window; "
          "the Rule Lab demo is then a real miss, honestly told")


if __name__ == "__main__":
    main()
