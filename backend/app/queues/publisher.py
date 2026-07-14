from app.core.logging import get_logger
from app.queues.topics import Topic
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

TOPIC_TASKS: dict[Topic, str] = {
    Topic.TRANSACTION_CREATED: "app.workers.rule_worker.process_transaction",
    Topic.FRAUD_ALERT_CREATED: "app.workers.ai_worker.analyze_alert",
}

TOPIC_QUEUES: dict[Topic, str] = {
    Topic.TRANSACTION_CREATED: "rules",
    Topic.FRAUD_ALERT_CREATED: "ai",
}


def publish(topic: Topic, payload: dict) -> None:
    logger.info("event %s %s", topic.value, payload)
    task = TOPIC_TASKS.get(topic)
    if task is None:
        return
    celery_app.send_task(
        task, kwargs=payload, queue=TOPIC_QUEUES.get(topic, "rules")
    )
