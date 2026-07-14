import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import TransactionStatus
from app.models.transaction import Transaction


class TransactionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, transaction: Transaction) -> Transaction:
        self.session.add(transaction)
        await self.session.flush()
        return transaction

    async def get(self, transaction_id: uuid.UUID) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction)
            .options(selectinload(Transaction.rule_hits))
            .where(Transaction.id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def list(self, limit: int = 100, offset: int = 0) -> list[Transaction]:
        result = await self.session.execute(
            select(Transaction)
            .options(selectinload(Transaction.rule_hits))
            .order_by(Transaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def history_for_customer(
        self, customer_id: str, exclude_id: uuid.UUID, limit: int = 10
    ) -> list[Transaction]:
        result = await self.session.execute(
            select(Transaction)
            .where(
                Transaction.customer_id == customer_id,
                Transaction.id != exclude_id,
            )
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_recent_for_card(
        self, card_token: str, since: datetime, exclude_id: uuid.UUID
    ) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.card_token == card_token,
                Transaction.created_at >= since,
                Transaction.id != exclude_id,
            )
        )
        return int(result.scalar_one())

    async def count_recent_declines_for_card(
        self, card_token: str, since: datetime, exclude_id: uuid.UUID
    ) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.card_token == card_token,
                Transaction.declined.is_(True),
                Transaction.created_at >= since,
                Transaction.id != exclude_id,
            )
        )
        return int(result.scalar_one())

    async def seen_device(self, customer_id: str, device_id: str) -> bool:
        result = await self.session.execute(
            select(Transaction.id)
            .where(
                Transaction.customer_id == customer_id,
                Transaction.device_id == device_id,
                Transaction.status != TransactionStatus.PENDING,
            )
            .limit(1)
        )
        return result.first() is not None

    async def seen_merchant(self, customer_id: str, merchant: str) -> bool:
        result = await self.session.execute(
            select(Transaction.id)
            .where(
                Transaction.customer_id == customer_id,
                Transaction.merchant == merchant,
                Transaction.status != TransactionStatus.PENDING,
            )
            .limit(1)
        )
        return result.first() is not None

    async def last_country(
        self, customer_id: str, exclude_id: uuid.UUID
    ) -> tuple[str, datetime] | None:
        result = await self.session.execute(
            select(Transaction.country, Transaction.created_at)
            .where(
                Transaction.customer_id == customer_id,
                Transaction.id != exclude_id,
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    async def fraud_stats(self, customer_id: str) -> dict:
        total = await self.session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.customer_id == customer_id)
        )
        flagged = await self.session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.customer_id == customer_id,
                Transaction.status.in_(
                    [
                        TransactionStatus.FLAGGED,
                        TransactionStatus.UNDER_INVESTIGATION,
                    ]
                ),
            )
        )
        return {
            "customer_total_transactions": int(total.scalar_one()),
            "customer_flagged_transactions": int(flagged.scalar_one()),
        }

    def window_start(self, minutes: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(minutes=minutes)
