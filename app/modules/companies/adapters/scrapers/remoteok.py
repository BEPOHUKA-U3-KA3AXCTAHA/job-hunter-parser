"""RemoteOK scraper - fetches from their public JSON API."""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import httpx
from loguru import logger

from app.modules.companies.services.email_extract import extract_apply_email
from app.modules.companies.models import Company, JobPosting
from app.modules.companies.ports import CompanySource
from app.shared import SearchCriteria, Seniority, TechStack

_API_URL = "https://remoteok.com/api"
_HEADERS = {"User-Agent": "Mozilla/5.0 (job-hunter-parser/0.1)"}


class RemoteOKScraper(CompanySource):
    source_name = "remoteok"

    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]:
        jobs = await self._fetch_jobs()
        seen: dict[str, Company] = {}

        for job in jobs:
            company_name = job.get("company", "").strip()
            if not company_name:
                continue

            tags = [t.lower() for t in job.get("tags", [])]
            title = job.get("position", "")

            if not criteria.matches_title(title):
                continue

            if criteria.tech_stack:
                if not any(t.lower() in tags for t in criteria.tech_stack):
                    continue

            if company_name not in seen:
                seen[company_name] = Company(
                    name=company_name,
                    tech_stack=TechStack(frozenset(tags)),
                    is_hiring=True,
                    source="remoteok",
                    source_url=job.get("url"),
                    location=job.get("location"),
                )
                if len(seen) >= criteria.limit_per_source:
                    break

        logger.info("RemoteOK: found {} companies", len(seen))
        for company in seen.values():
            yield company

    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]:
        jobs = await self._fetch_jobs()
        count = 0

        for job in jobs:
            tags = [t.lower() for t in job.get("tags", [])]
            title = job.get("position", "")

            if not criteria.matches_title(title):
                continue

            if criteria.tech_stack:
                if not any(t.lower() in tags for t in criteria.tech_stack):
                    continue

            salary_min = _parse_salary(job.get("salary_min"))
            if not criteria.matches_salary(salary_min):
                continue

            posted_at = _parse_epoch(job.get("epoch") or job.get("date"))
            if not criteria.matches_competition(None, posted_at):
                continue

            apply_email = extract_apply_email(
                f"{job.get('description','')} {job.get('apply_url','')}",
                job.get("company"),
            )

            yield JobPosting(
                title=title,
                company_name=job.get("company", ""),
                description=job.get("description"),
                tech_stack=TechStack(frozenset(tags)),
                seniority=Seniority.from_text(title),
                is_remote=True,
                location=job.get("location"),
                salary_min=salary_min,
                salary_max=_parse_salary(job.get("salary_max")),
                salary_currency="USD" if salary_min else None,
                source="remoteok",
                source_url=job.get("url"),
                posted_at=posted_at,
                apply_email=apply_email,
            )
            count += 1
            if count >= criteria.limit_per_source:
                break

        logger.info("RemoteOK: yielded {} postings", count)

    async def _fetch_jobs(self) -> list[dict]:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
            resp = await client.get(_API_URL)
            resp.raise_for_status()
            data = resp.json()
        return data[1:] if len(data) > 1 else []


def _parse_salary(val: str | int | None) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_epoch(val) -> datetime | None:
    """RemoteOK gives `epoch` (unix seconds) and ISO `date`. Try epoch first."""
    if val is None or val == "":
        return None
    try:
        return datetime.utcfromtimestamp(int(val))
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
