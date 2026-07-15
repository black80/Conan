from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import get_nfc_transaction_service
from app.schemas.nfc import NfcTransactionAccepted, NfcTransactionCreate
from app.services.nfc_transaction_service import (
    NfcTransactionConflictError,
    NfcTransactionService,
)

router = APIRouter(prefix="/nfc/transactions", tags=["nfc-transactions"])


@router.post(
    "",
    response_model=NfcTransactionAccepted,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {"description": "Identical submission already accepted"},
        status.HTTP_409_CONFLICT: {"description": "Transaction ID payload conflict"},
    },
)
async def create_nfc_transaction(
    data: NfcTransactionCreate,
    response: Response,
    service: NfcTransactionService = Depends(get_nfc_transaction_service),
) -> NfcTransactionAccepted:
    try:
        accepted, created = await service.create(data)
    except NfcTransactionConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transaction ID already exists with a different payload",
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return accepted
