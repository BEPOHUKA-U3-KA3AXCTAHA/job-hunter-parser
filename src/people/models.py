from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4

from src.shared import Email, LinkedInUrl


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
    """A decision maker. Contacts (email/linkedin/twitter/github/etc) live in
    a single dict for extensibility — easy to add new channels without schema migrations.
    """
    full_name: str
    role: DecisionMakerRole
    company_id: UUID
    title_raw: str | None = None
    location: str | None = None

    # All contact channels in one bag. Known keys (any may be missing):
    #   email, linkedin, twitter, github, website, telegram, phone
    contacts: dict[str, str] = field(default_factory=dict)

    id: UUID = field(default_factory=uuid4)

    # --- Convenience accessors (validated wrappers) ---

    @property
    def email(self) -> Email | None:
        v = self.contacts.get("email")
        if not v:
            return None
        try:
            return Email(v)
        except ValueError:
            return None

    @property
    def linkedin_url(self) -> LinkedInUrl | None:
        v = self.contacts.get("linkedin")
        if not v:
            return None
        try:
            return LinkedInUrl(v)
        except ValueError:
            return None

    @property
    def twitter_handle(self) -> str | None:
        return self.contacts.get("twitter")

    @property
    def github_handle(self) -> str | None:
        return self.contacts.get("github")

    def has_any_contact(self) -> bool:
        return bool(self.contacts)

    def best_channel(self) -> str | None:
        for ch in ("linkedin", "email", "twitter", "telegram", "github"):
            if ch in self.contacts:
                return ch
        return None
