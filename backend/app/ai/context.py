from app.models.transaction import Transaction
from app.rules.base import RuleHit


def transaction_dict(txn: Transaction) -> dict:
    return {
        "id": str(txn.id),
        "amount": float(txn.amount),
        "currency": txn.currency,
        "merchant": txn.merchant,
        "merchant_category": txn.merchant_category,
        "country": txn.country,
        "device_id": txn.device_id,
        "card_present": txn.card_present,
        "declined": txn.declined,
        "created_at": txn.created_at.isoformat(),
    }


def build_ai_context(
    txn: Transaction,
    hits: list[RuleHit],
    history: list[Transaction],
    stats: dict,
) -> dict:
    return {
        "transaction": transaction_dict(txn),
        "customer_profile": {
            "customer_id": txn.customer_id,
            "card_token": txn.card_token,
            **stats,
        },
        "previous_transactions": [transaction_dict(t) for t in history],
        "triggered_rules": [
            {
                "code": h.code,
                "name": h.name,
                "severity": h.severity,
                "detail": h.detail,
            }
            for h in hits
        ],
        "merchant_information": {
            "merchant": txn.merchant,
            "category": txn.merchant_category,
            "country": txn.country,
        },
        "device_information": {"device_id": txn.device_id},
        "fraud_statistics": stats,
    }
