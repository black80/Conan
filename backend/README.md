# AI Fraud Investigation Copilot (PoC)

An **investigation layer** that sits on top of an existing fraud-detection engine.
It receives flagged transactions, applies predefined fraud rules, and — when rules
trigger — asks an LLM to analyze the full context and produce a structured
recommendation for a human investigator.

> This is **not** a fraud-detection engine. It assumes transactions arrive already
> flagged by an upstream system.

## Flow

```
POST /transactions
      │  (transaction.created)
      ▼
RabbitMQ ──► Rule Worker
                │
                ├── no rule triggered ──► transaction approved
                │
                └── rule(s) triggered ──► fraud.alert.created
                                              │
                                          RabbitMQ ──► AI Worker
                                                          │
                                                  OpenAI-compatible LLM
                                                          │
                                                  investigation.created
                                                          │
                                            Investigation Case (via API)
                                                          │
                                            Human investigator decision
```

## Tech stack

FastAPI (async) · PostgreSQL · SQLAlchemy 2.x · Alembic · RabbitMQ · Celery ·
Pydantic v2 · Docker Compose · OpenAI-compatible LLM abstraction.

## Architecture

Clean architecture with clear separation of concerns:

```
app/
  api/          FastAPI routers + dependency injection
  ai/           LLM client, prompt, and context builder
  core/         config, logging, enums
  db/           engines/sessions, declarative base, seed
  models/       SQLAlchemy ORM models (6 tables)
  repositories/ data access (Repository Pattern)
  rules/        rule interface + 10 rule implementations + registry
  schemas/      Pydantic request/response models
  services/     use cases (rule engine, transaction, investigation, case)
  workers/      Celery app + rule/ai workers
  queues/       event topics + publisher
migrations/     Alembic
tests/          unit tests for the rule engine
```

The API is async (asyncpg). Celery workers reuse the same async services by running
each task in a short-lived event loop with its own session.

## Running with Docker

```bash
cd backend
cp .env.example .env          # optionally set LLM_API_KEY
docker compose up --build
```

Services started: `postgres`, `rabbitmq` (management UI on :15672), `api` (:8000),
`rule-worker`, `ai-worker`. On startup the API container runs `alembic upgrade head`
and seeds the fraud rules and two demo investigators.

- API docs: http://localhost:8000/docs
- Health:   http://localhost:8000/health

### LLM configuration

Set these in `.env`:

```
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

Any OpenAI-compatible `/chat/completions` endpoint works. **If `LLM_API_KEY` is
empty, the AI worker falls back to a deterministic heuristic** so the full pipeline
runs end-to-end without external credentials.

## API

| Method | Path                        | Description                          |
|--------|-----------------------------|--------------------------------------|
| POST   | `/api/transactions`         | Ingest a (flagged) transaction       |
| POST   | `/api/nfc/transactions`     | Ingest a CaseClosed NFC transaction  |
| GET    | `/api/transactions`         | List transactions                    |
| GET    | `/api/transactions/{id}`    | Get a transaction + rule hits        |
| GET    | `/api/cases`                | List cases (`?status=open|assigned|closed`) |
| GET    | `/api/cases/{id}`           | Case detail + AI recommendation      |
| POST   | `/api/cases/{id}/assign`    | Assign to an investigator            |
| POST   | `/api/cases/{id}/close`     | Close with a resolution              |
| POST   | `/api/cases/{id}/override`  | Override / accept the recommendation |
| GET    | `/api/investigators`        | List investigators                   |

### Example

```bash
# high-amount + high-risk merchant → will be flagged and become a case
curl -X POST http://localhost:8000/api/transactions -H 'Content-Type: application/json' -d '{
  "customer_id": "cust_001", "card_token": "card_aaa", "amount": 4200,
  "currency": "USD", "merchant": "QuickCrypto", "merchant_category": "crypto",
  "country": "US", "device_id": "dev_9", "card_present": false
}'

curl http://localhost:8000/api/cases?status=open
```

## NFC reader integration

The CaseClosed Android reader posts its nested EMV payload to
`POST /api/nfc/transactions`. The endpoint maps the transaction context into the
existing rule pipeline and stores the complete original payload in
`nfc_transaction_payloads`. A successful submission returns the transaction ID,
its current asynchronous status, and whether the request was an idempotent retry.

The Android build reads `BACKEND_BASE_URL` from its ignored `local.properties` or
from a Gradle property. For a physical NFC phone, set it to a hostname or LAN IP
that resolves to the computer running Docker Compose, for example:

```properties
BACKEND_BASE_URL=http://Aymans-MacBook-Air.local:8000
```

The phone and backend must be on the same network, macOS must allow inbound port
8000, and the API container must be running. The URL remains editable in the app.

### PoC data warning

The NFC integration is intentionally configured for a controlled demo. It stores
the full PAN, expiry, cardholder/banking fields, and EMV metadata as plaintext
JSONB, returns that payload from case detail, and includes it in LLM context. The
API is unauthenticated and Android permits cleartext HTTP for local networking.
Use test cards and an isolated trusted network only. This configuration is not
suitable for production or PCI-regulated data.

## Fraud rules

`High Amount`, `Impossible Travel`, `Velocity`, `New Device`, `New Merchant`,
`High Risk Merchant`, `Different Country`, `Night Transaction`, `Multiple Declines`,
`Card Not Present`. Each implements `Rule.evaluate(ctx) -> RuleHit | None`.
Thresholds are configurable via environment variables (see `.env.example`).

## Event topics

`transaction.created`, `fraud.alert.created`, `investigation.created`, `case.closed`.

## Tests

```bash
cd backend
pip install -r requirements.txt
pytest
```

The unit tests cover every rule (trigger and pass cases) and the rule-engine
aggregation. They require no database or broker.
