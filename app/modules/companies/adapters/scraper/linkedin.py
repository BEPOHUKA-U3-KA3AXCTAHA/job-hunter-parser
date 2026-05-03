"""LinkedIn public job search scraper - no auth required."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.modules.companies.models import Company, JobPosting
from app.modules.companies.ports import CompanySource
from app.modules.companies import SearchCriteria, Seniority

_BASE_URL = "https://www.linkedin.com/jobs/search/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_APPLICANT_NUM_RE = re.compile(r"(\d+)\s*applicants?", re.IGNORECASE)


class LinkedInScraper(CompanySource):
    source_name = "linkedin"

    def __init__(self, location: str = "Remote") -> None:
        self._location = location

    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]:
        postings = await self._fetch_all(criteria)
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
            if len(seen) >= criteria.limit_per_source:
                break

        logger.info("LinkedIn: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]:
        postings = await self._fetch_all(criteria)
        emitted = 0

        for p in postings:
            if not criteria.matches_title(p["title"]):
                continue
            if not criteria.matches_competition(p.get("applicants_count"), p.get("posted_at")):
                continue

            yield JobPosting(
                title=p["title"],
                company_name=p.get("company", ""),
                seniority=Seniority.from_text(p["title"]),
                is_remote="remote" in p.get("location", "").lower(),
                location=p["location"],
                source="linkedin",
                source_url=p["link"],
                applicants_count=p.get("applicants_count"),
                posted_at=p.get("posted_at"),
            )
            emitted += 1

        logger.info("LinkedIn: yielded {} postings (after competition filter, raw {})", emitted, len(postings))

    async def _fetch_all(self, criteria: SearchCriteria) -> list[dict]:
        keywords = " ".join(criteria.tech_stack + criteria.roles)
        keywords_q = quote_plus(f"{keywords} {self._location}")

        all_postings: list[dict] = []
        pages = min((criteria.limit_per_source // 25) + 1, 10)  # up to 10 pages = 250 rows

        async with httpx.AsyncClient(headers=_HEADERS, timeout=30, follow_redirects=True) as client:
            for page in range(pages):
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

                if len(all_postings) >= criteria.limit_per_source or len(parsed) < 10:
                    break
                await asyncio.sleep(2)

        return all_postings[:criteria.limit_per_source]


def _parse_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for card in soup.select(".base-card"):
        title_el = card.select_one(".base-search-card__title")
        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.select_one("a.base-card__full-link")
        date_el = card.select_one("time.job-search-card__listdate, time.job-search-card__listdate--new")
        benefit_el = card.select_one(".job-posting-benefits__text")

        if not title_el:
            continue

        posted_at = None
        if date_el and date_el.get("datetime"):
            try:
                posted_at = datetime.fromisoformat(date_el["datetime"])
            except ValueError:
                pass

        # Competition signal: LinkedIn shows either "Be an early applicant" (≈low),
        # "Be among the first 25 applicants" (=25), or N applicants.
        # Absent means not shown — usually saturated.
        applicants_count = None
        if benefit_el:
            txt = benefit_el.get_text(strip=True).lower()
            m = _APPLICANT_NUM_RE.search(txt)
            if m:
                applicants_count = int(m.group(1))
            elif "early applicant" in txt:
                applicants_count = 10  # heuristic: posted recently, very few apps

        results.append({
            "title": title_el.text.strip(),
            "company": company_el.text.strip() if company_el else "",
            "location": location_el.text.strip() if location_el else "",
            "link": link_el["href"].split("?")[0] if link_el else None,
            "posted_at": posted_at,
            "applicants_count": applicants_count,
        })

    return results
