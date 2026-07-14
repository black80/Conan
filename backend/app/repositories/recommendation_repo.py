from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_recommendation import AIRecommendation


class RecommendationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, recommendation: AIRecommendation) -> AIRecommendation:
        self.session.add(recommendation)
        await self.session.flush()
        return recommendation
