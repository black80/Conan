import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_case_service
from app.core.enums import CaseStatus
from app.schemas.case import (
    AssignRequest,
    CaseDetailOut,
    CaseOut,
    CloseRequest,
    OverrideRequest,
)
from app.services.case_service import (
    CaseNotFoundError,
    CaseService,
    InvestigatorNotFoundError,
)

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("", response_model=list[CaseOut])
async def list_cases(
    status: CaseStatus | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    service: CaseService = Depends(get_case_service),
) -> list[CaseOut]:
    cases = await service.list(status=status, limit=limit, offset=offset)
    return [CaseOut.model_validate(c) for c in cases]


@router.get("/{case_id}", response_model=CaseDetailOut)
async def get_case(
    case_id: uuid.UUID,
    service: CaseService = Depends(get_case_service),
) -> CaseDetailOut:
    try:
        case = await service.get(case_id)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail="Case not found")
    return CaseDetailOut.model_validate(case)


@router.post("/{case_id}/assign", response_model=CaseDetailOut)
async def assign_case(
    case_id: uuid.UUID,
    data: AssignRequest,
    service: CaseService = Depends(get_case_service),
) -> CaseDetailOut:
    try:
        case = await service.assign(case_id, data)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail="Case not found")
    except InvestigatorNotFoundError:
        raise HTTPException(status_code=404, detail="Investigator not found")
    return CaseDetailOut.model_validate(case)


@router.post("/{case_id}/close", response_model=CaseDetailOut)
async def close_case(
    case_id: uuid.UUID,
    data: CloseRequest,
    service: CaseService = Depends(get_case_service),
) -> CaseDetailOut:
    try:
        case = await service.close(case_id, data)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail="Case not found")
    return CaseDetailOut.model_validate(case)


@router.post("/{case_id}/override", response_model=CaseDetailOut)
async def override_case(
    case_id: uuid.UUID,
    data: OverrideRequest,
    service: CaseService = Depends(get_case_service),
) -> CaseDetailOut:
    try:
        case = await service.override(case_id, data)
    except CaseNotFoundError:
        raise HTTPException(status_code=404, detail="Case not found")
    return CaseDetailOut.model_validate(case)
