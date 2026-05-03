"""ApplyJournalRepository port — persist + query applies, plus convenience joins
with companies/people that the scoring & curation services need."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.applies.models import Apply, ApplyStatus
from app.modules.companies import Company
from app.modules.people import DecisionMaker


@runtime_checkable
class ApplyJournalRepository(Protocol):
    async def save(self, apply: Apply) -> None: ...
    async def save_many(self, applies: list[Apply]) -> None: ...
    async def get_by_id(self, apply_id: UUID) -> Apply | None: ...
    async def find_by_status(self, status: ApplyStatus) -> AsyncIterator[Apply]: ...
    async def list_by_status(self, status: str, limit: int = 50) -> list[Apply]:
        """Return up to `limit` applies with the given status (string form,
        accepts the raw column value). Used by the retry CLI command."""
        ...
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

    async def upsert_company_with_dm(
        self, company: Company, dm: DecisionMaker,
    ) -> None:
        """Persist a (company, decision_maker) pair without a generated message —
        used by the pipeline to keep enrichment records when the LLM bodyless'd a row."""
        ...

    async def company_name_to_id(self, names: list[str]) -> dict[str, UUID]:
        """Resolve a batch of company names to row IDs in one query.
        Names not present in the DB are simply absent from the returned dict."""
        ...
