from pydantic import BaseModel, Field

from app.core.enums import Priority, Recommendation


class AIResult(BaseModel):
    recommendation: Recommendation
    confidence: int = Field(ge=0, le=100)
    priority: Priority
    summary: str
    reasoning: list[str] = []
    next_action: str
