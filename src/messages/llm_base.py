"""Shared bits for LLM outreach adapters: system prompt, user-prompt builder,
and a tiny rate-limit helper. Provider-specific code only does the HTTP call.

Lifted out of llm.py / llm_gemini.py / llm_groq.py which were 90% duplicated.
"""
from __future__ import annotations

import asyncio
from abc import abstractmethod

from src.messages.models import Message
from src.messages.ports import LLMGenerator

SYSTEM_PROMPT = """\
You are writing a job outreach message from a candidate to a decision maker.

Hard rules:
- 4-6 sentences max
- Reference one concrete thing about the company or role
- Mention 1-2 candidate achievements but don't oversell
- Slightly self-deprecating beats braggy ("not sure I clear the bar but" > "I'm the perfect fit")
- Sound like a human wrote it
- Use plain dashes (-), never em-dashes
- No emojis except on twitter
- Always say where you found the posting
- End with a soft, no-pressure call to action

Channel-specific tone:
- linkedin: professional but warm
- email: slightly more formal, first line "Subject: ..."
- twitter: casual, 2-3 sentences max
- telegram / discord: casual

Output: just the message text. No preamble, no markdown headers.
"""


def build_user_prompt(message: Message, candidate_profile_summary: str) -> str:
    company = message.company
    dm = message.decision_maker
    jp = message.job_posting
    channel = message.channel.value if message.channel else "linkedin"

    job_block = ""
    if jp:
        tags = ", ".join(jp.tech_stack.technologies) if jp.tech_stack.technologies else "unknown"
        job_block = (
            "\nSPECIFIC JOB OPENING (mention it directly in the message):\n"
            f"- Title: {jp.title}\n"
            f"- Tech tags: {tags}\n"
            f"- Seniority: {jp.seniority.value if jp.seniority else 'unknown'}\n"
            f"- Posted: {jp.posted_at.strftime('%Y-%m-%d') if jp.posted_at else 'unknown'}\n"
            f"- URL: {jp.source_url or '(no link)'}\n"
        )

    company_tech = (
        ", ".join(company.tech_stack.technologies)
        if company.tech_stack.technologies else "unknown"
    )
    return (
        f"Generate a {channel} outreach message.\n\n"
        f"CANDIDATE:\n{candidate_profile_summary}\n\n"
        f"TARGET:\n"
        f"- Company: {company.name}\n"
        f"- Person: {dm.full_name} ({dm.title_raw or dm.role.value})\n"
        f"- Company tech: {company_tech}\n"
        f"- Company location: {company.location or 'unknown'}\n"
        f"- Job source: {company.source or 'unknown'}\n"
        f"{job_block}\n"
        "Write the message now. Just the message text, nothing else."
    )


class BaseLLMAdapter(LLMGenerator):
    """Common skeleton: throttle + prompt building. Subclass implements `_call`."""

    model_name: str = "set-me"

    def __init__(self, min_interval_s: float = 0.0) -> None:
        self._min_interval = min_interval_s
        self._last_call_at: float = 0.0
        self._lock = asyncio.Lock()

    async def generate_body(self, message: Message, candidate_profile_summary: str) -> str:
        prompt = build_user_prompt(message, candidate_profile_summary)
        await self._throttle()
        return await self._call(prompt, message)

    @abstractmethod
    async def _call(self, user_prompt: str, message: Message) -> str:
        """Provider HTTP call. Returns the message body or "" on failure."""
        ...

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_at = loop.time()
