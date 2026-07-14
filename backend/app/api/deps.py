from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_session
from app.services.case_service import CaseService
from app.services.transaction_service import TransactionService


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_async_session():
        yield session


def get_transaction_service(
    session: AsyncSession = Depends(get_session),
) -> TransactionService:
    return TransactionService(session)


def get_case_service(
    session: AsyncSession = Depends(get_session),
) -> CaseService:
    return CaseService(session)
