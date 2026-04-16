"""Claude LLM adapter - generates personalized outreach messages."""
from __future__ import annotations

import anthropic
from loguru import logger

from src.leads.models import Lead
from src.outreach.models import OutreachChannel, OutreachMessage
from src.outreach.ports import LLMGenerator
from src.shared import CandidateProfile

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

    async def generate_outreach(
        self,
        lead: Lead,
        channel: OutreachChannel,
        candidate_profile_summary: str,
    ) -> OutreachMessage:
        company = lead.company
        dm = lead.decision_maker

        user_prompt = f"""\
Generate a {channel.value} outreach message.

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
            body = f"[LLM ERROR: {e}]"

        subject = None
        if channel == OutreachChannel.EMAIL and body.startswith("Subject:"):
            lines = body.split("\n", 1)
            subject = lines[0].replace("Subject:", "").strip()
            body = lines[1].strip() if len(lines) > 1 else body

        logger.info("Generated {} message for {} at {}", channel, dm.full_name, company.name)

        return OutreachMessage(
            lead_id=lead.id,
            channel=channel,
            subject=subject,
            body=body,
        )
