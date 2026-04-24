"""TheOrg.com adapter - free org charts of companies.

Scrapes public theorg.com/org/{slug} pages, parses JSON-LD structured data.
For each person also fetches their TheOrg profile to extract LinkedIn URL.
Returns C-level decision makers: CEO, CTO, CFO, Founders, VPs, EMs.
No auth, no API key, no rate limits (be polite anyway).
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator

import httpx
from loguru import logger

from src.companies.models import Company
from src.people.models import DecisionMaker, DecisionMakerRole
from src.people.ports import ContactEnrichment, DecisionMakerSearch

_BASE_URL = "https://theorg.com/org"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0",
    "Accept": "text/html,application/xhtml+xml",
}

_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
_LINKEDIN_RE = re.compile(r'(https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+)')
_TWITTER_RE = re.compile(r'(?:twitter|x)\.com/([A-Za-z0-9_]{2,15})(?:[/?"\s]|$)')
_GITHUB_RE = re.compile(r'github\.com/([A-Za-z0-9-]{2,40})(?:[/?"\s]|$)')
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')

_ROLE_KEYWORDS = {
    DecisionMakerRole.CEO: ["ceo", "chief executive"],
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

# Domain noise to filter out from social handle regex
_TWITTER_BLACKLIST = {"theorgcom", "intent", "share", "search", "home"}
_GITHUB_BLACKLIST = {"features", "pricing", "marketplace", "explore", "topics"}


class TheOrgScraper(DecisionMakerSearch, ContactEnrichment):
    source_name = "theorg"

    def __init__(self, request_delay: float = 0.3, fetch_profiles: bool = True) -> None:
        self._delay = request_delay
        self._fetch_profiles = fetch_profiles

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

            persons = _parse_persons(resp.text)
            if not persons:
                logger.info("TheOrg: no people found for {}", company.name)
                return

            wanted_advisors = DecisionMakerRole.OTHER in roles
            emitted = 0

            for person in persons:
                if not wanted_advisors and "advisor" in person["title"].lower():
                    continue

                role = _detect_role(person["title"])
                if roles and role not in roles and DecisionMakerRole.OTHER not in roles:
                    continue

                contacts: dict = {}

                # Fetch personal profile for LinkedIn / Twitter / GitHub
                if self._fetch_profiles and person["profile_url"]:
                    try:
                        profile_resp = await client.get(person["profile_url"])
                        if profile_resp.status_code == 200:
                            contacts = _extract_contacts(profile_resp.text)
                        await asyncio.sleep(self._delay)
                    except httpx.HTTPError as e:
                        logger.debug("Profile fetch failed for {}: {}", person["name"], e)

                yield DecisionMaker(
                    full_name=person["name"],
                    role=role,
                    company_id=company.id,
                    title_raw=person["title"],
                    contacts=contacts,
                )
                emitted += 1
                if emitted >= limit:
                    break

            logger.info("TheOrg: yielded {} people for {}", emitted, company.name)

    async def enrich(self, decision_maker: DecisionMaker, company_domain: str) -> DecisionMaker:
        # TheOrg.find() already enriches via profile pages - nothing to add here
        return decision_maker


def _parse_persons(html: str) -> list[dict]:
    """Extract Person entries from JSON-LD. Returns list of dicts with name, title, profile_url."""
    results: list[dict] = []
    for block in _JSON_LD_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "Organization":
            continue
        for person in data.get("employee", []):
            if person.get("@type") != "Person":
                continue
            name = (person.get("name") or "").strip()
            title = (person.get("jobTitle") or "").strip()
            same_as = person.get("sameAs", [])
            profile_url = same_as[0] if same_as else None
            if name and title:
                results.append({"name": name, "title": title, "profile_url": profile_url})
    return results


def _extract_contacts(html: str) -> dict:
    """Extract LinkedIn / Twitter / GitHub / email URLs from a person profile page."""
    out: dict = {}

    li = _LINKEDIN_RE.search(html)
    if li:
        out["linkedin"] = li.group(1)

    for tw in _TWITTER_RE.findall(html):
        if tw.lower() not in _TWITTER_BLACKLIST:
            out["twitter"] = "@" + tw
            break

    for gh in _GITHUB_RE.findall(html):
        if gh.lower() not in _GITHUB_BLACKLIST:
            out["github"] = gh
            break

    for em in _EMAIL_RE.findall(html):
        if not em.endswith(("png", "jpg", "svg")) and "theorg" not in em:
            out["email"] = em
            break

    return out


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
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
