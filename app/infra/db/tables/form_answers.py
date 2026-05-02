"""SQLAlchemy table for cached / user-curated form answers.

Each row is a (normalized question text → answer) pair the bot has
encountered. On the next apply run the bot consults this table BEFORE
hitting the LLM — so a question Sergey hand-corrected once never gets
re-guessed by Claude.

Lifecycle:
  1. Bot sees a form question, normalize_question() → key
  2. SELECT WHERE question_norm = key → if hit, use that answer
  3. Else: ask LLM, save the answer with source='llm' + confidence
  4. After the run, `jhp qa review` lists low-confidence entries so the
     user can hand-correct them (source flips to 'user', conf=1.0)
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.engine import Base


class FormAnswerRow(Base):
    """One canonical (question → answer) pair learned from past applies."""

    __tablename__ = "form_answers"
    __table_args__ = (UniqueConstraint("question_norm", name="uq_form_answers_qnorm"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    question_norm: Mapped[str] = mapped_column(String(200), index=True)
    question_raw: Mapped[str] = mapped_column(String(500))
    options: Mapped[list | None] = mapped_column(JSON)        # for select/radio: list of allowed labels
    answer: Mapped[str] = mapped_column(String(2000))

    # 'user' = hand-curated truth (highest priority). 'llm' = guess to be reviewed.
    source: Mapped[str] = mapped_column(String(10), default="llm")
    confidence: Mapped[float] = mapped_column(default=1.0)

    used_count: Mapped[int] = mapped_column(default=1)
    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_used_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Optional context — useful for "review" CLI to show where this came from.
    last_company: Mapped[str | None] = mapped_column(String(200))
    last_job_title: Mapped[str | None] = mapped_column(String(300))
