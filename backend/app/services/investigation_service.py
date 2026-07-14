import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import llm_client
from app.ai.context import build_ai_context
from app.core.enums import CaseStatus, TransactionStatus
from app.core.logging import get_logger
from app.models.ai_recommendation import AIRecommendation
from app.models.investigation_case import InvestigationCase
from app.models.transaction_rule_hit import TransactionRuleHit
from app.queues.publisher import publish
from app.queues.topics import Topic
from app.repositories.case_repo import CaseRepository
from app.repositories.recommendation_repo import RecommendationRepository
from app.repositories.rule_hit_repo import RuleHitRepository
from app.repositories.transaction_repo import TransactionRepository
from app.rules.base import RuleHit
from app.services.rule_engine import RuleEngine

logger = get_logger(__name__)


class InvestigationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.transactions = TransactionRepository(session)
        self.rule_hits = RuleHitRepository(session)
        self.cases = CaseRepository(session)
        self.recommendations = RecommendationRepository(session)
        self.engine = RuleEngine(self.transactions)

    async def process_transaction(self, transaction_id: uuid.UUID) -> None:
        txn = await self.transactions.get(transaction_id)
        if txn is None:
            logger.warning("transaction %s not found", transaction_id)
            return
        if txn.status != TransactionStatus.PENDING:
            logger.info("transaction %s already processed", transaction_id)
            return

        hits = await self.engine.run(txn)
        if not hits:
            txn.status = TransactionStatus.APPROVED
            await self.session.commit()
            logger.info("transaction %s approved (no rules)", transaction_id)
            return

        await self.rule_hits.add_many(
            [
                TransactionRuleHit(
                    transaction_id=txn.id,
                    rule_code=h.code,
                    rule_name=h.name,
                    severity=h.severity,
                    detail=h.detail,
                )
                for h in hits
            ]
        )
        txn.status = TransactionStatus.FLAGGED
        await self.session.commit()
        logger.info(
            "transaction %s flagged with %d rule(s)", transaction_id, len(hits)
        )
        publish(
            Topic.FRAUD_ALERT_CREATED,
            {"transaction_id": str(txn.id)},
        )

    async def analyze_alert(self, transaction_id: uuid.UUID) -> None:
        txn = await self.transactions.get(transaction_id)
        if txn is None:
            logger.warning("transaction %s not found", transaction_id)
            return
        existing = await self.cases.get_by_transaction(txn.id)
        if existing is not None:
            logger.info("case already exists for transaction %s", transaction_id)
            return

        hits = [
            RuleHit(
                code=h.rule_code,
                name=h.rule_name,
                severity=h.severity,
                detail=h.detail,
            )
            for h in txn.rule_hits
        ]
        history = await self.transactions.history_for_customer(
            txn.customer_id, txn.id
        )
        stats = await self.transactions.fraud_stats(txn.customer_id)
        context = build_ai_context(txn, hits, history, stats)

        result = llm_client.analyze(context)
        logger.info(
            "AI analysis for %s: %s (%d%%)",
            transaction_id,
            result.recommendation,
            result.confidence,
        )

        case = InvestigationCase(
            transaction_id=txn.id,
            status=CaseStatus.OPEN,
            priority=result.priority,
        )
        await self.cases.add(case)
        recommendation = AIRecommendation(
            case_id=case.id,
            recommendation=result.recommendation,
            confidence=result.confidence,
            priority=result.priority,
            summary=result.summary,
            reasoning=result.reasoning,
            next_action=result.next_action,
            model=llm_client.model if llm_client.enabled else "heuristic-fallback",
        )
        await self.recommendations.add(recommendation)
        txn.status = TransactionStatus.UNDER_INVESTIGATION
        await self.session.commit()

        publish(
            Topic.INVESTIGATION_CREATED,
            {"case_id": str(case.id), "transaction_id": str(txn.id)},
        )
