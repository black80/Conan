"""The human loop: label taxonomy, severe-failure detection, case assignment.

Implements REQUIREMENTS.md D1/D2/D10:
  - one label taxonomy (mule / normal / suspicious), everything else derived
  - a failure is SEVERE only when the agent approved a mule or blocked a normal
  - assignment is a deterministic least-loaded allocator, cap 10 -- NOT an LLM.
    It is a pure function of (alerts, labels): replayed in alert order, so the
    same inputs always yield the same assignment map. No state to persist.
"""

from __future__ import annotations

# label -> the agent recommendation it AGREES with (D1 diagonal)
AGREES_WITH = {"mule": "block", "normal": "approve", "suspicious": "escalate"}

# label -> derived final_decision; mule/normal close the case, suspicious stays open
FINAL_DECISION = {"mule": "confirmed_fraud", "normal": "false_positive",
                  "suspicious": "escalated"}
CLOSES = {"mule", "normal"}

INVESTIGATORS = ["Aisha", "Omar", "Sara"]      # demo team; capacity below
MAX_OPEN = 10


def judge(label: str, recommendation: str) -> dict:
    """Score one human label against the agent's recommendation (D1 + D10)."""
    disagreement = AGREES_WITH.get(label) != recommendation
    if label == "mule" and recommendation == "approve":
        failure_type = "missed_mule"               # the dangerous one
    elif label == "normal" and recommendation == "block":
        failure_type = "false_alarm"               # blocked a legit account
    else:
        failure_type = None                        # mild or none -- not harvested
    return {
        "disagreement": disagreement,
        "severe": failure_type is not None,
        "failure_type": failure_type,
        "final_decision": FINAL_DECISION[label],
        "closes_case": label in CLOSES,
    }


def assign_all(alerts: list[dict], labels: list[dict]) -> dict[str, str]:
    """Deterministic FILL-FIRST assignment, replayed in alert (time) order:
    an investigator keeps receiving cases until they hold MAX_OPEN open ones,
    then the next in the roster starts filling. A case stops counting toward
    load once closed (labeled mule/normal). If everyone is full the alert
    stays unassigned (None) -- the overflow queue.
    """
    closed = {l["alert_id"] for l in labels if l.get("closes_case")}
    load = {name: 0 for name in INVESTIGATORS}
    out: dict[str, str | None] = {}
    for a in sorted(alerts, key=lambda a: a["txn"]["timestamp"]):
        pick = next((n for n in INVESTIGATORS if load[n] < MAX_OPEN), None)
        out[a["alert_id"]] = pick
        if pick is not None and a["alert_id"] not in closed:
            load[pick] += 1
    return out
