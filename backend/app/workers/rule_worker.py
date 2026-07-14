import uuid

from app.core.logging import get_logger
from app.services.investigation_service import InvestigationService
from app.workers.celery_app import celery_app
from app.workers.runtime import run_with_session

logger = get_logger(__name__)


@celery_app.task(name="app.workers.rule_worker.process_transaction", bind=True)
def process_transaction(self, transaction_id: str) -> None:
    logger.info("rule worker processing transaction %s", transaction_id)
    txn_id = uuid.UUID(transaction_id)

    async def handler(session):
        service = InvestigationService(session)
        await service.process_transaction(txn_id)

    run_with_session(handler)
