"""SQLAlchemy ORM table for the applies module."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db import Base

if TYPE_CHECKING:
    from app.modules.people.adapters.orm import DecisionMakerRow  # noqa: F401


class ApplyRow(Base):
    """One outreach attempt per (job_posting, decision_maker, attempt_no).

    Two flanks share this table:
      - flank='dm_outreach' — direct contact to a specific person (CEO/CTO via LinkedIn/email/telegram)
      - flank='mass_apply'  — submission to a posting's apply form (Easy Apply / Workday / Greenhouse / careers@)

    For mass_apply, decision_maker_id points to a SYNTHETIC "Hiring Team" DM
    (role=HR, contacts={email: careers@...}) — keeps the (job, dm) tuple uniform.
    """
    __tablename__ = "applies"
    __table_args__ = (
        UniqueConstraint(
            "job_posting_id", "decision_maker_id", "attempt_no",
            name="uq_apply_job_dm_attempt",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_posting_id: Mapped[UUID | None] = mapped_column(ForeignKey("job_postings.id"), index=True)
    decision_maker_id: Mapped[UUID] = mapped_column(ForeignKey("decision_makers.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(default=1)

    # Origin & routing
    flank: Mapped[str] = mapped_column(String(20), default="dm_outreach")
    method: Mapped[str] = mapped_column(String(20), default="manual")
    channel: Mapped[str | None] = mapped_column(String(30))

    # Scoring
    relevance_score: Mapped[int] = mapped_column(default=0)

    # Status: new → generated → queued → sent → seen → replied → ...
    status: Mapped[str] = mapped_column(String(30), default="new")

    # Content
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(String(4000))
    cover_letter: Mapped[str | None] = mapped_column(String(4000))
    form_responses: Mapped[dict | None] = mapped_column(JSON)
    apply_url: Mapped[str | None] = mapped_column(String(500))

    notes: Mapped[str] = mapped_column(String(2000), default="")

    # Dates
    generated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    sent_at: Mapped[datetime | None]
    response_at: Mapped[datetime | None]

    decision_maker: Mapped["DecisionMakerRow"] = relationship(
        "DecisionMakerRow", back_populates="applies"
    )
