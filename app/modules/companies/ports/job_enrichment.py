"""Port for fetching a job posting's source page and pulling additional
fields (apply_email, etc) out of it. Adapter implementation lives in
adapters/job_enrichment/<impl>.py — typically httpx-backed."""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class JobEnrichment(Protocol):
    """Best-effort web fetch + parse for a batch of job IDs."""

    async def enrich_jobs_by_id(self, job_ids: list[UUID]) -> dict: ...
