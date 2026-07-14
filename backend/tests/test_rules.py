from datetime import datetime, timezone

from app.rules.base import RuleContext
from app.rules.implementations import (
    CardNotPresentRule,
    DifferentCountryRule,
    HighAmountRule,
    HighRiskMerchantRule,
    ImpossibleTravelRule,
    MultipleDeclinesRule,
    NewDeviceRule,
    NewMerchantRule,
    NightTransactionRule,
    VelocityRule,
)
from tests.conftest import make_transaction


def ctx(txn, **kwargs) -> RuleContext:
    return RuleContext(transaction=txn, **kwargs)


def test_high_amount_triggers():
    txn = make_transaction(amount=5000)
    assert HighAmountRule().evaluate(ctx(txn)) is not None


def test_high_amount_pass():
    txn = make_transaction(amount=50)
    assert HighAmountRule().evaluate(ctx(txn)) is None


def test_impossible_travel_triggers():
    txn = make_transaction(
        country="FR", created_at=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    )
    c = ctx(
        txn,
        last_country="US",
        last_country_at=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc),
    )
    assert ImpossibleTravelRule().evaluate(c) is not None


def test_impossible_travel_pass_when_enough_time():
    txn = make_transaction(
        country="FR", created_at=datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
    )
    c = ctx(
        txn,
        last_country="US",
        last_country_at=datetime(2026, 7, 13, 11, 0, tzinfo=timezone.utc),
    )
    assert ImpossibleTravelRule().evaluate(c) is None


def test_velocity_triggers():
    txn = make_transaction()
    assert VelocityRule().evaluate(ctx(txn, recent_txn_count=5)) is not None


def test_velocity_pass():
    txn = make_transaction()
    assert VelocityRule().evaluate(ctx(txn, recent_txn_count=2)) is None


def test_new_device_triggers():
    txn = make_transaction()
    assert NewDeviceRule().evaluate(ctx(txn, seen_device=False)) is not None


def test_new_device_pass():
    txn = make_transaction()
    assert NewDeviceRule().evaluate(ctx(txn, seen_device=True)) is None


def test_new_merchant_triggers():
    txn = make_transaction()
    assert NewMerchantRule().evaluate(ctx(txn, seen_merchant=False)) is not None


def test_high_risk_merchant_triggers():
    txn = make_transaction(merchant_category="crypto")
    assert HighRiskMerchantRule().evaluate(ctx(txn)) is not None


def test_high_risk_merchant_pass():
    txn = make_transaction(merchant_category="coffee")
    assert HighRiskMerchantRule().evaluate(ctx(txn)) is None


def test_different_country_triggers():
    txn = make_transaction(country="FR")
    assert DifferentCountryRule().evaluate(ctx(txn, last_country="US")) is not None


def test_different_country_pass_same():
    txn = make_transaction(country="US")
    assert DifferentCountryRule().evaluate(ctx(txn, last_country="US")) is None


def test_night_transaction_triggers():
    txn = make_transaction(
        created_at=datetime(2026, 7, 13, 3, 0, tzinfo=timezone.utc)
    )
    assert NightTransactionRule().evaluate(ctx(txn)) is not None


def test_night_transaction_pass():
    txn = make_transaction(
        created_at=datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    )
    assert NightTransactionRule().evaluate(ctx(txn)) is None


def test_multiple_declines_triggers():
    txn = make_transaction()
    assert MultipleDeclinesRule().evaluate(ctx(txn, recent_decline_count=3)) is not None


def test_multiple_declines_pass():
    txn = make_transaction()
    assert MultipleDeclinesRule().evaluate(ctx(txn, recent_decline_count=1)) is None


def test_card_not_present_triggers():
    txn = make_transaction(card_present=False, amount=500)
    assert CardNotPresentRule().evaluate(ctx(txn)) is not None


def test_card_not_present_pass_small_amount():
    txn = make_transaction(card_present=False, amount=10)
    assert CardNotPresentRule().evaluate(ctx(txn)) is None
