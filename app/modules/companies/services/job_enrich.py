"""Enrich existing job_postings rows by fetching their source_url and looking
for an apply-to email on the detail page.

Cheap and on-demand: only run this for jobs you actually plan to act on
(curated set), not the entire scraped corpus. Uses httpx async with bounded
parallelism so we don't hammer any single host.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from loguru import logger
from sqlalchemy import select

from app.infra.db import get_session_maker
from app.infra.db.orm.companies import CompanyRow, JobPostingRow
from app.modules.companies.services.email_extract import extract_apply_email

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_CONCURRENCY = 4
_TIMEOUT = 20


async def enrich_jobs_by_id(job_ids: list[UUID]) -> dict:
    """Fetch detail pages for the given jobs, extract apply_email, persist.

    Returns {hit, miss, http_err, no_url, already_set}.
    """
    Session = get_session_maker()
    async with Session() as session:
        result = await session.execute(
            select(JobPostingRow, CompanyRow.name)
            .join(CompanyRow, CompanyRow.id == JobPostingRow.company_id)
            .where(JobPostingRow.id.in_(job_ids))
        )
        rows = result.all()

    sem = asyncio.Semaphore(_CONCURRENCY)
    stats = {"hit": 0, "miss": 0, "http_err": 0, "no_url": 0, "already_set": 0}

    async def one(jp_row: JobPostingRow, comp_name: str) -> tuple[UUID, str | None]:
        if jp_row.apply_email:
            stats["already_set"] += 1
            return jp_row.id, jp_row.apply_email
        if not jp_row.source_url:
            stats["no_url"] += 1
            return jp_row.id, None
        async with sem:
            try:
                async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as c:
                    r = await c.get(jp_row.source_url)
            except Exception as e:
                logger.debug("enrich {} fetch failed: {}", jp_row.source_url, e)
                stats["http_err"] += 1
                return jp_row.id, None
        if r.status_code != 200:
            stats["http_err"] += 1
            return jp_row.id, None
        em = extract_apply_email(r.text, comp_name)
        if em:
            stats["hit"] += 1
        else:
            stats["miss"] += 1
        return jp_row.id, em

    pairs = [(jp, name) for jp, name in rows]
    fetched = await asyncio.gather(*[one(jp, n) for jp, n in pairs])

    # Persist
    async with Session() as session:
        for jp_id, em in fetched:
            if em:
                row = (await session.execute(select(JobPostingRow).where(JobPostingRow.id == jp_id))).scalar_one_or_none()
                if row and not row.apply_email:
                    row.apply_email = em
        await session.commit()

    return stats
