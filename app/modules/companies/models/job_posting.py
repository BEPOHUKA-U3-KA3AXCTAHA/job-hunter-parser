"""JobPosting entity — a single advertised role at a company."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from app.modules.companies.models.seniority import Seniority
from app.modules.companies.models.tech_stack import TechStack


@dataclass(slots=True)
class JobPosting:
    title: str
    company_id: UUID | None = None             # filled later when company is persisted
    company_name: str = ""                      # source-provided company name (for joining later)
    description: str | None = None
    tech_stack: TechStack = field(default_factory=TechStack)
    seniority: Seniority = Seniority.UNKNOWN
    is_remote: bool = False
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    source: str | None = None                   # "web3.career" / "linkedin" / etc
    source_url: str | None = None

    # Competition signals — used to filter saturated postings.
    # applicants_count: best-effort, only LinkedIn exposes it publicly
    # posted_at: when the post went live; older posts ⇒ more applicants
    applicants_count: int | None = None
    posted_at: datetime | None = None

    # Real apply-to email when the posting (or its detail page) exposes one.
    # Usually careers@/jobs@/hr@<companydomain> — verified, near-zero bounce.
    apply_email: str | None = None

    id: UUID = field(default_factory=uuid4)
    discovered_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def posted_age_days(self) -> int | None:
        if self.posted_at is None:
            return None
        return (datetime.utcnow() - self.posted_at).days


class JobPostingNotFound(Exception):
    """Raised by repository when a lookup misses."""
