import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.context import build_ai_context
from app.api.deps import get_nfc_transaction_service
from app.core.enums import CaseResolution, CaseStatus, TransactionStatus
from app.main import app
from app.models.investigation_case import InvestigationCase
from app.models.nfc_transaction_payload import NfcTransactionPayload
from app.schemas.case import CaseDetailOut
from app.schemas.nfc import NfcTransactionAccepted, NfcTransactionCreate
from app.services.nfc_transaction_service import (
    NfcTransactionConflictError,
    NfcTransactionService,
)


TRANSACTION_ID = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")


def nfc_request(**overrides) -> NfcTransactionCreate:
    payload = {
        "transaction_id": str(TRANSACTION_ID),
        "timestamp": "2026-07-15T10:26:47Z",
        "real_card_data": {
            "pan": "5412751234124532",
            "expiry": "07/29",
            "scheme": "VISA",
            "card_state": "ACTIVE",
            "cardholder_name": "DEMO HOLDER",
            "bic": "DEMOBIC",
            "iban": "SA001234567890",
            "atr": "3B8F8001",
            "atr_descriptions": ["Contactless EMV card"],
            "track1_available": False,
            "track2_available": True,
            "applications": [
                {
                    "aid": "A0000000031010",
                    "label": "VISA CREDIT",
                    "priority": 1,
                    "reading_step": "READ",
                }
            ],
            "service_code": {
                "interchange": "INTERNATIONAL",
                "authorization_processing": "NORMAL",
                "allowed_services": "GOODS_AND_SERVICES_ONLY",
            },
        },
        "anomaly_context": {
            "amount": "8250.50",
            "currency": "sar",
            "merchant_name": "HighEnd Electronics Online",
            "merchant_category": "electronics",
            "country": "sa",
            "terminal_ip": "103.24.12.5",
            "ip_country_geolocation": "Saudi Arabia",
            "device_fingerprint": "demo/device/build",
            "card_present": True,
            "declined": False,
        },
    }
    payload.update(overrides)
    return NfcTransactionCreate.model_validate(payload)


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class FakeTransactionRepository:
    def __init__(self):
        self.transaction = None

    async def get(self, transaction_id):
        if self.transaction is not None and self.transaction.id == transaction_id:
            return self.transaction
        return None

    async def add(self, transaction):
        self.transaction = transaction
        return transaction


class FakePayloadRepository:
    def __init__(self):
        self.payload = None

    async def get(self, transaction_id):
        if self.payload is not None and self.payload.transaction_id == transaction_id:
            return self.payload
        return None

    async def add(self, payload):
        self.payload = payload
        return payload


def service_with_fakes():
    session = FakeSession()
    service = NfcTransactionService(session)
    service.transactions = FakeTransactionRepository()
    service.payloads = FakePayloadRepository()
    return service, session


def test_nfc_schema_normalizes_codes_and_requires_valid_expiry():
    data = nfc_request()
    assert data.anomaly_context.currency == "SAR"
    assert data.anomaly_context.country == "SA"

    with pytest.raises(ValidationError):
        nfc_request(
            real_card_data={
                **data.real_card_data.model_dump(),
                "expiry": "13/29",
            }
        )

    with pytest.raises(ValidationError):
        nfc_request(timestamp="2026-07-15T13:26:47+03:00")


@pytest.mark.asyncio
async def test_nfc_submission_maps_and_persists_full_payload(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.nfc_transaction_service.publish",
        lambda topic, payload: events.append((topic, payload)),
    )
    service, session = service_with_fakes()
    data = nfc_request()

    accepted, created = await service.create(data)

    transaction = service.transactions.transaction
    assert created is True
    assert accepted.transaction_id == TRANSACTION_ID
    assert accepted.status == TransactionStatus.PENDING
    assert accepted.duplicate is False
    assert transaction.amount == Decimal("8250.50")
    assert transaction.currency == "SAR"
    assert transaction.country == "SA"
    assert transaction.created_at == datetime(
        2026, 7, 15, 10, 26, 47, tzinfo=timezone.utc
    )
    assert transaction.card_token.startswith("nfc_card_")
    assert transaction.customer_id.startswith("nfc_customer_")
    assert service.payloads.payload.payload["real_card_data"]["pan"] == (
        "5412751234124532"
    )
    assert session.commits == 1
    assert len(events) == 1


@pytest.mark.asyncio
async def test_identical_retry_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        "app.services.nfc_transaction_service.publish", lambda topic, payload: None
    )
    service, session = service_with_fakes()
    data = nfc_request()
    await service.create(data)

    accepted, created = await service.create(data)

    assert created is False
    assert accepted.duplicate is True
    assert session.commits == 1


@pytest.mark.asyncio
async def test_reused_id_with_different_payload_conflicts(monkeypatch):
    monkeypatch.setattr(
        "app.services.nfc_transaction_service.publish", lambda topic, payload: None
    )
    service, _ = service_with_fakes()
    await service.create(nfc_request())
    changed = nfc_request()
    changed.anomaly_context.amount = Decimal("9000.00")

    with pytest.raises(NfcTransactionConflictError):
        await service.create(changed)


def test_ai_context_includes_complete_nfc_payload():
    data = nfc_request()
    payload = data.model_dump(mode="json")
    from tests.conftest import make_transaction

    txn = make_transaction(id=TRANSACTION_ID)
    txn.nfc_payload = NfcTransactionPayload(
        transaction_id=TRANSACTION_ID,
        payload=payload,
    )

    context = build_ai_context(txn, [], [], {})

    assert context["nfc_payload"]["real_card_data"]["pan"] == "5412751234124532"
    assert context["nfc_payload"]["real_card_data"]["iban"] == "SA001234567890"


def test_case_detail_returns_complete_nfc_payload():
    from tests.conftest import make_transaction

    data = nfc_request()
    txn = make_transaction(id=TRANSACTION_ID)
    txn.nfc_payload = NfcTransactionPayload(
        transaction_id=TRANSACTION_ID,
        payload=data.model_dump(mode="json"),
    )
    case = InvestigationCase(
        id=uuid.uuid4(),
        transaction_id=TRANSACTION_ID,
        transaction=txn,
        status=CaseStatus.OPEN,
        resolution=CaseResolution.UNRESOLVED,
        priority="high",
        assigned_to=None,
        final_decision=None,
        notes=None,
        closed_at=None,
        created_at=datetime.now(timezone.utc),
    )

    output = CaseDetailOut.model_validate(case)

    assert output.nfc_payload is not None
    assert output.nfc_payload.real_card_data.pan == "5412751234124532"


def test_nfc_route_returns_created_and_duplicate_statuses():
    class FakeService:
        calls = 0

        async def create(self, data):
            self.calls += 1
            return (
                NfcTransactionAccepted(
                    transaction_id=data.transaction_id,
                    status=TransactionStatus.PENDING,
                    duplicate=self.calls > 1,
                ),
                self.calls == 1,
            )

    service = FakeService()
    app.dependency_overrides[get_nfc_transaction_service] = lambda: service
    try:
        with TestClient(app) as client:
            body = nfc_request().model_dump(mode="json")
            created = client.post("/api/nfc/transactions", json=body)
            duplicate = client.post("/api/nfc/transactions", json=body)
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert created.json()["duplicate"] is False
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_nfc_route_returns_conflict():
    class ConflictingService:
        async def create(self, data):
            raise NfcTransactionConflictError(str(data.transaction_id))

    app.dependency_overrides[get_nfc_transaction_service] = lambda: ConflictingService()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/nfc/transactions",
                json=nfc_request().model_dump(mode="json"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Transaction ID already exists with a different payload"
    )
