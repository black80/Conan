from app.core.config import settings
from app.models.transaction import Transaction
from app.repositories.transaction_repo import TransactionRepository
from app.rules.base import RuleContext, RuleHit
from app.rules.registry import all_rules


class RuleEngine:
    def __init__(self, transactions: TransactionRepository):
        self.transactions = transactions

    async def build_context(self, txn: Transaction) -> RuleContext:
        velocity_since = self.transactions.window_start(
            settings.velocity_window_minutes
        )
        recent_txn_count = await self.transactions.count_recent_for_card(
            txn.card_token, velocity_since, txn.id
        )
        recent_decline_count = await self.transactions.count_recent_declines_for_card(
            txn.card_token, velocity_since, txn.id
        )
        seen_device = await self.transactions.seen_device(
            txn.customer_id, txn.device_id
        )
        seen_merchant = await self.transactions.seen_merchant(
            txn.customer_id, txn.merchant
        )
        last = await self.transactions.last_country(txn.customer_id, txn.id)
        last_country, last_country_at = last if last else (None, None)
        return RuleContext(
            transaction=txn,
            recent_txn_count=recent_txn_count,
            recent_decline_count=recent_decline_count,
            seen_device=seen_device,
            seen_merchant=seen_merchant,
            last_country=last_country,
            last_country_at=last_country_at,
        )

    def evaluate(self, ctx: RuleContext) -> list[RuleHit]:
        hits: list[RuleHit] = []
        for rule in all_rules():
            hit = rule.evaluate(ctx)
            if hit is not None:
                hits.append(hit)
        return hits

    async def run(self, txn: Transaction) -> list[RuleHit]:
        ctx = await self.build_context(txn)
        return self.evaluate(ctx)
