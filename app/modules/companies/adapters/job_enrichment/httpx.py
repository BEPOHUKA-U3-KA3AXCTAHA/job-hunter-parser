"""Enrich existing job postings: fetch source URL, scrape apply-to email,
persist back via repository.

Pure orchestration over a CompanyDirectoryRepository port — no SQLAlchemy here.
Cheap and on-demand: only run for jobs you actually plan to act on
(curated set), not the whole scraped corpus. Bounded parallelism via
asyncio.Semaphore so we don't hammer any single host.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from loguru import logger

from app.modules.companies.ports.company_directory import CompanyDirectoryRepository, JobApplyTarget
from app.modules.companies.services.email_extract import extract_apply_email

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_CONCURRENCY = 4
_TIMEOUT = 20


def _default_uow_factory():
    """Lazy default — keeps the SQLA import out of module-load."""
    from app.modules.companies import default_uow
    return default_uow


async def enrich_jobs_by_id(
    job_ids: list[UUID],
    uow_factory=None,
) -> dict:
    """Fetch detail pages for the given jobs, extract apply_email, persist.

    Returns {hit, miss, http_err, no_url, already_set}.
    `uow_factory` returns a CompaniesUoW; defaults to SQLA-backed impl.
    """
    uow_factory = uow_factory or _default_uow_factory()
    async with uow_factory() as _read_uow:
        targets = await _read_uow.companies.load_apply_targets(job_ids)

    sem = asyncio.Semaphore(_CONCURRENCY)
    stats = {"hit": 0, "miss": 0, "http_err": 0, "no_url": 0, "already_set": 0}

    async def one(t: JobApplyTarget) -> tuple[UUID, str | None]:
        if t.current_apply_email:
            stats["already_set"] += 1
            return t.job_id, t.current_apply_email
        if not t.source_url:
            stats["no_url"] += 1
            return t.job_id, None
        async with sem:
            try:
                async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as c:
                    r = await c.get(t.source_url)
            except Exception as e:
                logger.debug("enrich {} fetch failed: {}", t.source_url, e)
                stats["http_err"] += 1
                return t.job_id, None
        if r.status_code != 200:
            stats["http_err"] += 1
            return t.job_id, None
        em = extract_apply_email(r.text, t.company_name)
        if em:
            stats["hit"] += 1
        else:
            stats["miss"] += 1
        return t.job_id, em

    fetched = await asyncio.gather(*[one(t) for t in targets])

    async with uow_factory() as _write_uow:
        for jp_id, em in fetched:
            if em:
                await _write_uow.companies.set_apply_email(jp_id, em)
        await _write_uow.commit()

    return stats
