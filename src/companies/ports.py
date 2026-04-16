from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from src.companies.models import Company, JobPosting
from src.shared import SearchCriteria


class CompanySource(ABC):
    """Port for scraping companies and job postings.

    Implementations: YC, Web3Career, RustJobs, Wellfound, RemoteOK, LinkedIn.
    """

    source_name: str

    @abstractmethod
    async def fetch_companies(self, criteria: SearchCriteria) -> AsyncIterator[Company]:
        ...

    @abstractmethod
    async def fetch_job_postings(self, criteria: SearchCriteria) -> AsyncIterator[JobPosting]:
        ...
