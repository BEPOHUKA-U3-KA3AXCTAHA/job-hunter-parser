"""Apollo.io adapter - finds decision makers and enriches contacts.

Free tier: 10,000 credits/month. Each people search = 1 credit.
API docs: https://apolloio.github.io/apollo-api-docs/
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from loguru import logger

from src.companies.models import Company
from src.people.models import DecisionMaker, DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch
from src.shared import Email, LinkedInUrl

_API_BASE = "https://api.apollo.io/v1"

_ROLE_TITLES = {
    DecisionMakerRole.FOUNDER: ["founder", "co-founder"],
    DecisionMakerRole.CEO: ["ceo", "chief executive"],
    DecisionMakerRole.CTO: ["cto", "chief technology", "chief technical"],
    DecisionMakerRole.HEAD_OF_ENGINEERING: ["head of engineering", "vp of engineering", "director of engineering"],
    DecisionMakerRole.VP_ENGINEERING: ["vp engineering", "vice president engineering"],
    DecisionMakerRole.ENGINEERING_MANAGER: ["engineering manager", "eng manager"],
    DecisionMakerRole.TECH_LEAD: ["tech lead", "technical lead", "lead engineer"],
}


class ApolloAdapter(DecisionMakerSearch, ContactEnrichment):
    source_name = "apollo"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        title_keywords = []
        for role in roles:
            title_keywords.extend(_ROLE_TITLES.get(role, []))

        if not title_keywords:
            title_keywords = ["cto", "founder", "head of engineering"]

        domain = _extract_domain(company.website) if company.website else None
        org_name = company.name

        async with httpx.AsyncClient(timeout=30) as client:
            payload = {
                "api_key": self._api_key,
                "q_organization_name": org_name,
                "person_titles": title_keywords,
                "page": 1,
                "per_page": limit,
            }
            if domain:
                payload["q_organization_domains"] = domain

            try:
                resp = await client.post(f"{_API_BASE}/mixed_people/search", json=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("Apollo search failed for {}: {}", company.name, e)
                return

        people = data.get("people", [])
        logger.info("Apollo: found {} people at {}", len(people), company.name)

        for person in people:
            role = _detect_role(person.get("title", ""))
            email_str = person.get("email")
            linkedin_str = person.get("linkedin_url")

            email = None
            if email_str:
                try:
                    email = Email(email_str)
                except ValueError:
                    pass

            linkedin = None
            if linkedin_str:
                try:
                    linkedin = LinkedInUrl(linkedin_str)
                except ValueError:
                    pass

            yield DecisionMaker(
                full_name=f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
                role=role,
                company_id=company.id,
                title_raw=person.get("title"),
                email=email,
                linkedin_url=linkedin,
                twitter_handle=person.get("twitter_url"),
                location=person.get("city"),
            )

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        if decision_maker.email and decision_maker.linkedin_url:
            return decision_maker

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{_API_BASE}/people/match",
                    json={
                        "api_key": self._api_key,
                        "first_name": decision_maker.full_name.split()[0] if decision_maker.full_name else "",
                        "last_name": decision_maker.full_name.split()[-1] if decision_maker.full_name else "",
                        "organization_name": company_domain,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("Apollo enrich failed: {}", e)
                return decision_maker

        person = data.get("person", {})
        if not person:
            return decision_maker

        if not decision_maker.email and person.get("email"):
            try:
                decision_maker.email = Email(person["email"])
            except ValueError:
                pass

        if not decision_maker.linkedin_url and person.get("linkedin_url"):
            try:
                decision_maker.linkedin_url = LinkedInUrl(person["linkedin_url"])
            except ValueError:
                pass

        return decision_maker


def _extract_domain(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").split("/")[0]


def _detect_role(title: str) -> DecisionMakerRole:
    t = title.lower()
    for role, keywords in _ROLE_TITLES.items():
        if any(kw in t for kw in keywords):
            return role
    if "recruit" in t or "talent" in t:
        return DecisionMakerRole.RECRUITER
    if "hr" in t or "human" in t:
        return DecisionMakerRole.HR
    return DecisionMakerRole.OTHER
