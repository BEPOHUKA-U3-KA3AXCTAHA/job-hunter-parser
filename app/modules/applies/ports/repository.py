"""ApplyRepository port — persist + query applies, plus convenience joins
with companies/people that the scoring & curation services need."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.applies.models import Apply, ApplyStatus
from app.modules.people import DecisionMaker


@runtime_checkable
class ApplyRepository(Protocol):
    async def save(self, apply: Apply) -> None: ...
    async def save_many(self, applies: list[Apply]) -> None: ...
    async def get_by_id(self, apply_id: UUID) -> Apply | None: ...
    async def find_by_status(self, status: ApplyStatus) -> AsyncIterator[Apply]: ...
    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Apply]: ...
    async def update_status(self, apply_id: UUID, status: ApplyStatus) -> None: ...
    async def count(self) -> int: ...

    async def get_fresh_contacts(
        self, company_name: str, max_age_days: int
    ) -> list[DecisionMaker] | None:
        """Return cached contacts if last_dm_scan_at is within N days; None if stale/missing."""
        ...

    async def save_job_postings(self, postings: list, company_name_to_id: dict) -> int:
        """Persist job postings. Dedup by source_url. Returns new count."""
        ...

    async def mark_dm_scan_done(self, company_name: str) -> None:
        """Set company.last_dm_scan_at = now after we attempted dm enrichment for it."""
        ...
