from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.companies.models import Company
from src.people.models import DecisionMaker, DecisionMakerRole


class DecisionMakerSearch(ABC):
    """Port for finding decision makers at a company.

    Implementations: Apollo, LinkedIn Sales Navigator.
    """

    source_name: str

    @abstractmethod
    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        ...


class ContactEnrichment(ABC):
    """Port for enriching a DecisionMaker with missing contact info.

    Implementations: Apollo.io, Hunter.io.
    """

    source_name: str

    @abstractmethod
    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        ...
