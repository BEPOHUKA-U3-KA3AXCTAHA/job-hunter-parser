from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.entities.lead import Lead
from src.domain.entities.outreach_message import OutreachChannel, OutreachMessage


class LLMGeneratorPort(ABC):
    """Port for LLM-based personalized message generation.

    Implementations: Claude (Anthropic), OpenAI.
    """

    model_name: str

    @abstractmethod
    async def generate_outreach(
        self,
        lead: Lead,
        channel: OutreachChannel,
        candidate_profile_summary: str,
    ) -> OutreachMessage:
        """Generate a personalized outreach message for the given lead and channel."""
        ...
