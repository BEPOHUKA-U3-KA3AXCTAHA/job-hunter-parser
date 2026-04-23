"""Apify adapter - paid LinkedIn scraping via Apify Actors.

STUB: not implemented yet. Plan:
1. Use Apify "LinkedIn Company People Scraper" Actor.
2. Pass company LinkedIn URL or name -> get all employees with titles.
3. Filter by Engineering Manager / Tech Lead / Head of Engineering.

Pricing: ~$5 for 1000 results pay-as-you-go, or $49/mo unlimited.
Docs: https://apify.com/apify/linkedin-company-people-scraper

Activates when APIFY_API_KEY env var is set.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from loguru import logger

from src.companies.models import Company
from src.people.models import DecisionMaker, DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch


class ApifyAdapter(DecisionMakerSearch, ContactEnrichment):
    source_name = "apify"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        logger.warning("Apify adapter is a STUB - not implemented yet")

    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        # TODO: implement when ready to spend $5
        # POST https://api.apify.com/v2/acts/apify~linkedin-company-people-scraper/runs
        #   ?token={api_key}
        #   body: {"companyUrls": [...], "maxResults": limit}
        # Then poll the run, get dataset items.
        logger.info("Apify: STUB called for {} - returning empty", company.name)
        return
        yield  # makes function an async generator

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        return decision_maker
