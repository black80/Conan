import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class NfcTransactionPayload(Base, TimestampMixin):
    __tablename__ = "nfc_transaction_payloads"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    transaction: Mapped["Transaction"] = relationship(back_populates="nfc_payload")


from app.models.transaction import Transaction  # noqa: E402
