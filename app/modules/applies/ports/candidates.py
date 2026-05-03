"""Port: load candidate (job × company × decision-makers) bundles for curation.

Service layer asks for already-domain objects — no Row leakage.
Adapter does the SQL join + Row→domain mapping internally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.modules.companies import Company, JobPosting
from app.modules.people import DecisionMaker


@dataclass(slots=True)
class CandidateBundle:
    """One job posting + the company that owns it + every DM at that company.

    Returned by `CandidateBundles.load_active_bundles()` — the
    curate service then filters/scores these into outreach pairs.
    """
    job: JobPosting
    company: Company
    decision_makers: list[DecisionMaker] = field(default_factory=list)


@runtime_checkable
class CandidateBundles(Protocol):
    """Driven port: persist + load (job, company, dms) bundles for curation."""

    async def load_active_bundles(self) -> list[CandidateBundle]: ...
