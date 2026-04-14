"""Orchestration: pulls together scrape → enrich → score → generate → persist."""
from __future__ import annotations

from loguru import logger

from src.companies.models import Company
from src.companies.ports import CompanySource
from src.leads.models import Lead, LeadStatus
from src.leads.ports import LeadRepository
from src.leads.scorer import LeadScorer
from src.outreach.models import OutreachChannel
from src.outreach.ports import LLMGenerator
from src.people.models import DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch


DEFAULT_TARGET_ROLES = [
    DecisionMakerRole.FOUNDER,
    DecisionMakerRole.CEO,
    DecisionMakerRole.CTO,
    DecisionMakerRole.HEAD_OF_ENGINEERING,
    DecisionMakerRole.ENGINEERING_MANAGER,
]


class Pipeline:
    """Main orchestrator - wires adapters together into a flow."""

    def __init__(
        self,
        source: CompanySource,
        dm_search: DecisionMakerSearch,
        enrichment: ContactEnrichment,
        scorer: LeadScorer,
        llm: LLMGenerator,
        repo: LeadRepository,
    ) -> None:
        self.source = source
        self.dm_search = dm_search
        self.enrichment = enrichment
        self.scorer = scorer
        self.llm = llm
        self.repo = repo

    async def scrape(self, tech: list[str], limit: int) -> list[Company]:
        logger.info("Scraping companies from {}", self.source.source_name)
        companies: list[Company] = []
        async for c in self.source.fetch_companies(tech_stack_filter=tech, limit=limit):
            companies.append(c)
        logger.info("Got {} companies", len(companies))
        return companies

    async def build_leads(self, companies: list[Company]) -> list[Lead]:
        leads: list[Lead] = []
        for company in companies:
            async for dm in self.dm_search.find(company, DEFAULT_TARGET_ROLES, limit=3):
                if company.website:
                    domain = company.website.replace("https://", "").replace("http://", "").split("/")[0]
                    dm = await self.enrichment.enrich(dm, domain)

                score = self.scorer.score(company, dm)
                lead = Lead(company=company, decision_maker=dm, relevance_score=score)
                if lead.is_worth_outreach():
                    lead.advance_status(LeadStatus.ENRICHED)
                    leads.append(lead)

        await self.repo.save_many(leads)
        logger.info("Built {} worth-outreach leads", len(leads))
        return leads

    async def generate_messages(
        self,
        channel: OutreachChannel,
        candidate_summary: str,
        min_score: int = 60,
    ) -> int:
        count = 0
        async for lead in self.repo.find_worth_outreach(min_score=min_score):
            await self.llm.generate_outreach(lead, channel, candidate_summary)
            lead.advance_status(LeadStatus.MESSAGE_GENERATED)
            await self.repo.save(lead)
            count += 1
        logger.info("Generated {} messages for {}", count, channel)
        return count
