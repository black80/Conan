import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import TransactionStatus


class EmvApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aid: str
    label: str | None = None
    priority: int
    reading_step: str


class EmvServiceCodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interchange: str
    authorization_processing: str
    allowed_services: str


class RealCardDataCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pan: str = Field(pattern=r"^\d{12,19}$")
    expiry: str
    scheme: str
    card_state: str
    cardholder_name: str | None = None
    bic: str | None = None
    iban: str | None = None
    atr: str | None = None
    atr_descriptions: list[str] = Field(default_factory=list)
    track1_available: bool = False
    track2_available: bool = False
    applications: list[EmvApplicationCreate] = Field(default_factory=list)
    service_code: EmvServiceCodeCreate | None = None

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, value: str) -> str:
        match = re.fullmatch(r"(\d{2})/(\d{2})", value)
        if match is None or not 1 <= int(match.group(1)) <= 12:
            raise ValueError("expiry must use MM/yy with a valid month")
        return value


class AnomalyContextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    merchant_name: str = Field(min_length=1)
    merchant_category: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=2)
    terminal_ip: str
    ip_country_geolocation: str
    device_fingerprint: str = Field(min_length=1)
    card_present: bool = True
    declined: bool = False

    @field_validator("currency", "country")
    @classmethod
    def uppercase_code(cls, value: str) -> str:
        return value.upper()


class NfcTransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: uuid.UUID
    timestamp: datetime
    real_card_data: RealCardDataCreate
    anomaly_context: AnomalyContextCreate

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        if value.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be UTC")
        return value


class NfcTransactionAccepted(BaseModel):
    transaction_id: uuid.UUID
    status: TransactionStatus
    duplicate: bool
