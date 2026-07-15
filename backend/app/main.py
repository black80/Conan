from fastapi import FastAPI

from app.api.routes import cases, investigators, nfc_transactions, transactions
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title=settings.app_name)

app.include_router(transactions.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(investigators.router, prefix="/api")
app.include_router(nfc_transactions.router, prefix="/api")


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
