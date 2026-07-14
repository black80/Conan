from enum import StrEnum


class TransactionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    FLAGGED = "flagged"
    UNDER_INVESTIGATION = "under_investigation"


class CaseStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    CLOSED = "closed"


class CaseResolution(StrEnum):
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNRESOLVED = "unresolved"


class Recommendation(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"
    INVESTIGATE = "investigate"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
