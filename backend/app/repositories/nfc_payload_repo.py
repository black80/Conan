import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nfc_transaction_payload import NfcTransactionPayload


class NfcPayloadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, payload: NfcTransactionPayload) -> NfcTransactionPayload:
        self.session.add(payload)
        await self.session.flush()
        return payload

    async def get(self, transaction_id: uuid.UUID) -> NfcTransactionPayload | None:
        result = await self.session.execute(
            select(NfcTransactionPayload).where(
                NfcTransactionPayload.transaction_id == transaction_id
            )
        )
        return result.scalar_one_or_none()
