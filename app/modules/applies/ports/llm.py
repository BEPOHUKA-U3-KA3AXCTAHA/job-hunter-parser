"""LLMGenerator port — produce a personalized message body for an Apply."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.applies.models import Apply


@runtime_checkable
class LLMGenerator(Protocol):
    """Generate the body of an outreach message via LLM.

    Implementations: anthropic (Claude), gemini (Google), groq (fast OSS models).
    """

    model_name: str

    async def generate_body(
        self,
        apply: Apply,
        candidate_profile_summary: str,
    ) -> str: ...
