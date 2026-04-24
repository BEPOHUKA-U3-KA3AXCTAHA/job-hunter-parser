"""Claude LLM adapter - generates personalized outreach messages."""
from __future__ import annotations

import anthropic
from loguru import logger

from src.messages.models import Message
from src.messages.ports import LLMGenerator

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


class ClaudeLLMAdapter(LLMGenerator):
    model_name = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)

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
        try:
            response = self._client.messages.create(
                model=self.model_name,
                max_tokens=500,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            body = response.content[0].text.strip()
        except Exception as e:
            logger.error("Claude generation failed for {}: {}", company.name, e)
            return ""

        logger.info("Generated {} message for {} at {}", channel, dm.full_name, company.name)
        return body
