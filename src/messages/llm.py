"""Claude (Anthropic) outreach LLM adapter."""
from __future__ import annotations

import anthropic
from loguru import logger

from src.messages.llm_base import SYSTEM_PROMPT, BaseLLMAdapter
from src.messages.models import Message


class ClaudeLLMAdapter(BaseLLMAdapter):
    # Outreach is a templated task — Haiku is enough. Override via constructor for higher quality.
    model_name = "claude-haiku-4-5"

    def __init__(self, api_key: str, model: str | None = None) -> None:
        super().__init__(min_interval_s=0.0)
        self._client = anthropic.Anthropic(api_key=api_key)
        if model:
            self.model_name = model

    async def _call(self, user_prompt: str, message: Message) -> str:
        try:
            response = self._client.messages.create(
                model=self.model_name,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            body = response.content[0].text.strip()
        except Exception as e:
            logger.error("Claude generation failed for {}: {}", message.company.name, e)
            return ""
        logger.info(
            "Claude ({}): generated for {} at {}",
            self.model_name, message.decision_maker.full_name, message.company.name,
        )
        return body
