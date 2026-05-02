"""Driven port for the apply automation orchestrators.

Read + write operations the Easy-Apply / DM-outreach orchestrators need
to persist their outcomes and pull pending work. Service code never
touches ApplyRow / DecisionMakerRow / CompanyRow / JobPostingRow directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(slots=True)
class PendingOutreach:
    """A queued DM outreach the send_orchestrator can dispatch right now."""
    apply_id: UUID
    company_name: str
    dm_full_name: str
    dm_linkedin_url: str | None
    job_title: str
    job_url: str | None
    body: str
    relevance_score: int


@runtime_checkable
class MassApplyRepository(Protocol):
    """Read + write port for ATS / DM apply attempts."""

    # ---- Reads ----
    async def count_applies_today(self, flank: str) -> int:
        """Count applies for a flank with sent_at within the last 24h."""
        ...

    async def list_pending_dm_outreach(self, limit: int) -> list[PendingOutreach]:
        """Generated-but-not-sent dm_outreach rows with a LinkedIn target,
        ordered by relevance_score desc."""
        ...

    # ---- Writes ----
    async def upsert_mass_apply(
        self,
        company_name: str,
        job_title: str,
        job_url: str,
        channel: str,                # "ats_easy_apply" | "ats_workday" | …
        success: bool,
        notes: str,
        attempt_no: int = 1,
    ) -> None:
        """Insert or update the (synthetic-Hiring-Team) apply row for a job
        scraped during the mass-apply pipeline. Handles Company / DM /
        JobPosting upserts internally."""
        ...

    async def mark_apply_sent(
        self,
        apply_id: UUID,
        success: bool,
        notes: str = "",
    ) -> None:
        """Stamp sent_at = now and update status based on outcome."""
        ...
