import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class TransactionRuleHit(Base, TimestampMixin):
    __tablename__ = "transaction_rule_hits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), index=True, nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String, nullable=False)
    rule_name: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[int] = mapped_column(nullable=False)
    detail: Mapped[str] = mapped_column(String, nullable=False)

    transaction: Mapped["Transaction"] = relationship(back_populates="rule_hits")


from app.models.transaction import Transaction  # noqa: E402
