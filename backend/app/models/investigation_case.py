import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import CaseResolution, CaseStatus
from app.db.base import Base, TimestampMixin


class InvestigationCase(Base, TimestampMixin):
    __tablename__ = "investigation_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id"),
        unique=True,
        index=True,
        nullable=False,
    )
    status: Mapped[CaseStatus] = mapped_column(
        String, default=CaseStatus.OPEN, index=True, nullable=False
    )
    resolution: Mapped[CaseResolution] = mapped_column(
        String, default=CaseResolution.UNRESOLVED, nullable=False
    )
    priority: Mapped[str] = mapped_column(String, default="medium", nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigators.id"), nullable=True
    )
    final_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="case")
    recommendation: Mapped["AIRecommendation | None"] = relationship(
        back_populates="case", uselist=False, cascade="all, delete-orphan"
    )
    investigator: Mapped["Investigator | None"] = relationship()

    @property
    def nfc_payload_data(self) -> dict | None:
        if self.transaction.nfc_payload is None:
            return None
        return self.transaction.nfc_payload.payload


from app.models.ai_recommendation import AIRecommendation  # noqa: E402
from app.models.investigator import Investigator  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
