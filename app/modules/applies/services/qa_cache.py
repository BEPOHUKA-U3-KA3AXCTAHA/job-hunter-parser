"""Cache layer for form-question answers.

Backed by app.infra.db.tables.form_answers. Two reads per question:
  1. by exact question_norm
  2. fall back to LLM (caller decides)

Writes: every LLM-supplied answer with confidence ≥ 0.6 is persisted as
source='llm' so 'jhp qa review' can surface them for the user to confirm
or edit. User-edited rows have source='user' and always win over LLM.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from loguru import logger
from sqlalchemy import select

from app.infra.db import get_session_maker
from app.infra.db.tables.form_answers import FormAnswerRow

_NORM_RE = re.compile(r"[^a-z0-9]+")
_NORM_LEN = 200


def normalize_question(label: str) -> str:
    """Canonical key for a question label.

    Lowercase + collapse non-alnum to single space + strip + truncate.
    Identical questions across different jobs (e.g. 'What is your expected
    annual compensation') hash to the same key.
    """
    return _NORM_RE.sub(" ", (label or "").lower()).strip()[:_NORM_LEN]


async def get_cached(label: str) -> tuple[str, str, float] | None:
    """Return (answer, source, confidence) if a cached answer exists, else None."""
    key = normalize_question(label)
    if not key:
        return None
    Session = get_session_maker()
    async with Session() as session:
        row = (
            await session.execute(
                select(FormAnswerRow).where(FormAnswerRow.question_norm == key)
            )
        ).scalar_one_or_none()
        if not row:
            return None
        # Bump usage stats
        row.used_count = (row.used_count or 0) + 1
        row.last_used_at = datetime.utcnow()
        await session.commit()
        return row.answer, row.source, row.confidence


async def save_to_cache(
    label: str,
    answer: str,
    options: list[str] | None,
    source: Literal["user", "llm"],
    confidence: float,
    company: str = "",
    job_title: str = "",
) -> None:
    """Insert or upsert a cache entry. User-source rows are never overwritten by LLM."""
    key = normalize_question(label)
    if not key or not answer:
        return
    Session = get_session_maker()
    async with Session() as session:
        row = (
            await session.execute(
                select(FormAnswerRow).where(FormAnswerRow.question_norm == key)
            )
        ).scalar_one_or_none()
        if row:
            # User-curated rows are sacred — LLM cannot overwrite them
            if row.source == "user" and source == "llm":
                return
            row.answer = answer
            row.options = options
            row.source = source
            row.confidence = confidence
            row.last_used_at = datetime.utcnow()
            row.last_company = company or row.last_company
            row.last_job_title = job_title or row.last_job_title
        else:
            row = FormAnswerRow(
                question_norm=key,
                question_raw=label[:500],
                options=options,
                answer=answer[:2000],
                source=source,
                confidence=confidence,
                last_company=company[:200] if company else None,
                last_job_title=job_title[:300] if job_title else None,
            )
            session.add(row)
        try:
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.debug("qa_cache write failed: {}", e)


async def list_all(limit: int = 200, source: str | None = None) -> list[FormAnswerRow]:
    Session = get_session_maker()
    async with Session() as session:
        q = select(FormAnswerRow).order_by(FormAnswerRow.last_used_at.desc()).limit(limit)
        if source:
            q = q.where(FormAnswerRow.source == source)
        return list((await session.execute(q)).scalars())


async def list_low_confidence(threshold: float = 0.7) -> list[FormAnswerRow]:
    """Rows where the LLM was uncertain — surface for user review."""
    Session = get_session_maker()
    async with Session() as session:
        return list(
            (
                await session.execute(
                    select(FormAnswerRow)
                    .where(FormAnswerRow.source == "llm")
                    .where(FormAnswerRow.confidence < threshold)
                    .order_by(FormAnswerRow.last_used_at.desc())
                )
            ).scalars()
        )


async def upsert_user_answer(label: str, answer: str, options: list[str] | None = None) -> None:
    """Hand-curated entry — overrides any LLM guess."""
    await save_to_cache(label, answer, options, source="user", confidence=1.0)
