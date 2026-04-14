from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from uuid import UUID

from src.domain.entities.lead import Lead, LeadStatus


class LeadRepositoryPort(ABC):
    """Port for persisting and querying leads."""

    @abstractmethod
    async def save(self, lead: Lead) -> None: ...

    @abstractmethod
    async def save_many(self, leads: list[Lead]) -> None: ...

    @abstractmethod
    async def get_by_id(self, lead_id: UUID) -> Lead | None: ...

    @abstractmethod
    async def find_by_status(self, status: LeadStatus) -> AsyncIterator[Lead]: ...

    @abstractmethod
    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Lead]: ...

    @abstractmethod
    async def update_status(self, lead_id: UUID, status: LeadStatus) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...
