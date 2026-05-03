"""CompanySource port — driven contract for fetching companies + jobs from external sources.

Implementations live under ../adapters/scrapers/.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.modules.companies.models import Company, JobPosting
from app.modules.companies import SearchCriteria


@runtime_checkable
class CompanySource(Protocol):
    """Driven port: scraping companies and job postings from a single source.

    Implementations: linkedin, web3career, rustjobs, remoteok.
    """

    source_name: str

    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]: ...

    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]: ...
