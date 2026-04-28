"""Apply = one outreach attempt to a decision maker about a job opening.

Two flanks share this model:
  - dm_outreach: direct contact to a real person (CEO/CTO via LinkedIn/email/telegram)
  - mass_apply: submission to a posting's apply form (Easy Apply / Workday / careers@)

For mass_apply, decision_maker is a synthetic "Hiring Team" record (role=HR)
keyed to the company.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from src.companies.models import Company, JobPosting
from src.people.models import DecisionMaker


class ApplyStatus(StrEnum):
    NEW = "new"
    GENERATED = "generated"
    QUEUED = "queued"
    SENT = "sent"
    SEEN = "seen"
    REPLIED = "replied"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NO_REPLY = "no_reply"
    FAILED = "failed"


class ApplyChannel(StrEnum):
    LINKEDIN = "linkedin"                  # 1st-degree message or accepted Connect
    LINKEDIN_INMAIL = "linkedin_inmail"    # Premium InMail to non-connection
    EMAIL = "email"
    TELEGRAM = "telegram"
    TWITTER = "twitter"
    DISCORD = "discord"
    ATS_EASY_APPLY = "ats_easy_apply"      # LinkedIn Easy Apply form
    ATS_WORKDAY = "ats_workday"
    ATS_GREENHOUSE = "ats_greenhouse"
    ATS_LEVER = "ats_lever"
    ATS_ASHBY = "ats_ashby"
    OTHER = "other"


class ApplyFlank(StrEnum):
    """Which automation arm produced this apply."""
    MASS_APPLY = "mass_apply"        # bulk via ATS forms / Easy Apply
    DM_OUTREACH = "dm_outreach"      # 1-to-1 personal contact


class ApplyMethod(StrEnum):
    """How the apply was generated and (eventually) sent."""
    MANUAL = "manual"                # user generated and sent by hand
    HAND_WRITTEN = "hand_written"    # we wrote it manually but bot will send
    AUTO_APPLY = "auto_apply"        # bot generated + sent (mass_apply pipeline)
    AUTO_OUTREACH = "auto_outreach"  # bot generated + sent (dm_outreach pipeline)
    SEMI_AUTO = "semi_auto"          # bot generated, user reviews + clicks send


@dataclass(slots=True)
class Apply:
    """A planned / sent outreach for one (job, decision_maker) pair.

    Company is reachable via decision_maker.company_id.
    """

    decision_maker: DecisionMaker
    company: Company                            # for convenience during pipeline
    job_posting: JobPosting | None = None       # which posting this targets
    attempt_no: int = 1

    flank: ApplyFlank = ApplyFlank.DM_OUTREACH
    method: ApplyMethod = ApplyMethod.MANUAL
    channel: ApplyChannel | None = None

    relevance_score: int = 0
    status: ApplyStatus = ApplyStatus.NEW
    notes: str = ""

    # Content
    body: str = ""                              # DM message body or email body
    subject: str | None = None                  # email/inmail subject
    cover_letter: str | None = None             # ATS form CL field
    form_responses: dict[str, str] | None = None  # {field_name: answer} for ATS
    apply_url: str | None = None                # exact URL we submitted to

    # Dates
    generated_at: datetime = field(default_factory=datetime.utcnow)
    sent_at: datetime | None = None
    response_at: datetime | None = None

    id: UUID = field(default_factory=uuid4)

    def is_worth_outreach(self, min_score: int = 60) -> bool:
        return (
            self.relevance_score >= min_score
            and self.decision_maker.has_any_contact()
            and self.company.is_actively_hiring()
        )

    def advance_status(self, new_status: ApplyStatus) -> None:
        self.status = new_status


# --- Backwards-compat aliases (will be removed once all callsites updated) ---
Message = Apply
MessageStatus = ApplyStatus
MessageChannel = ApplyChannel
