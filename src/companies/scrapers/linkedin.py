"""LinkedIn public job search scraper - no auth required.

Uses LinkedIn's public job search page which returns server-rendered HTML.
No login needed, but rate-limited - don't hammer it.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.companies.models import Company, JobPosting
from src.companies.ports import CompanySource
from src.shared import Seniority, TechStack

_BASE_URL = "https://www.linkedin.com/jobs/search/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class LinkedInScraper(CompanySource):
    source_name = "linkedin"

    def __init__(self, location: str = "Remote") -> None:
        self._location = location

    async def fetch_companies(
        self,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Company]:
        postings = await self._fetch_all_postings(tech_stack_filter, limit * 2)
        seen: dict[str, Company] = {}

        for p in postings:
            name = p["company"]
            if not name or name in seen:
                continue

            seen[name] = Company(
                name=name,
                is_hiring=True,
                source="linkedin",
                source_url=p["link"],
                location=p["location"],
            )
            if len(seen) >= limit:
                break

        logger.info("LinkedIn: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(
        self,
        company_id: str | None = None,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[JobPosting]:
        postings = await self._fetch_all_postings(tech_stack_filter, limit)

        for p in postings:
            yield JobPosting(
                company_id=0,
                title=p["title"],
                seniority=Seniority.from_text(p["title"]),
                is_remote="remote" in p.get("location", "").lower(),
                location=p["location"],
                source_url=p["link"],
            )

        logger.info("LinkedIn: yielded {} postings", len(postings))

    async def _fetch_all_postings(
        self,
        tech_stack_filter: list[str] | None,
        limit: int,
    ) -> list[dict]:
        keywords = " ".join(tech_stack_filter) if tech_stack_filter else "python backend"
        keywords_q = quote_plus(f"{keywords} {self._location}")

        all_postings: list[dict] = []
        pages_needed = min((limit // 25) + 1, 4)  # max 4 pages to avoid rate limiting

        async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
            for page in range(pages_needed):
                start = page * 25
                url = f"{_BASE_URL}?keywords={keywords_q}&position=1&pageNum={page}&start={start}"

                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    logger.warning("LinkedIn page {} failed: {}", page, e)
                    break

                parsed = _parse_page(resp.text)
                all_postings.extend(parsed)

                if len(all_postings) >= limit or len(parsed) < 10:
                    break

                await asyncio.sleep(2)  # be polite

        return all_postings[:limit]


def _parse_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".base-card")
    results = []

    for card in cards:
        title_el = card.select_one(".base-search-card__title")
        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.select_one("a.base-card__full-link")

        if not title_el:
            continue

        results.append({
            "title": title_el.text.strip(),
            "company": company_el.text.strip() if company_el else "",
            "location": location_el.text.strip() if location_el else "",
            "link": link_el["href"].split("?")[0] if link_el else None,
        })

    return results
