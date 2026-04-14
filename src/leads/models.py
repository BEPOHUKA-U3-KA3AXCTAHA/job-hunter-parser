from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from src.companies.models import Company
from src.people.models import DecisionMaker


class LeadStatus(StrEnum):
    NEW = "new"
    ENRICHED = "enriched"
    MESSAGE_GENERATED = "message_generated"
    SENT = "sent"
    REPLIED = "replied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    NO_REPLY = "no_reply"


@dataclass(slots=True)
class Lead:
    """Company + DecisionMaker + outreach state."""

    company: Company
    decision_maker: DecisionMaker
    relevance_score: int = 0  # 0-100
    status: LeadStatus = LeadStatus.NEW
    notes: str = ""

    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def is_worth_outreach(self, min_score: int = 60) -> bool:
        return (
            self.relevance_score >= min_score
            and self.decision_maker.has_any_contact()
            and self.company.is_actively_hiring()
        )

    def advance_status(self, new_status: LeadStatus) -> None:
        self.status = new_status
        self.updated_at = datetime.utcnow()
