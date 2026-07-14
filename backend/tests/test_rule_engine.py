from app.services.rule_engine import RuleEngine
from tests.conftest import make_transaction


def test_engine_evaluate_collects_multiple_hits():
    engine = RuleEngine(transactions=None)
    txn = make_transaction(amount=9999, merchant_category="crypto")
    from app.rules.base import RuleContext

    hits = engine.evaluate(
        RuleContext(transaction=txn, seen_device=False, seen_merchant=False)
    )
    codes = {h.code for h in hits}
    assert "HIGH_AMOUNT" in codes
    assert "HIGH_RISK_MERCHANT" in codes
    assert "NEW_DEVICE" in codes
    assert "NEW_MERCHANT" in codes


def test_engine_evaluate_no_hits_for_clean_transaction():
    engine = RuleEngine(transactions=None)
    txn = make_transaction(amount=20)
    from app.rules.base import RuleContext

    hits = engine.evaluate(RuleContext(transaction=txn))
    assert hits == []
