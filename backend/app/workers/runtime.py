import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

T = TypeVar("T")


def run_with_session(handler: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def _runner() -> T:
        engine = create_async_engine(settings.async_database_url)
        session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        try:
            async with session_factory() as session:
                return await handler(session)
        finally:
            await engine.dispose()

    return asyncio.run(_runner())
