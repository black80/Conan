import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import CaseStatus
from app.models.investigation_case import InvestigationCase
from app.models.transaction import Transaction


class CaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, case: InvestigationCase) -> InvestigationCase:
        self.session.add(case)
        await self.session.flush()
        return case

    async def get(self, case_id: uuid.UUID) -> InvestigationCase | None:
        result = await self.session.execute(
            select(InvestigationCase)
            .options(
                selectinload(InvestigationCase.transaction).selectinload(
                    Transaction.rule_hits
                ),
                selectinload(InvestigationCase.recommendation),
                selectinload(InvestigationCase.investigator),
            )
            .where(InvestigationCase.id == case_id)
        )
        return result.scalar_one_or_none()

    async def get_by_transaction(
        self, transaction_id: uuid.UUID
    ) -> InvestigationCase | None:
        result = await self.session.execute(
            select(InvestigationCase).where(
                InvestigationCase.transaction_id == transaction_id
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self, status: CaseStatus | None = None, limit: int = 100, offset: int = 0
    ) -> list[InvestigationCase]:
        query = (
            select(InvestigationCase)
            .options(
                selectinload(InvestigationCase.recommendation),
                selectinload(InvestigationCase.investigator),
            )
            .order_by(InvestigationCase.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            query = query.where(InvestigationCase.status == status)
        result = await self.session.execute(query)
        return list(result.scalars().all())
