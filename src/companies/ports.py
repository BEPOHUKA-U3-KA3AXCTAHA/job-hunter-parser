from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.companies.models import Company, JobPosting


class CompanySource(ABC):
    """Port for scraping companies and job postings.

    Implementations: YC, Web3Career, RustJobs, Wellfound, RemoteOK.
    """

    source_name: str

    @abstractmethod
    async def fetch_companies(
        self,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Company]:
        ...

    @abstractmethod
    async def fetch_job_postings(
        self,
        company_id: str | None = None,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[JobPosting]:
        ...
