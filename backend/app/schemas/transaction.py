import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import TransactionStatus


class TransactionCreate(BaseModel):
    customer_id: str
    card_token: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    merchant: str
    merchant_category: str
    country: str = Field(min_length=2, max_length=2)
    device_id: str
    card_present: bool = True
    declined: bool = False


class RuleHitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_code: str
    rule_name: str
    severity: int
    detail: str


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: str
    card_token: str
    amount: Decimal
    currency: str
    merchant: str
    merchant_category: str
    country: str
    device_id: str
    card_present: bool
    declined: bool
    status: TransactionStatus
    created_at: datetime
    rule_hits: list[RuleHitOut] = []
