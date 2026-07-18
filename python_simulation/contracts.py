"""FROZEN contracts between the three subsystems.

Person A (detection) produces Alert. Person B (agent) consumes Alert, produces
Case. Person C (API/frontend) consumes Case, produces Decision. Everyone mocks
the others' side with the fixtures in fixtures/ -- do not integrate early.

v1.1 additions over the handoff draft (agreed extension, do not remove):
  - Alert.subject_account / Alert.subject_side: the account the rules INDICT.
    FAN_IN_SPIKE describes the RECEIVER collecting; the case must open on it,
    not on the payer that happened to trip the rule. The agent investigates
    subject_account, not txn.sender_account.

`is_laundering` and the Patterns.txt typology exist ONLY for evaluation.
They must never reach the agent: strip them before a case is built.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Transaction(BaseModel):
    txn_id: str
    timestamp: datetime
    sender_bank: str
    sender_account: str
    receiver_bank: str
    receiver_account: str
    amount_paid: float
    payment_currency: str
    amount_received: float
    receiving_currency: str
    payment_format: str          # Wire | ACH | Credit Card | Cheque | Cash | Bitcoin | Reinvestment


class Alert(BaseModel):
    alert_id: str
    txn: Transaction
    subject_account: str         # the account the rules indict -- investigate THIS
    subject_side: Literal["sender", "receiver"]
    rules_fired: list[str]       # ["FAN_IN_SPIKE", "PASS_THROUGH"]
    score: int
    features: dict[str, float]   # feature-store row: fan_in, collect_ratio, burst_in, ...
    created_at: datetime


class Evidence(BaseModel):
    label: str                   # "Inbound counterparties (7d)"
    value: str                   # "17 distinct senders, 0 outbound"
    supports: Literal["fraud", "legit", "neutral"]


class Case(BaseModel):
    case_id: str
    alert: Alert
    summary: str                 # agent narrative, 3-5 sentences, verdict first
    evidence: list[Evidence]
    typology: str | None         # agent's guess: FAN-IN / STACK / ... (scored vs Patterns.txt)
    recommendation: Literal["block", "approve", "escalate"]
    confidence: float
    reasoning_trace: list[str]   # every tool call + result. POPULATE FROM COMMIT ONE.
    status: Literal["auto_closed", "needs_review", "resolved"]


class Decision(BaseModel):
    case_id: str
    action: Literal["block", "approve", "escalate"]
    analyst_note: str | None = None
    agreed_with_agent: bool      # derived from recommendation vs action, not asked
