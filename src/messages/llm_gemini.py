"""Gemini (Google AI Studio) free-tier outreach adapter.

Get a key at https://aistudio.google.com/apikey (free, no card).
gemini-1.5-flash free tier: 15 RPM, 1500 RPD as of 2026.
"""
from __future__ import annotations

import httpx
from loguru import logger

from src.messages.llm_base import SYSTEM_PROMPT, BaseLLMAdapter
from src.messages.models import Message

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiLLMAdapter(BaseLLMAdapter):
    model_name = "gemini-1.5-flash"

    def __init__(self, api_key: str, model: str | None = None, min_interval_s: float = 4.1) -> None:
        # 4.1s ⇒ ~14.6 RPM, just under the 15 RPM limit
        super().__init__(min_interval_s=min_interval_s)
        self._api_key = api_key
        if model:
            self.model_name = model

    async def _call(self, user_prompt: str, message: Message) -> str:
        url = f"{_BASE_URL}/{self.model_name}:generateContent?key={self._api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7},
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Gemini HTTP {} for {}: {}", e.response.status_code, message.company.name, e.response.text[:200])
            return ""
        except Exception as e:
            logger.error("Gemini call failed for {}: {}", message.company.name, e)
            return ""

        try:
            body = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Gemini returned no text for {}: {}", message.company.name, str(data)[:200])
            return ""

        logger.info(
            "Gemini ({}): generated for {} at {}",
            self.model_name, message.decision_maker.full_name, message.company.name,
        )
        return body
