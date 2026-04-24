"""Ports for persisting messages and generating their body via LLM."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from uuid import UUID

from src.messages.models import Message, MessageChannel, MessageStatus
from src.people.models import DecisionMaker


class MessageRepository(ABC):
    """Port for persisting and querying Messages."""

    @abstractmethod
    async def save(self, message: Message) -> None: ...

    @abstractmethod
    async def save_many(self, messages: list[Message]) -> None: ...

    @abstractmethod
    async def get_by_id(self, message_id: UUID) -> Message | None: ...

    @abstractmethod
    async def find_by_status(self, status: MessageStatus) -> AsyncIterator[Message]: ...

    @abstractmethod
    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Message]: ...

    @abstractmethod
    async def update_status(self, message_id: UUID, status: MessageStatus) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def get_fresh_contacts(
        self, company_name: str, max_age_days: int
    ) -> list[DecisionMaker] | None:
        """Return cached contacts if last_verified_at is within N days. None if stale/missing."""
        ...

    @abstractmethod
    async def save_job_postings(self, postings: list, company_name_to_id: dict) -> int:
        """Persist job postings. Dedup by source_url. Returns new count."""
        ...


class LLMGenerator(ABC):
    """Port for LLM-based personalized message body generation."""

    model_name: str

    @abstractmethod
    async def generate_body(
        self,
        message: Message,
        candidate_profile_summary: str,
    ) -> str:
        """Generate message body for the given outreach attempt. Returns plain text."""
        ...
