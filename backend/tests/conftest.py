import uuid
from datetime import datetime, timezone

from app.models.transaction import Transaction


def make_transaction(**overrides) -> Transaction:
    defaults = dict(
        id=uuid.uuid4(),
        customer_id="cust_1",
        card_token="card_1",
        amount=50,
        currency="USD",
        merchant="Corner Coffee",
        merchant_category="coffee",
        country="US",
        device_id="dev_1",
        card_present=True,
        declined=False,
        status="pending",
        created_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Transaction(**defaults)
