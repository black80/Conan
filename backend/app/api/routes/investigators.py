from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.investigator_repo import InvestigatorRepository
from app.schemas.case import InvestigatorOut

router = APIRouter(prefix="/investigators", tags=["investigators"])


@router.get("", response_model=list[InvestigatorOut])
async def list_investigators(
    session: AsyncSession = Depends(get_session),
) -> list[InvestigatorOut]:
    repo = InvestigatorRepository(session)
    investigators = await repo.list()
    return [InvestigatorOut.model_validate(i) for i in investigators]
