import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_transaction_service
from app.schemas.transaction import TransactionCreate, TransactionOut
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    data: TransactionCreate,
    service: TransactionService = Depends(get_transaction_service),
) -> TransactionOut:
    txn = await service.create(data)
    return TransactionOut.model_validate(txn)


@router.get("", response_model=list[TransactionOut])
async def list_transactions(
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    service: TransactionService = Depends(get_transaction_service),
) -> list[TransactionOut]:
    txns = await service.list(limit=limit, offset=offset)
    return [TransactionOut.model_validate(t) for t in txns]


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(
    transaction_id: uuid.UUID,
    service: TransactionService = Depends(get_transaction_service),
) -> TransactionOut:
    txn = await service.get(transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return TransactionOut.model_validate(txn)
