from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.domain.entities.company import Company
from src.domain.entities.decision_maker import DecisionMaker, DecisionMakerRole


class DecisionMakerSearchPort(ABC):
    """Port for finding decision makers at a company.

    Implementations: LinkedIn Sales Navigator, Apollo, custom scrapers.
    """

    source_name: str

    @abstractmethod
    async def find_decision_makers(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        """Find decision makers at a company matching given roles."""
        ...
