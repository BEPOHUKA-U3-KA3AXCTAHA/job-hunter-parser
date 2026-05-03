"""web3.career scraper - parses HTML job listings."""
from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.modules.companies.models import Company, JobPosting
from app.modules.companies.ports import CompanySource
from app.modules.companies import SearchCriteria, Seniority, TechStack

_BASE_URL = "https://web3.career"
_HEADERS = {"User-Agent": "Mozilla/5.0 (job-hunter-parser/0.1)"}
_SALARY_RE = re.compile(r"\$(\d+)k\s*-\s*\$(\d+)k")
_AGO_RE = re.compile(r"(\d+)\s*(d|w|mo|m|y|h)", re.IGNORECASE)

_CATEGORY_URLS = {
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

    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]:
        rows = await self._fetch_rows()
        seen: dict[str, Company] = {}

        for row in rows:
            parsed = _parse_row(row)
            if not parsed or not parsed["company"]:
                continue

            name = parsed["company"]
            if name in seen:
                continue

            if not criteria.matches_title(parsed["title"]):
                continue

            if not criteria.matches_salary(parsed["salary_min"]):
                continue

            seen[name] = Company(
                name=name,
                tech_stack=TechStack(frozenset(parsed["tags"])),
                is_hiring=True,
                source="web3.career",
                source_url=f"{_BASE_URL}{parsed['link']}" if parsed["link"] else None,
                location=parsed["location"],
            )
            if len(seen) >= criteria.limit_per_source:
                break

        logger.info("web3.career: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]:
        rows = await self._fetch_rows()
        count = 0

        for row in rows:
            parsed = _parse_row(row)
            if not parsed:
                continue

            if not criteria.matches_title(parsed["title"]):
                continue

            if not criteria.matches_salary(parsed["salary_min"]):
                continue

            if not criteria.matches_competition(None, parsed.get("posted_at")):
                continue

            yield JobPosting(
                title=parsed["title"],
                company_name=parsed["company"] or "",
                tech_stack=TechStack(frozenset(parsed["tags"])),
                seniority=Seniority.from_text(parsed["title"]),
                is_remote="remote" in parsed.get("location", "").lower(),
                location=parsed["location"],
                salary_min=parsed["salary_min"],
                salary_max=parsed["salary_max"],
                salary_currency="USD" if parsed["salary_min"] else None,
                source="web3.career",
                source_url=f"{_BASE_URL}{parsed['link']}" if parsed["link"] else None,
                posted_at=parsed.get("posted_at"),
            )
            count += 1
            if count >= criteria.limit_per_source:
                break

        logger.info("web3.career: yielded {} postings", count)

    async def _fetch_rows(self) -> list:
        """Fetch multiple pages of job listings to get more rows."""
        base = f"{_BASE_URL}{_CATEGORY_URLS.get(self._category, '/remote-jobs')}"
        all_rows: list = []

        async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
            for page in range(1, 11):  # up to 10 pages ~= 200+ rows
                url = base if page == 1 else f"{base}?page={page}"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = soup.select("tr.table_row")
                if not rows:
                    break
                all_rows.extend(rows)

        return all_rows


def _parse_row(row) -> dict | None:
    tds = row.find_all("td")
    if len(tds) < 5:
        return None

    title_el = row.select_one("h2")
    company_el = row.select_one("h3")
    link_el = row.select_one("a[href*='/']")

    title = title_el.text.strip() if title_el else ""
    company = company_el.text.strip() if company_el else tds[1].text.strip()
    posted_text = tds[2].text.strip() if len(tds) > 2 else ""
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

    posted_at = _parse_relative_ago(posted_text)

    return {
        "title": title,
        "company": company,
        "location": location,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "tags": tags,
        "link": link,
        "posted_at": posted_at,
    }


def _parse_relative_ago(text: str) -> datetime | None:
    """Parse '1d', '2w', '3mo', '1y', '4h' → approximate datetime in the past."""
    if not text:
        return None
    m = _AGO_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=n * 30),
        "mo": timedelta(days=n * 30),
        "y": timedelta(days=n * 365),
    }.get(unit)
    if delta is None:
        return None
    return datetime.utcnow() - delta
