"""web3.career scraper - parses HTML job listings."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.companies.models import Company, JobPosting
from src.companies.ports import CompanySource
from src.shared import Seniority, TechStack

_BASE_URL = "https://web3.career"
_HEADERS = {"User-Agent": "Mozilla/5.0 (job-hunter-parser/0.1)"}

_SALARY_RE = re.compile(r"\$(\d+)k\s*-\s*\$(\d+)k")

_SEARCH_URLS = {
    "rust": "/rust-jobs",
    "python": "/python-jobs",
    "backend": "/backend-jobs",
    "senior": "/senior-jobs",
    "remote": "/remote-jobs",
}


class Web3CareerScraper(CompanySource):
    source_name = "web3.career"

    def __init__(self, category: str = "rust") -> None:
        self._category = category

    async def fetch_companies(
        self,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Company]:
        rows = await self._fetch_rows()
        seen: dict[str, Company] = {}

        for row in rows:
            parsed = _parse_row(row)
            if not parsed:
                continue

            name = parsed["company"]
            if not name or name in seen:
                continue

            tags = parsed["tags"]
            if tech_stack_filter:
                if not any(t.lower() in tags for t in tech_stack_filter):
                    continue

            seen[name] = Company(
                name=name,
                tech_stack=TechStack(frozenset(tags)),
                is_hiring=True,
                source="web3.career",
                source_url=f"{_BASE_URL}{parsed['link']}" if parsed["link"] else None,
                location=parsed["location"],
            )

            if len(seen) >= limit:
                break

        logger.info("web3.career: found {} companies (category={})", len(seen), self._category)

        for company in seen.values():
            yield company

    async def fetch_job_postings(
        self,
        company_id: str | None = None,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[JobPosting]:
        rows = await self._fetch_rows()
        count = 0

        for row in rows:
            parsed = _parse_row(row)
            if not parsed:
                continue

            tags = parsed["tags"]
            if tech_stack_filter:
                if not any(t.lower() in tags for t in tech_stack_filter):
                    continue

            yield JobPosting(
                company_id=0,
                title=parsed["title"],
                tech_stack=TechStack(frozenset(tags)),
                seniority=Seniority.from_text(parsed["title"]),
                is_remote="remote" in parsed.get("location", "").lower(),
                location=parsed["location"],
                salary_min=parsed["salary_min"],
                salary_max=parsed["salary_max"],
                salary_currency="USD" if parsed["salary_min"] else None,
                source_url=f"{_BASE_URL}{parsed['link']}" if parsed["link"] else None,
            )

            count += 1
            if count >= limit:
                break

        logger.info("web3.career: yielded {} job postings", count)

    async def _fetch_rows(self) -> list:
        url = f"{_BASE_URL}{_SEARCH_URLS.get(self._category, '/remote-jobs')}"
        async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.select("tr.table_row")


def _parse_row(row) -> dict | None:
    tds = row.find_all("td")
    if len(tds) < 5:
        return None

    title_el = row.select_one("h2")
    company_el = row.select_one("h3")
    link_el = row.select_one("a[href*='/']")

    title = title_el.text.strip() if title_el else ""
    company = company_el.text.strip() if company_el else tds[1].text.strip()
    location = tds[3].text.strip() if len(tds) > 3 else ""
    salary_text = tds[4].text.strip() if len(tds) > 4 else ""
    tags_text = tds[5].text.strip() if len(tds) > 5 else ""
    link = link_el["href"] if link_el else None

    tags = [t.strip().lower() for t in tags_text.split() if t.strip()]

    salary_min, salary_max = None, None
    m = _SALARY_RE.search(salary_text)
    if m:
        salary_min = int(m.group(1)) * 1000
        salary_max = int(m.group(2)) * 1000

    return {
        "title": title,
        "company": company,
        "location": location,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "tags": tags,
        "link": link,
    }
