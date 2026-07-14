from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.models.transaction import Transaction


@dataclass
class RuleContext:
    transaction: Transaction
    recent_txn_count: int = 0
    recent_decline_count: int = 0
    seen_device: bool = True
    seen_merchant: bool = True
    last_country: str | None = None
    last_country_at: datetime | None = None


@dataclass
class RuleHit:
    code: str
    name: str
    severity: int
    detail: str


class Rule(ABC):
    code: str
    name: str
    severity: int

    @abstractmethod
    def evaluate(self, ctx: RuleContext) -> RuleHit | None: ...

    def hit(self, detail: str) -> RuleHit:
        return RuleHit(
            code=self.code, name=self.name, severity=self.severity, detail=detail
        )
