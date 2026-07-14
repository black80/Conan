# AI Fraud Investigation Copilot PoC - Codex Implementation Prompt

You are a senior software engineer. Build a production-quality Proof of Concept for an **AI Fraud Investigation Copilot**.

## Goal

Do **not** build a fraud detection engine.

Assume an existing fraud engine already flags suspicious transactions.

Your job is to build the investigation layer that:
- Receives flagged transactions.
- Applies predefined fraud rules.
- If no rule is triggered, approves the transaction.
- If one or more rules are triggered, sends the case to an LLM.
- The LLM analyzes the complete context and returns:
  - recommendation
  - confidence
  - reasoning
  - priority
  - next action
- Create an investigation case if required.
- Allow a human investigator to make the final decision.

## Tech Stack

- FastAPI
- PostgreSQL
- SQLAlchemy 2.x
- Alembic
- RabbitMQ
- Celery workers
- Pydantic v2
- Docker + Docker Compose
- OpenAI-compatible LLM abstraction

## Architecture

```text
POS Demo
  |
FastAPI
  |
PostgreSQL
  |
transaction.created
  |
RabbitMQ
  |
Rule Worker
  |-- No rule -> Approve
  |-- Rule -> fraud.alert.created
                  |
              RabbitMQ
                  |
               AI Worker
                  |
             OpenAI-compatible LLM
                  |
         Investigation Case
                  |
      Investigator Dashboard
```

## APIs

- POST /transactions
- GET /transactions
- GET /transactions/{id}
- GET /cases
- GET /cases/{id}
- POST /cases/{id}/assign
- POST /cases/{id}/close
- POST /cases/{id}/override

## Database Tables

transactions:
- id
- customer_id
- card_token
- amount
- currency
- merchant
- merchant_category
- country
- device_id
- status
- created_at

fraud_rules
transaction_rule_hits
investigation_cases
ai_recommendations
investigators

## Initial Rules

- High Amount
- Impossible Travel
- Velocity
- New Device
- New Merchant
- High Risk Merchant
- Different Country
- Night Transaction
- Multiple Declines
- Card Not Present

Each rule implements:

```python
class Rule:
    def evaluate(self, transaction):
        ...
```

## Event Topics

- transaction.created
- fraud.alert.created
- investigation.created
- case.closed

## AI Context

Provide:
- transaction
- customer profile
- previous transactions
- triggered rules
- merchant information
- device information
- fraud statistics

Prompt the LLM to return ONLY JSON:

```json
{
  "recommendation": "",
  "confidence": 0,
  "priority": "",
  "summary": "",
  "reasoning": [],
  "next_action": ""
}
```

## Investigator Dashboard

Display:
- open cases
- assigned cases
- closed cases

Case details:
- transaction
- triggered rules
- AI reasoning
- confidence
- recommendation

Actions:
- accept recommendation
- override recommendation
- mark fraud
- mark legitimate
- add notes
- close case

## Project Structure

```text
backend/
  app/
    api/
    ai/
    core/
    db/
    models/
    repositories/
    rules/
    schemas/
    services/
    workers/
    queues/
    utils/
    main.py
migrations/
tests/
docker-compose.yml
```

## Requirements

- Clean Architecture
- Repository Pattern
- Dependency Injection where appropriate
- Async FastAPI
- Event-driven workflow
- Typed code
- Dockerized
- Environment variables
- Logging
- Error handling
- Unit tests for rule engine
- README with setup instructions

Generate complete source code with clear separation of concerns. dont write comment. and the most important thing is keep it simple.
