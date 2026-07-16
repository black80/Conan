"""Generate fixture Alert JSONs from the real alert stream + one example Case.

Persons B and C build against fixtures/, never against live engine output.
is_laundering is stripped here -- the agent must never see it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))          # runnable as `python fixtures/make_fixtures.py`

from contracts import Alert, Case, Evidence, Transaction          # noqa: E402
from system.rules import FEATURE_KEYS as FEATURE_COLS            # noqa: E402


def alert_from_row(row: dict) -> Alert:
    return Alert(
        alert_id=f"AL-{row['txn_id']:08d}",
        txn=Transaction(
            txn_id=str(row["txn_id"]),
            timestamp=row["ts"],
            sender_bank=row["sender_bank"],
            sender_account=row["sender_account"],
            receiver_bank=row["receiver_bank"],
            receiver_account=row["receiver_account"],
            amount_paid=row["amount_paid"],
            payment_currency=row["payment_currency"],
            amount_received=row["amount_received"],
            receiving_currency=row["receiving_currency"],
            payment_format=row["payment_format"],
        ),
        subject_account=row["subject_account"],
        subject_side=row["subject_side"],
        rules_fired=row["rules_fired"],
        score=row["score"],
        features={k: float(row[k]) for k in FEATURE_COLS if row.get(k) is not None},
        created_at=datetime.now(timezone.utc),
    )


def main() -> None:
    alerts = pl.read_parquet(REPO / "out" / "alerts.parquet")
    fixtures = REPO / "fixtures"
    fixtures.mkdir(exist_ok=True)

    # one fixture per headline rule: highest-scoring alert where that rule is
    # DOMINANT (max-weight fired), so each fixture shows the rule's typical shape
    # rather than one noisy hub that fires everything
    sides = {"FAN_IN_SPIKE": "receiver", "FAN_OUT_SPRAY": "sender",
             "PASS_THROUGH": "sender", "STRUCTURING": "sender"}
    for rule, side in sides.items():
        sub = alerts.filter(
            (pl.col("dominant_rule") == rule) & (pl.col("subject_side") == side)
        ).sort(["subject_burst", "score"], descending=True)
        if not sub.height:
            continue
        alert = alert_from_row(sub.row(0, named=True))
        path = fixtures / f"alert_{rule.lower()}.json"
        path.write_text(alert.model_dump_json(indent=2))
        print(f"wrote {path.name}: subject={alert.subject_account} "
              f"({alert.subject_side}), score={alert.score}, rules={alert.rules_fired}")

    # an example Case, hand-built on the fan-in fixture, showing every field
    # Person C renders and Person B must populate (esp. reasoning_trace)
    src = json.loads((fixtures / "alert_fan_in_spike.json").read_text())
    case = Case(
        case_id="CS-00000001",
        alert=Alert(**src),
        summary=(
            "Likely money-mule collection account. In the 24h before this alert the "
            "subject received funds from many distinct senders it had never "
            "transacted with, while sending almost nothing out - a fan-in pattern "
            "inconsistent with its prior history. Counterparties are spread across "
            "multiple banks with no shared payroll or merchant signature. "
            "Recommend blocking pending source-of-funds verification."
        ),
        evidence=[
            Evidence(label="Inbound counterparties (lifetime)",
                     value=f"{int(src['features'].get('r_fan_in', 0))} distinct senders, "
                           f"{int(src['features'].get('r_fan_out', 0))} outbound",
                     supports="fraud"),
            Evidence(label="Collect ratio (in-fan / out-fan)",
                     value=f"{src['features'].get('collect_ratio', 0):.1f} (calibrated threshold 4.0)",
                     supports="fraud"),
            Evidence(label="New counterparties in last 24h",
                     value=f"{src['features'].get('r_burst_in', 0):.0%} of lifetime fan acquired in 24h",
                     supports="fraud"),
            Evidence(label="Account age",
                     value="active since file start (17-day window)",
                     supports="neutral"),
        ],
        typology="FAN-IN",
        recommendation="block",
        confidence=0.82,
        reasoning_trace=[
            "get_account_profile('" + src["subject_account"] + "') -> entity: <from Accounts.csv>",
            "get_counterparties('" + src["subject_account"] + "', direction='in', days=7) -> N distinct senders",
            "get_counterparties('" + src["subject_account"] + "', direction='out', days=7) -> ~0 outbound",
            "trace_funds('" + src["subject_account"] + "', hops=2, direction='in') -> sources unrelated",
            "verdict: FAN-IN collection, recommend block",
        ],
        status="needs_review",
    )
    (fixtures / "case_example.json").write_text(case.model_dump_json(indent=2))
    print("wrote case_example.json")

    # sanity: no label leakage into fixtures
    for f in fixtures.glob("*.json"):
        assert "is_laundering" not in f.read_text(), f"label leaked into {f}"
    print("label-leak check passed")


if __name__ == "__main__":
    main()
