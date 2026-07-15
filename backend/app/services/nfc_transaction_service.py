import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import TransactionStatus
from app.core.logging import get_logger
from app.models.nfc_transaction_payload import NfcTransactionPayload
from app.models.transaction import Transaction
from app.queues.publisher import publish
from app.queues.topics import Topic
from app.repositories.nfc_payload_repo import NfcPayloadRepository
from app.repositories.transaction_repo import TransactionRepository
from app.schemas.nfc import NfcTransactionAccepted, NfcTransactionCreate

logger = get_logger(__name__)


class NfcTransactionConflictError(Exception):
    pass


class NfcTransactionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.transactions = TransactionRepository(session)
        self.payloads = NfcPayloadRepository(session)

    async def create(
        self, data: NfcTransactionCreate
    ) -> tuple[NfcTransactionAccepted, bool]:
        stored_payload = data.model_dump(mode="json")
        existing = await self.transactions.get(data.transaction_id)
        if existing is not None:
            nfc_payload = await self.payloads.get(data.transaction_id)
            if nfc_payload is None or nfc_payload.payload != stored_payload:
                raise NfcTransactionConflictError(str(data.transaction_id))
            return (
                NfcTransactionAccepted(
                    transaction_id=existing.id,
                    status=existing.status,
                    duplicate=True,
                ),
                False,
            )

        anomaly = data.anomaly_context
        digest = hashlib.sha256(data.real_card_data.pan.encode("ascii")).hexdigest()
        transaction = Transaction(
            id=data.transaction_id,
            customer_id=f"nfc_customer_{digest}",
            card_token=f"nfc_card_{digest}",
            amount=anomaly.amount,
            currency=anomaly.currency,
            merchant=anomaly.merchant_name,
            merchant_category=anomaly.merchant_category,
            country=anomaly.country,
            device_id=anomaly.device_fingerprint,
            card_present=anomaly.card_present,
            declined=anomaly.declined,
            status=TransactionStatus.PENDING,
            created_at=data.timestamp,
        )
        try:
            await self.transactions.add(transaction)
            await self.payloads.add(
                NfcTransactionPayload(
                    transaction_id=transaction.id,
                    payload=stored_payload,
                )
            )
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.transactions.get(data.transaction_id)
            nfc_payload = await self.payloads.get(data.transaction_id)
            if (
                existing is None
                or nfc_payload is None
                or nfc_payload.payload != stored_payload
            ):
                raise NfcTransactionConflictError(str(data.transaction_id))
            return (
                NfcTransactionAccepted(
                    transaction_id=existing.id,
                    status=existing.status,
                    duplicate=True,
                ),
                False,
            )
        logger.info(
            "NFC transaction created id=%s amount=%s",
            transaction.id,
            transaction.amount,
        )
        publish(Topic.TRANSACTION_CREATED, {"transaction_id": str(transaction.id)})
        return (
            NfcTransactionAccepted(
                transaction_id=transaction.id,
                status=transaction.status,
                duplicate=False,
            ),
            True,
        )
