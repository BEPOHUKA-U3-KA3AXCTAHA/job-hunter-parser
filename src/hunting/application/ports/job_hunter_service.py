from __future__ import annotations

from abc import ABC, abstractmethod

from src.companies.domain.company import Company
from src.leads.domain.lead import Lead
from src.outreach.domain.outreach_message import OutreachChannel


class JobHunterServicePort(ABC):
    """Inbound port - what CLI/API clients can do.

    The main facade that orchestrates all use cases.
    """

    @abstractmethod
    async def scrape_companies(
        self, source: str, tech_stack: list[str], limit: int
    ) -> list[Company]: ...

    @abstractmethod
    async def enrich_companies_with_decision_makers(
        self, companies: list[Company]
    ) -> list[Lead]: ...

    @abstractmethod
    async def generate_outreach_messages(
        self, leads: list[Lead], channel: OutreachChannel
    ) -> int:
        """Returns number of messages generated."""
        ...

    @abstractmethod
    async def export_to_csv(self, output_path: str) -> int:
        """Export all leads ready for outreach. Returns row count."""
        ...
