from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4

from src.shared.kernel.email import Email
from src.shared.kernel.linkedin_url import LinkedInUrl


class DecisionMakerRole(StrEnum):
    FOUNDER = "founder"
    CEO = "ceo"
    CTO = "cto"
    HEAD_OF_ENGINEERING = "head_of_engineering"
    VP_ENGINEERING = "vp_engineering"
    ENGINEERING_MANAGER = "engineering_manager"
    TECH_LEAD = "tech_lead"
    RECRUITER = "recruiter"
    HR = "hr"
    OTHER = "other"

    @property
    def priority(self) -> int:
        """Higher = better target for outreach."""
        return {
            self.FOUNDER: 5,
            self.CEO: 5,
            self.CTO: 5,
            self.HEAD_OF_ENGINEERING: 4,
            self.VP_ENGINEERING: 4,
            self.ENGINEERING_MANAGER: 4,
            self.TECH_LEAD: 3,
            self.RECRUITER: 2,
            self.HR: 1,
            self.OTHER: 0,
        }[self]


@dataclass(slots=True)
class DecisionMaker:
    full_name: str
    role: DecisionMakerRole
    company_id: UUID
    title_raw: str | None = None
    email: Email | None = None
    linkedin_url: LinkedInUrl | None = None
    twitter_handle: str | None = None
    location: str | None = None

    id: UUID = field(default_factory=uuid4)

    def has_any_contact(self) -> bool:
        return any([self.email, self.linkedin_url, self.twitter_handle])

    def best_channel(self) -> str | None:
        if self.linkedin_url:
            return "linkedin"
        if self.email:
            return "email"
        if self.twitter_handle:
            return "twitter"
        return None
