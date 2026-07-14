import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import TransactionStatus
from app.db.base import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    card_token: Mapped[str] = mapped_column(String, index=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    merchant: Mapped[str] = mapped_column(String, nullable=False)
    merchant_category: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    device_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    card_present: Mapped[bool] = mapped_column(default=True, nullable=False)
    declined: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        String, default=TransactionStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rule_hits: Mapped[list["TransactionRuleHit"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    case: Mapped["InvestigationCase | None"] = relationship(
        back_populates="transaction", uselist=False
    )


from app.models.investigation_case import InvestigationCase  # noqa: E402
from app.models.transaction_rule_hit import TransactionRuleHit  # noqa: E402
