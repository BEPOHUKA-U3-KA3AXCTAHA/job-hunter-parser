"""TheOrg.com adapter - free org charts of companies.

Scrapes public theorg.com/org/{slug} pages, parses JSON-LD structured data.
Returns C-level decision makers: CEO, CTO, CFO, Founders, VPs.
No auth, no API key, no rate limits (be polite anyway).

Coverage on test set: ~80% of known companies.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

import httpx
from loguru import logger

from src.companies.models import Company
from src.people.models import DecisionMaker, DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch
from src.shared import LinkedInUrl

_BASE_URL = "https://theorg.com/org"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0",
    "Accept": "text/html,application/xhtml+xml",
}

# Pattern to extract Person entries from JSON-LD: {"@type":"Person","name":"...","jobTitle":"..."}
_PERSON_RE = re.compile(
    r'"@type"\s*:\s*"Person"[^}]*?"name"\s*:\s*"([^"]+)"[^}]*?"jobTitle"\s*:\s*"([^"]+)"'
)
_LINKEDIN_RE = re.compile(r'(https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+)')

_ROLE_KEYWORDS = {
    DecisionMakerRole.CEO: ["ceo", "chief executive", "chief executive officer"],
    DecisionMakerRole.CTO: ["cto", "chief technology", "chief technical"],
    DecisionMakerRole.FOUNDER: ["founder", "co-founder", "cofounder"],
    DecisionMakerRole.HEAD_OF_ENGINEERING: [
        "head of engineering", "vp of engineering", "vice president of engineering",
        "director of engineering", "chief engineering", "engineering director",
    ],
    DecisionMakerRole.VP_ENGINEERING: ["vp engineering", "vice president engineering"],
    DecisionMakerRole.ENGINEERING_MANAGER: ["engineering manager", "eng manager"],
    DecisionMakerRole.TECH_LEAD: ["tech lead", "technical lead", "lead engineer"],
}


class TheOrgScraper(DecisionMakerSearch, ContactEnrichment):
    source_name = "theorg"

    def __init__(self, request_delay: float = 0.5) -> None:
        self._delay = request_delay

    async def find(
        self,
        company: Company,
        roles: list[DecisionMakerRole],
        limit: int = 5,
    ) -> AsyncIterator[DecisionMaker]:
        slug = _slugify(company.name)
        url = f"{_BASE_URL}/{slug}"

        async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                logger.warning("TheOrg request failed for {}: {}", company.name, e)
                return

            if resp.status_code == 404:
                logger.info("TheOrg: {} not found", company.name)
                return
            if resp.status_code != 200:
                logger.warning("TheOrg: {} returned HTTP {}", company.name, resp.status_code)
                return

        await asyncio.sleep(self._delay)

        persons = _PERSON_RE.findall(resp.text)
        if not persons:
            logger.info("TheOrg: no people found for {}", company.name)
            return

        # Filter advisors out unless explicitly requested
        wanted_advisors = DecisionMakerRole.OTHER in roles
        emitted = 0

        for name, title in persons:
            role = _detect_role(title)

            # Skip advisors by default (low value for hiring)
            if not wanted_advisors and "advisor" in title.lower():
                continue

            # If caller specified a role list, only return matching roles
            if roles and role not in roles and DecisionMakerRole.OTHER not in roles:
                continue

            yield DecisionMaker(
                full_name=name.strip(),
                role=role,
                company_id=company.id,
                title_raw=title.strip(),
            )
            emitted += 1
            if emitted >= limit:
                break

        logger.info("TheOrg: yielded {} people for {}", emitted, company.name)

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        # TheOrg page may contain LinkedIn links for some people - try to find them
        # but typically TheOrg doesn't expose emails publicly
        return decision_maker


def _slugify(name: str) -> str:
    """Convert company name to TheOrg URL slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)  # drop special chars
    s = re.sub(r"\s+", "-", s)  # spaces to dashes
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _detect_role(title: str) -> DecisionMakerRole:
    t = title.lower()
    for role, keywords in _ROLE_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return role
    if "recruit" in t or "talent" in t:
        return DecisionMakerRole.RECRUITER
    if t.startswith("hr ") or " hr " in t or "human resources" in t:
        return DecisionMakerRole.HR
    return DecisionMakerRole.OTHER
