import json

import httpx

from app.ai.prompt import SYSTEM_PROMPT, build_user_prompt
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.ai import AIResult

logger = get_logger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.api_base = settings.llm_api_base.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze(self, context: dict) -> AIResult:
        if not self.enabled:
            logger.warning("LLM_API_KEY not set, using heuristic fallback analysis")
            return self._fallback(context)
        try:
            raw = self._call(context)
            return self._parse(raw)
        except Exception as exc:
            logger.exception("LLM call failed, using fallback: %s", exc)
            return self._fallback(context)

    def _call(self, context: dict) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(context)},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    def _parse(self, raw: str) -> AIResult:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        return AIResult.model_validate_json(raw)

    def _fallback(self, context: dict) -> AIResult:
        rules = context.get("triggered_rules", [])
        max_severity = max((r.get("severity", 1) for r in rules), default=1)
        confidence = min(50 + max_severity * 8 + len(rules) * 3, 95)
        if max_severity >= 5 or len(rules) >= 4:
            recommendation, priority, action = (
                "decline",
                "critical",
                "Block card and contact customer",
            )
        elif max_severity >= 3:
            recommendation, priority, action = (
                "investigate",
                "high",
                "Assign to investigator for manual review",
            )
        else:
            recommendation, priority, action = (
                "investigate",
                "medium",
                "Review customer history before deciding",
            )
        reasoning = [r.get("detail", r.get("name", "rule")) for r in rules]
        summary = (
            f"{len(rules)} rule(s) triggered with max severity {max_severity}. "
            "Heuristic assessment (LLM unavailable)."
        )
        return AIResult(
            recommendation=recommendation,
            confidence=confidence,
            priority=priority,
            summary=summary,
            reasoning=reasoning or ["Flagged by fraud rules"],
            next_action=action,
        )


llm_client = LLMClient()
