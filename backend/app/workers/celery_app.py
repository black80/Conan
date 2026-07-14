from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "fraud_copilot",
    broker=settings.broker_url,
    backend="rpc://",
    include=["app.workers.rule_worker", "app.workers.ai_worker"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="rules",
    task_routes={
        "app.workers.rule_worker.*": {"queue": "rules"},
        "app.workers.ai_worker.*": {"queue": "ai"},
    },
)
