from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.seniority import Seniority


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

    def matches_title(self, title: str) -> bool:
        t = title.lower()
        if any(ex in t for ex in self.exclude_keywords):
            return False
        return True

    def matches_salary(self, salary_min: int | None) -> bool:
        if self.salary_min_usd is None or salary_min is None:
            return True
        return salary_min >= self.salary_min_usd
