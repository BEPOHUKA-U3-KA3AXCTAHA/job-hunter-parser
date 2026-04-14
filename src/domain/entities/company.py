from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from src.domain.value_objects.tech_stack import TechStack


@dataclass(slots=True)
class Company:
    name: str
    website: str | None = None
    description: str | None = None
    tech_stack: TechStack = field(default_factory=TechStack)
    headcount: int | None = None
    location: str | None = None
    is_hiring: bool = False
    source: str | None = None  # "yc" | "web3.career" | "rustjobs" | "wellfound" | "remoteok"
    source_url: str | None = None
    tags: list[str] = field(default_factory=list)

    id: UUID = field(default_factory=uuid4)
    discovered_at: datetime = field(default_factory=datetime.utcnow)

    def is_startup(self, max_headcount: int = 200) -> bool:
        return self.headcount is not None and self.headcount <= max_headcount

    def is_actively_hiring(self) -> bool:
        return self.is_hiring
