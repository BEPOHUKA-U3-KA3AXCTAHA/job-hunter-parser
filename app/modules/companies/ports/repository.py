"""Companies module driven port — read + write operations the service layer needs.

Service code (job_enrich, future enrich/curate operations) depends on this
Protocol; SQLA adapter implements it. The only place importing CompanyRow
/ JobPostingRow is the adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(slots=True)
class JobApplyTarget:
    """Minimal projection: just what `enrich_jobs_by_id` needs to act on a job.

    Repository returns this so the service stays Row-blind.
    """
    job_id: UUID
    company_name: str
    source_url: str | None
    current_apply_email: str | None


@runtime_checkable
class CompanyRepository(Protocol):
    """Read + write port for the companies/jobs schema."""

    async def load_apply_targets(self, job_ids: list[UUID]) -> list[JobApplyTarget]: ...

    async def set_apply_email(self, job_id: UUID, email: str) -> None:
        """Set apply_email on a job posting if currently empty (idempotent)."""
        ...
