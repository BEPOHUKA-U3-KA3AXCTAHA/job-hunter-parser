"""Gemini LLM adapter — free tier on Google AI Studio.

Get a key at https://aistudio.google.com/apikey (free, no card).
Limits on gemini-1.5-flash free tier as of 2026: 15 RPM, 1M TPM, 1500 RPD.
For 500 outreach messages this fits in one day with room to spare.

We hit the REST API directly via httpx so we don't add a heavy SDK.
"""
from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from src.messages.models import Message
from src.messages.ports import LLMGenerator

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_SYSTEM_PROMPT = """\
You are a professional job outreach assistant. Generate a short, personalized message
from a job candidate to a decision maker at a company. The message should:

- Be concise (4-6 sentences max)
- Reference something specific about the company or role
- Highlight 1-2 relevant candidate achievements
- End with a clear call to action
- Sound human, not robotic
- Use dashes (-) not em-dashes
- No emojis unless the channel is twitter

Adapt tone to the channel:
- linkedin: professional but warm
- email: slightly more formal, include subject line on first line as "Subject: ..."
- twitter: casual, shorter (2-3 sentences)
- telegram: casual, concise
- discord: casual, can be a bit technical
"""


class GeminiLLMAdapter(LLMGenerator):
    """Free Gemini Flash. Self-throttles to stay under 15 RPM by sleeping ~4.1s between calls."""

    model_name = "gemini-1.5-flash"

    def __init__(self, api_key: str, model: str | None = None, min_interval_s: float = 4.1) -> None:
        self._api_key = api_key
        self.model_name = model or self.model_name
        self._min_interval = min_interval_s
        self._last_call_at: float = 0.0
        self._lock = asyncio.Lock()

    async def generate_body(self, message: Message, candidate_profile_summary: str) -> str:
        company = message.company
        dm = message.decision_maker
        channel = message.channel.value if message.channel else "linkedin"

        user_prompt = f"""\
Generate a {channel} outreach message.

CANDIDATE:
{candidate_profile_summary}

TARGET:
- Company: {company.name}
- Person: {dm.full_name} ({dm.title_raw or dm.role.value})
- Company tech: {', '.join(company.tech_stack.technologies) if company.tech_stack.technologies else 'unknown'}
- Company location: {company.location or 'unknown'}
- Job source: {company.source or 'unknown'}

Write the message now. Just the message text, nothing else.
"""

        url = f"{_BASE_URL}/{self.model_name}:generateContent?key={self._api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7},
        }

        await self._throttle()

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Gemini HTTP {} for {}: {}", e.response.status_code, company.name, e.response.text[:200])
            return ""
        except Exception as e:
            logger.error("Gemini call failed for {}: {}", company.name, e)
            return ""

        try:
            body = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Gemini returned no text for {}: {}", company.name, str(data)[:200])
            return ""

        logger.info("Gemini ({}): generated {} for {} at {}", self.model_name, channel, dm.full_name, company.name)
        return body

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_at = asyncio.get_event_loop().time()
