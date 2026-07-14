from app.core.config import settings
from app.rules.base import Rule, RuleContext, RuleHit

HIGH_RISK_CATEGORIES = {"gambling", "crypto", "money_transfer", "jewelry", "gift_card"}
CARD_NOT_PRESENT_MIN_AMOUNT = 200
IMPOSSIBLE_TRAVEL_MAX_HOURS = 2
MULTIPLE_DECLINES_THRESHOLD = 2


class HighAmountRule(Rule):
    code = "HIGH_AMOUNT"
    name = "High Amount"
    severity = 3

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        amount = float(ctx.transaction.amount)
        if amount >= settings.high_amount_threshold:
            return self.hit(
                f"Amount {amount:.2f} >= threshold {settings.high_amount_threshold:.2f}"
            )
        return None


class ImpossibleTravelRule(Rule):
    code = "IMPOSSIBLE_TRAVEL"
    name = "Impossible Travel"
    severity = 5

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if ctx.last_country is None or ctx.last_country_at is None:
            return None
        if ctx.last_country == ctx.transaction.country:
            return None
        delta = ctx.transaction.created_at - ctx.last_country_at
        hours = delta.total_seconds() / 3600
        if 0 <= hours <= IMPOSSIBLE_TRAVEL_MAX_HOURS:
            return self.hit(
                f"Country change {ctx.last_country}->{ctx.transaction.country} "
                f"within {hours:.1f}h"
            )
        return None


class VelocityRule(Rule):
    code = "VELOCITY"
    name = "Velocity"
    severity = 4

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if ctx.recent_txn_count >= settings.velocity_max_txns:
            return self.hit(
                f"{ctx.recent_txn_count} txns in last "
                f"{settings.velocity_window_minutes}m"
            )
        return None


class NewDeviceRule(Rule):
    code = "NEW_DEVICE"
    name = "New Device"
    severity = 2

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if not ctx.seen_device:
            return self.hit(f"Device {ctx.transaction.device_id} not seen before")
        return None


class NewMerchantRule(Rule):
    code = "NEW_MERCHANT"
    name = "New Merchant"
    severity = 1

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if not ctx.seen_merchant:
            return self.hit(f"Merchant {ctx.transaction.merchant} not seen before")
        return None


class HighRiskMerchantRule(Rule):
    code = "HIGH_RISK_MERCHANT"
    name = "High Risk Merchant"
    severity = 3

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        category = ctx.transaction.merchant_category.lower()
        if category in HIGH_RISK_CATEGORIES:
            return self.hit(f"Merchant category '{category}' is high risk")
        return None


class DifferentCountryRule(Rule):
    code = "DIFFERENT_COUNTRY"
    name = "Different Country"
    severity = 2

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if ctx.last_country and ctx.last_country != ctx.transaction.country:
            return self.hit(
                f"Country {ctx.transaction.country} differs from last "
                f"{ctx.last_country}"
            )
        return None


class NightTransactionRule(Rule):
    code = "NIGHT_TRANSACTION"
    name = "Night Transaction"
    severity = 1

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        hour = ctx.transaction.created_at.hour
        if settings.night_start_hour <= hour < settings.night_end_hour:
            return self.hit(f"Transaction at {hour:02d}:00 (night window)")
        return None


class MultipleDeclinesRule(Rule):
    code = "MULTIPLE_DECLINES"
    name = "Multiple Declines"
    severity = 4

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        if ctx.recent_decline_count >= MULTIPLE_DECLINES_THRESHOLD:
            return self.hit(
                f"{ctx.recent_decline_count} recent declines on card"
            )
        return None


class CardNotPresentRule(Rule):
    code = "CARD_NOT_PRESENT"
    name = "Card Not Present"
    severity = 2

    def evaluate(self, ctx: RuleContext) -> RuleHit | None:
        amount = float(ctx.transaction.amount)
        if not ctx.transaction.card_present and amount >= CARD_NOT_PRESENT_MIN_AMOUNT:
            return self.hit(
                f"Card-not-present transaction of {amount:.2f}"
            )
        return None
