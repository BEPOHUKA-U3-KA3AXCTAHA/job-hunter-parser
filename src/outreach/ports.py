from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from uuid import UUID

from src.leads.models import Lead
from src.outreach.models import OutreachChannel, OutreachMessage


class LLMGenerator(ABC):
    """Port for LLM-based personalized message generation.

    Implementations: Claude, OpenAI.
    """

    model_name: str

    @abstractmethod
    async def generate_outreach(
        self,
        lead: Lead,
        channel: OutreachChannel,
        candidate_profile_summary: str,
    ) -> OutreachMessage:
        ...


class OutreachLog(ABC):
    """Port for logging outreach attempts and tracking replies."""

    @abstractmethod
    async def log_message(self, message: OutreachMessage) -> None: ...

    @abstractmethod
    async def mark_sent(self, message_id: UUID) -> None: ...

    @abstractmethod
    async def mark_replied(self, message_id: UUID, reply_text: str) -> None: ...

    @abstractmethod
    async def find_by_lead(self, lead_id: UUID) -> AsyncIterator[OutreachMessage]: ...
