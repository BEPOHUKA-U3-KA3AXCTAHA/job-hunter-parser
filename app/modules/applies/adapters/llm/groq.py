"""Groq free-tier outreach adapter (Llama 3.3 70B and friends).

Get a key at https://console.groq.com/keys (free, no card).
llama-3.3-70b-versatile free tier: 30 RPM, 6000 RPD as of 2026.
"""
from __future__ import annotations

import httpx
from loguru import logger

from app.modules.applies.adapters.llm.base import SYSTEM_PROMPT, BaseLLMAdapter
from app.modules.applies.models import Message

_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqLLMAdapter(BaseLLMAdapter):
    model_name = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, model: str | None = None, min_interval_s: float = 2.1) -> None:
        # 2.1s ⇒ ~28.5 RPM, just under the 30 RPM limit
        super().__init__(min_interval_s=min_interval_s)
        self._api_key = api_key
        if model:
            self.model_name = model

    async def _call(self, user_prompt: str, message: Message) -> str:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    _API_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Groq HTTP {} for {}: {}", e.response.status_code, message.company.name, e.response.text[:200])
            return ""
        except Exception as e:
            logger.error("Groq call failed for {}: {}", message.company.name, e)
            return ""

        try:
            body = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Groq returned no text for {}: {}", message.company.name, str(data)[:200])
            return ""

        logger.info(
            "Groq ({}): generated for {} at {}",
            self.model_name, message.decision_maker.full_name, message.company.name,
        )
        return body
