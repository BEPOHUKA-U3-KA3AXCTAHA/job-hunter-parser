"""SQLA implementation of QACacheRepository.

Cosmic-Python style: takes the AsyncSession in __init__ (handed in by the
SqlaUnitOfWork). NEVER opens its own session, NEVER commits — the UoW
owns transaction lifecycle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.tables.form_answers import FormAnswerRow
from app.modules.applies.ports.qa_cache import QACacheRepository


def normalize_question(label: str) -> str:
    """Lowercase + collapse whitespace + strip non-alphanumeric so trivial
    rewordings hit the same cache row."""
    import re
    s = (label or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


class SqlaQACacheRepository(QACacheRepository):
    """Implements `app.modules.applies.ports.qa_cache.QACacheRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- Reads ----

    async def get_cached(self, label: str) -> tuple[str, str, float] | None:
        key = normalize_question(label)
        if not key:
            return None
        row = (
            await self._s.execute(
                select(FormAnswerRow).where(FormAnswerRow.question_norm == key)
            )
        ).scalar_one_or_none()
        if not row:
            return None
        # bump usage counter (UoW will commit when service exits scope)
        row.used_count = (row.used_count or 0) + 1
        row.last_used_at = datetime.utcnow()
        return row.answer, row.source, row.confidence

    async def list_all(self, limit: int = 200, source: str | None = None) -> list[FormAnswerRow]:
        q = select(FormAnswerRow).order_by(FormAnswerRow.last_used_at.desc()).limit(limit)
        if source:
            q = q.where(FormAnswerRow.source == source)
        return list((await self._s.execute(q)).scalars())

    async def list_low_confidence(self, threshold: float = 0.7) -> list[FormAnswerRow]:
        return list(
            (
                await self._s.execute(
                    select(FormAnswerRow)
                    .where(FormAnswerRow.source == "llm")
                    .where(FormAnswerRow.confidence < threshold)
                    .order_by(FormAnswerRow.last_used_at.desc())
                )
            ).scalars()
        )

    # ---- Writes (UoW commits) ----

    async def save_to_cache(
        self,
        label: str,
        answer: str,
        options: list[str] | None,
        source: Literal["user", "llm"],
        confidence: float,
        company: str = "",
        job_title: str = "",
    ) -> None:
        key = normalize_question(label)
        if not key or not answer:
            return
        row = (
            await self._s.execute(
                select(FormAnswerRow).where(FormAnswerRow.question_norm == key)
            )
        ).scalar_one_or_none()
        if row:
            if row.source == "user" and source == "llm":
                return  # never overwrite hand-curated entries
            row.answer = answer
            row.options = options
            row.source = source
            row.confidence = confidence
            row.last_used_at = datetime.utcnow()
            row.last_company = company or row.last_company
            row.last_job_title = job_title or row.last_job_title
        else:
            self._s.add(FormAnswerRow(
                question_norm=key,
                question_raw=label[:500],
                options=options,
                answer=answer[:2000],
                source=source,
                confidence=confidence,
                last_company=company[:200] if company else None,
                last_job_title=job_title[:300] if job_title else None,
            ))

    async def upsert_user_answer(
        self, label: str, answer: str, options: list[str] | None = None,
    ) -> None:
        """Hand-curated entry — overrides any LLM guess."""
        await self.save_to_cache(
            label, answer, options, source="user", confidence=1.0,
        )
