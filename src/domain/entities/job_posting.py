from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from src.domain.value_objects.seniority import Seniority
from src.domain.value_objects.tech_stack import TechStack


@dataclass(slots=True)
class JobPosting:
    company_id: UUID
    title: str
    description: str | None = None
    tech_stack: TechStack = field(default_factory=TechStack)
    seniority: Seniority = Seniority.UNKNOWN
    is_remote: bool = False
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    source_url: str | None = None

    id: UUID = field(default_factory=uuid4)
    posted_at: datetime | None = None
    discovered_at: datetime = field(default_factory=datetime.utcnow)
