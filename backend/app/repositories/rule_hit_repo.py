import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction_rule_hit import TransactionRuleHit


class RuleHitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_many(self, hits: list[TransactionRuleHit]) -> None:
        self.session.add_all(hits)
        await self.session.flush()

    async def list_for_transaction(
        self, transaction_id: uuid.UUID
    ) -> list[TransactionRuleHit]:
        result = await self.session.execute(
            select(TransactionRuleHit).where(
                TransactionRuleHit.transaction_id == transaction_id
            )
        )
        return list(result.scalars().all())
