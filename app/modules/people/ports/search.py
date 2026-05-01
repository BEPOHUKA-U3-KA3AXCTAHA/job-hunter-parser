"""Driven ports for the people domain."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.modules.companies import Company
from app.modules.people.models import DecisionMaker, DecisionMakerRole


@runtime_checkable
class DecisionMakerSearch(Protocol):
    """Find decision makers at a company.

    Implementations: apollo, theorg, apify (LinkedIn proxy).
    """

    source_name: str

    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]: ...


@runtime_checkable
class ContactEnrichment(Protocol):
    """Enrich a DecisionMaker with missing contact info (email, linkedin URL).

    Implementations: apollo, hunter, email_guesser.
    """

    source_name: str

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker: ...
