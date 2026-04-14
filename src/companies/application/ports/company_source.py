from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.companies.domain.company import Company
from src.companies.domain.job_posting import JobPosting


class CompanySourcePort(ABC):
    """Port for scraping companies and job postings from external sources.

    Implementations: YCombinator, Web3Career, RustJobs, Wellfound, RemoteOK, etc.
    """

    source_name: str

    @abstractmethod
    async def fetch_companies(
        self,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[Company]:
        """Yield companies matching filter, up to `limit`."""
        ...

    @abstractmethod
    async def fetch_job_postings(
        self,
        company_id: str | None = None,
        tech_stack_filter: list[str] | None = None,
        limit: int = 100,
    ) -> AsyncIterator[JobPosting]:
        """Yield job postings. If company_id is set, only for that company."""
        ...
