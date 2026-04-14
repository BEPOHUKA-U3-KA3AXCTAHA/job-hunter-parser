from __future__ import annotations

from abc import ABC, abstractmethod

from src.people.domain.decision_maker import DecisionMaker


class ContactEnrichmentPort(ABC):
    """Port for enriching a DecisionMaker with missing contact info.

    Implementations: Apollo.io, Hunter.io, etc.
    """

    source_name: str

    @abstractmethod
    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        """Return enriched copy of decision maker with email/linkedin filled if found."""
        ...
