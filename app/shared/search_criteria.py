from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.shared.seniority import Seniority


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """What kind of jobs to look for. Passed to scrapers as port argument."""

    tech_stack: list[str] = field(default_factory=lambda: ["python", "rust"])
    roles: list[str] = field(default_factory=lambda: ["backend", "software engineer"])
    seniority: list[Seniority] = field(
        default_factory=lambda: [Seniority.MIDDLE, Seniority.SENIOR]
    )
    locations: list[str] = field(default_factory=lambda: ["remote"])
    salary_min_usd: int | None = None
    sources: list[str] = field(
        default_factory=lambda: ["web3", "linkedin", "rustjobs", "remoteok"]
    )
    exclude_keywords: list[str] = field(
        default_factory=lambda: ["intern", "frontend", "qa", "devops", "manager"]
    )
    limit_per_source: int = 50

    # Competition filters — to avoid saturated postings.
    # max_applicants: skip posts with > N applicants (None = no filter).
    #   Only LinkedIn exposes this publicly; on other boards posts pass when applicants_count is None.
    # max_posted_age_days: skip posts older than N days (None = no filter).
    #   Effective on LinkedIn / RemoteOK / web3.career; rustjobs doesn't expose dates.
    max_applicants: int | None = None
    max_posted_age_days: int | None = None

    def matches_title(self, title: str) -> bool:
        t = (title or "").strip().lower()
        if not t:
            return False
        if any(ex in t for ex in self.exclude_keywords):
            return False
        return True

    def matches_salary(self, salary_min: int | None) -> bool:
        if self.salary_min_usd is None or salary_min is None:
            return True
        return salary_min >= self.salary_min_usd

    def matches_competition(
        self, applicants_count: int | None, posted_at: datetime | None
    ) -> bool:
        """Reject posts that look saturated.
        Unknown signals pass — we only filter when the source explicitly told us
        the post is over the limit.
        """
        if self.max_applicants is not None and applicants_count is not None:
            if applicants_count > self.max_applicants:
                return False
        if self.max_posted_age_days is not None and posted_at is not None:
            age = (datetime.utcnow() - posted_at).days
            if age > self.max_posted_age_days:
                return False
        return True
