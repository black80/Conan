import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.transaction import Transaction
from app.queues.publisher import publish
from app.queues.topics import Topic
from app.repositories.transaction_repo import TransactionRepository
from app.schemas.transaction import TransactionCreate

logger = get_logger(__name__)


class TransactionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TransactionRepository(session)

    async def create(self, data: TransactionCreate) -> Transaction:
        txn = Transaction(**data.model_dump())
        await self.repo.add(txn)
        await self.session.commit()
        await self.session.refresh(txn, attribute_names=["rule_hits"])
        logger.info("transaction created id=%s amount=%s", txn.id, txn.amount)
        publish(Topic.TRANSACTION_CREATED, {"transaction_id": str(txn.id)})
        return txn

    async def get(self, transaction_id: uuid.UUID) -> Transaction | None:
        return await self.repo.get(transaction_id)

    async def list(self, limit: int = 100, offset: int = 0) -> list[Transaction]:
        return await self.repo.list(limit=limit, offset=offset)
