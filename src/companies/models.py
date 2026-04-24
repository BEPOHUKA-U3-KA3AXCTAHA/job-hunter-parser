from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from src.shared import Seniority, TechStack


@dataclass(slots=True)
class Company:
    name: str
    website: str | None = None
    description: str | None = None
    tech_stack: TechStack = field(default_factory=TechStack)
    headcount: int | None = None
    location: str | None = None
    is_hiring: bool = False
    source: str | None = None  # "yc" | "web3" | "rustjobs" | "wellfound" | "remoteok"
    source_url: str | None = None
    tags: list[str] = field(default_factory=list)

    id: UUID = field(default_factory=uuid4)
    discovered_at: datetime = field(default_factory=datetime.utcnow)

    def is_startup(self, max_headcount: int = 200) -> bool:
        return self.headcount is not None and self.headcount <= max_headcount

    def is_actively_hiring(self) -> bool:
        return self.is_hiring


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

    id: UUID = field(default_factory=uuid4)
    posted_at: datetime | None = None
    discovered_at: datetime = field(default_factory=datetime.utcnow)
