"""Groq LLM adapter — free tier with Llama 3.3 70B and Mixtral.

Get a key at https://console.groq.com/keys (free, no card).
Limits as of 2026 on free tier: 30 RPM, 6000 RPD on llama-3.3-70b-versatile.
Inference speed: ~500 tok/s — outreach messages render in <1s each.

OpenAI-compatible API; we hit it via httpx.
"""
from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from src.messages.models import Message
from src.messages.ports import LLMGenerator

_API_URL = "https://api.groq.com/openai/v1/chat/completions"

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

Output the message text only — no preamble, no explanation.
"""


class GroqLLMAdapter(LLMGenerator):
    model_name = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, model: str | None = None, min_interval_s: float = 2.1) -> None:
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

        await self._throttle()

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    _API_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Groq HTTP {} for {}: {}", e.response.status_code, company.name, e.response.text[:200])
            return ""
        except Exception as e:
            logger.error("Groq call failed for {}: {}", company.name, e)
            return ""

        try:
            body = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            logger.warning("Groq returned no text for {}: {}", company.name, str(data)[:200])
            return ""

        logger.info("Groq ({}): generated {} for {} at {}", self.model_name, channel, dm.full_name, company.name)
        return body

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_at = asyncio.get_event_loop().time()
