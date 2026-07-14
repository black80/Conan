import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CaseStatus
from app.core.logging import get_logger
from app.models.investigation_case import InvestigationCase
from app.queues.publisher import publish
from app.queues.topics import Topic
from app.repositories.case_repo import CaseRepository
from app.repositories.investigator_repo import InvestigatorRepository
from app.schemas.case import AssignRequest, CloseRequest, OverrideRequest

logger = get_logger(__name__)


class CaseNotFoundError(Exception):
    pass


class InvestigatorNotFoundError(Exception):
    pass


class CaseService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.cases = CaseRepository(session)
        self.investigators = InvestigatorRepository(session)

    async def list(
        self, status: CaseStatus | None = None, limit: int = 100, offset: int = 0
    ) -> list[InvestigationCase]:
        return await self.cases.list(status=status, limit=limit, offset=offset)

    async def get(self, case_id: uuid.UUID) -> InvestigationCase:
        case = await self.cases.get(case_id)
        if case is None:
            raise CaseNotFoundError(str(case_id))
        return case

    async def assign(
        self, case_id: uuid.UUID, data: AssignRequest
    ) -> InvestigationCase:
        case = await self.get(case_id)
        investigator = await self.investigators.get(data.investigator_id)
        if investigator is None:
            raise InvestigatorNotFoundError(str(data.investigator_id))
        case.assigned_to = investigator.id
        if case.status == CaseStatus.OPEN:
            case.status = CaseStatus.ASSIGNED
        await self.session.commit()
        logger.info("case %s assigned to %s", case_id, investigator.id)
        return await self.get(case_id)

    async def close(
        self, case_id: uuid.UUID, data: CloseRequest
    ) -> InvestigationCase:
        case = await self.get(case_id)
        case.status = CaseStatus.CLOSED
        case.resolution = data.resolution
        case.final_decision = data.final_decision
        case.notes = data.notes
        case.closed_at = datetime.now(timezone.utc)
        await self.session.commit()
        logger.info("case %s closed resolution=%s", case_id, data.resolution)
        publish(
            Topic.CASE_CLOSED,
            {"case_id": str(case_id), "resolution": data.resolution.value},
        )
        return await self.get(case_id)

    async def override(
        self, case_id: uuid.UUID, data: OverrideRequest
    ) -> InvestigationCase:
        case = await self.get(case_id)
        case.final_decision = data.final_decision
        case.resolution = data.resolution
        if data.notes is not None:
            case.notes = data.notes
        await self.session.commit()
        logger.info("case %s overridden decision=%s", case_id, data.final_decision)
        return await self.get(case_id)
