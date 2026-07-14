import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.investigator import Investigator


class InvestigatorRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, investigator_id: uuid.UUID) -> Investigator | None:
        result = await self.session.execute(
            select(Investigator).where(Investigator.id == investigator_id)
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[Investigator]:
        result = await self.session.execute(select(Investigator))
        return list(result.scalars().all())
