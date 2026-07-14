from enum import StrEnum


class Topic(StrEnum):
    TRANSACTION_CREATED = "transaction.created"
    FRAUD_ALERT_CREATED = "fraud.alert.created"
    INVESTIGATION_CREATED = "investigation.created"
    CASE_CLOSED = "case.closed"
