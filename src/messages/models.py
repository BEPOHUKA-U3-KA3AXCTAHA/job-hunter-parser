"""Message = one outreach attempt to a decision maker.

Includes: relevance, status, generated body, channel, send/reply tracking.
Previously split between `leads.Lead` and `outreach.OutreachMessage` — now unified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from src.companies.models import Company, JobPosting
from src.people.models import DecisionMaker


class MessageStatus(StrEnum):
    NEW = "new"
    ENRICHED = "enriched"
    GENERATED = "generated"
    SENT = "sent"
    REPLIED = "replied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    NO_REPLY = "no_reply"


class MessageChannel(StrEnum):
    LINKEDIN = "linkedin"
    EMAIL = "email"
    TWITTER = "twitter"
    TELEGRAM = "telegram"
    DISCORD = "discord"


@dataclass(slots=True)
class Message:
    """A planned / sent outreach to one decision maker.

    Company is reachable via decision_maker.company_id.
    """

    decision_maker: DecisionMaker
    company: Company                            # for convenience during pipeline
    job_posting: JobPosting | None = None       # which posting this outreach references
    attempt_no: int = 1
    relevance_score: int = 0
    status: MessageStatus = MessageStatus.NEW
    notes: str = ""

    # Message body — written once at this attempt
    body: str = ""
    subject: str | None = None                  # only for email
    channel: MessageChannel | None = None
    generated_at: datetime = field(default_factory=datetime.utcnow)

    id: UUID = field(default_factory=uuid4)

    def is_worth_outreach(self, min_score: int = 60) -> bool:
        return (
            self.relevance_score >= min_score
            and self.decision_maker.has_any_contact()
            and self.company.is_actively_hiring()
        )

    def advance_status(self, new_status: MessageStatus) -> None:
        self.status = new_status
