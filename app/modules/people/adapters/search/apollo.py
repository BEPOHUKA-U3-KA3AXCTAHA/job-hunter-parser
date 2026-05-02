"""Apollo.io adapter - finds decision makers and enriches contacts.

Free tier: 75 credits/month. Each people search = 1 credit.
API docs: https://docs.apollo.io/reference/people-api-search
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from loguru import logger

from app.modules.companies import Company
from app.modules.people.models import DecisionMaker, DecisionMakerRole
from app.modules.people.ports import ContactEnrichment, DecisionMakerSearch

_API_BASE = "https://api.apollo.io/api/v1"

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
    """Apollo.io. Free tier returns 403 on the people-search/match endpoints,
    so once we see one 403 we stop trying for the rest of the run — otherwise
    every call generates noise in the log.
    """
    source_name = "apollo"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }
        self._disabled_reason: str | None = None

    def _disable(self, reason: str) -> None:
        if self._disabled_reason is None:
            self._disabled_reason = reason
            logger.warning(
                "Apollo disabled for the rest of this run ({}). Likely free-tier "
                "blocks API access; subsequent calls will silently no-op.",
                reason,
            )

    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        if self._disabled_reason:
            return
        title_keywords = []
        for role in roles:
            title_keywords.extend(_ROLE_TITLES.get(role, []))

        if not title_keywords:
            title_keywords = ["cto", "founder", "head of engineering"]

        async with httpx.AsyncClient(timeout=30) as client:
            params = {
                "person_titles[]": title_keywords,
                "q_organization_name": company.name,
                "per_page": limit,
                "page": 1,
            }

            try:
                resp = await client.post(
                    f"{_API_BASE}/mixed_people/search",
                    headers=self._headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 402, 403):
                    self._disable(f"HTTP {e.response.status_code} on search")
                else:
                    logger.warning("Apollo search failed for {}: {}", company.name, e)
                return
            except httpx.HTTPError as e:
                logger.warning("Apollo search failed for {}: {}", company.name, e)
                return

        people = data.get("people", [])
        logger.info("Apollo: found {} people at {}", len(people), company.name)

        for person in people:
            role = _detect_role(person.get("title", ""))
            contacts: dict = {}
            if person.get("email"):
                contacts["email"] = person["email"]
            if person.get("linkedin_url"):
                contacts["linkedin"] = person["linkedin_url"]
            if person.get("twitter_url"):
                contacts["twitter"] = person["twitter_url"]
            if person.get("github_url"):
                contacts["github"] = person["github_url"]

            yield DecisionMaker(
                full_name=f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
                role=role,
                company_id=company.id,
                title_raw=person.get("title"),
                location=person.get("city"),
                contacts=contacts,
            )

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        if self._disabled_reason:
            return decision_maker
        if decision_maker.email and decision_maker.linkedin_url:
            return decision_maker

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{_API_BASE}/people/match",
                    headers=self._headers,
                    json={
                        "first_name": decision_maker.full_name.split()[0] if decision_maker.full_name else "",
                        "last_name": decision_maker.full_name.split()[-1] if decision_maker.full_name else "",
                        "organization_name": company_domain,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 402, 403):
                    self._disable(f"HTTP {e.response.status_code} on match")
                else:
                    logger.warning("Apollo enrich failed: {}", e)
                return decision_maker
            except httpx.HTTPError as e:
                logger.warning("Apollo enrich failed: {}", e)
                return decision_maker

        person = data.get("person", {})
        if not person:
            return decision_maker

        if "email" not in decision_maker.contacts and person.get("email"):
            decision_maker.contacts["email"] = person["email"]
        if "linkedin" not in decision_maker.contacts and person.get("linkedin_url"):
            decision_maker.contacts["linkedin"] = person["linkedin_url"]
        if "twitter" not in decision_maker.contacts and person.get("twitter_url"):
            decision_maker.contacts["twitter"] = person["twitter_url"]
        if "github" not in decision_maker.contacts and person.get("github_url"):
            decision_maker.contacts["github"] = person["github_url"]

        return decision_maker


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
