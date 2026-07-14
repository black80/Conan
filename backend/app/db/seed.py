from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import SyncSessionLocal
from app.models.fraud_rule import FraudRule
from app.models.investigator import Investigator
from app.rules.registry import all_rules

logger = get_logger(__name__)

RULE_DESCRIPTIONS = {
    "HIGH_AMOUNT": "Transaction amount exceeds the configured threshold",
    "IMPOSSIBLE_TRAVEL": "Country change within a physically impossible time window",
    "VELOCITY": "Too many transactions on the card in a short window",
    "NEW_DEVICE": "First time this customer uses this device",
    "NEW_MERCHANT": "First time this customer pays this merchant",
    "HIGH_RISK_MERCHANT": "Merchant category is considered high risk",
    "DIFFERENT_COUNTRY": "Country differs from the customer's previous transaction",
    "NIGHT_TRANSACTION": "Transaction occurred during night hours",
    "MULTIPLE_DECLINES": "Multiple recent declines on the card",
    "CARD_NOT_PRESENT": "Card-not-present transaction above minimum amount",
}

INVESTIGATORS = [
    {"name": "Alice Nguyen", "email": "alice@fraudcopilot.dev"},
    {"name": "Bruno Silva", "email": "bruno@fraudcopilot.dev"},
]


def seed() -> None:
    with SyncSessionLocal() as session:
        for rule in all_rules():
            exists = session.execute(
                select(FraudRule).where(FraudRule.code == rule.code)
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    FraudRule(
                        code=rule.code,
                        name=rule.name,
                        description=RULE_DESCRIPTIONS.get(rule.code, rule.name),
                        severity=rule.severity,
                        enabled=True,
                    )
                )
        for inv in INVESTIGATORS:
            exists = session.execute(
                select(Investigator).where(Investigator.email == inv["email"])
            ).scalar_one_or_none()
            if exists is None:
                session.add(Investigator(**inv))
        session.commit()
    logger.info("seed complete")


if __name__ == "__main__":
    seed()
