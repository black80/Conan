import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CaseResolution, CaseStatus
from app.schemas.transaction import TransactionOut
from app.schemas.nfc import NfcTransactionCreate


class AIRecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recommendation: str
    confidence: int
    priority: str
    summary: str
    reasoning: list[str]
    next_action: str
    model: str
    created_at: datetime


class InvestigatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transaction_id: uuid.UUID
    status: CaseStatus
    resolution: CaseResolution
    priority: str
    assigned_to: uuid.UUID | None
    final_decision: str | None
    notes: str | None
    closed_at: datetime | None
    created_at: datetime


class CaseDetailOut(CaseOut):
    transaction: TransactionOut
    recommendation: AIRecommendationOut | None
    investigator: InvestigatorOut | None
    nfc_payload: NfcTransactionCreate | None = Field(
        default=None, validation_alias="nfc_payload_data"
    )


class AssignRequest(BaseModel):
    investigator_id: uuid.UUID


class CloseRequest(BaseModel):
    resolution: CaseResolution
    final_decision: str
    notes: str | None = None


class OverrideRequest(BaseModel):
    final_decision: str
    resolution: CaseResolution
    notes: str | None = None
