"""SQLA implementation of MassApplyRepository.

Cosmic-Python style: takes the AsyncSession in __init__ (handed in by the
SqlaUnitOfWork at __aenter__ time). NEVER opens its own session, NEVER
commits — those responsibilities live in the UoW.

Only place importing the cross-module Row classes used by the apply
orchestrators (ApplyRow + CompanyRow + DecisionMakerRow + JobPostingRow).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.tables.applies import ApplyRow
from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.infra.db.tables.people import DecisionMakerRow
from app.modules.applies.ports.mass_apply import MassApplyRepository, PendingOutreach


class SqlaMassApplyRepository(MassApplyRepository):
    """Implements `app.modules.applies.ports.mass_apply.MassApplyRepository`.

    Session is handed in by the UoW. Methods perform their reads/writes on
    that shared session; the UoW is responsible for commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- Reads ----

    async def count_applies_today(self, flank: str) -> int:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        result = await self._s.execute(
            select(ApplyRow).where(
                and_(
                    ApplyRow.flank == flank,
                    ApplyRow.sent_at.is_not(None),
                    ApplyRow.sent_at >= cutoff,
                )
            )
        )
        return len(list(result.scalars()))

    async def list_pending_dm_outreach(self, limit: int) -> list[PendingOutreach]:
        result = await self._s.execute(
            select(ApplyRow, DecisionMakerRow, CompanyRow, JobPostingRow)
            .join(DecisionMakerRow, ApplyRow.decision_maker_id == DecisionMakerRow.id)
            .join(CompanyRow, DecisionMakerRow.company_id == CompanyRow.id)
            .outerjoin(JobPostingRow, ApplyRow.job_posting_id == JobPostingRow.id)
            .where(
                and_(
                    ApplyRow.flank == "dm_outreach",
                    ApplyRow.sent_at.is_(None),
                    or_(ApplyRow.status == "generated", ApplyRow.status == "queued"),
                    ApplyRow.body.is_not(None),
                )
            )
            .order_by(ApplyRow.relevance_score.desc(), ApplyRow.generated_at.asc())
            .limit(limit)
        )
        out: list[PendingOutreach] = []
        for ap, dm, comp, jp in result.all():
            out.append(PendingOutreach(
                apply_id=ap.id,
                company_name=comp.name,
                dm_full_name=dm.full_name,
                dm_linkedin_url=(dm.contacts or {}).get("linkedin"),
                job_title=(jp.title if jp else "") or "",
                job_url=(jp.source_url if jp else None),
                body=ap.body or "",
                relevance_score=ap.relevance_score or 0,
            ))
        return out

    # ---- Writes (no commit — the UoW commits at the end) ----

    async def upsert_mass_apply(
        self,
        company_name: str,
        job_title: str,
        job_url: str,
        channel: str,
        success: bool,
        notes: str,
        attempt_no: int = 1,
    ) -> None:
        # 1. Company
        comp = (
            await self._s.execute(select(CompanyRow).where(CompanyRow.name == company_name))
        ).scalar_one_or_none()
        if not comp:
            comp = CompanyRow(name=company_name, source="linkedin_easy_apply", is_hiring=True)
            self._s.add(comp)
            await self._s.flush()

        # 2. Synthetic Hiring Team DM
        dm = (
            await self._s.execute(
                select(DecisionMakerRow).where(
                    and_(
                        DecisionMakerRow.company_id == comp.id,
                        DecisionMakerRow.full_name == "Hiring Team",
                    )
                )
            )
        ).scalar_one_or_none()
        if not dm:
            dm = DecisionMakerRow(
                company_id=comp.id, full_name="Hiring Team", role="hr",
                contacts={"channel": "linkedin_easy_apply"},
            )
            self._s.add(dm)
            await self._s.flush()

        # 3. Job posting
        jp = (
            await self._s.execute(select(JobPostingRow).where(JobPostingRow.source_url == job_url))
        ).scalar_one_or_none()
        if not jp:
            jp = JobPostingRow(
                title=job_title, company_id=comp.id,
                source="linkedin_easy_apply", source_url=job_url, is_active=True,
            )
            self._s.add(jp)
            await self._s.flush()

        # 4. Upsert apply row
        now = datetime.utcnow()
        existing = (
            await self._s.execute(
                select(ApplyRow).where(
                    and_(
                        ApplyRow.job_posting_id == jp.id,
                        ApplyRow.decision_maker_id == dm.id,
                        ApplyRow.attempt_no == attempt_no,
                    )
                )
            )
        ).scalar_one_or_none()
        note_str = notes[:300]
        if existing:
            existing.status = "sent" if success else "failed"
            existing.method = "auto_apply"
            existing.channel = channel
            existing.apply_url = job_url
            if success and existing.sent_at is None:
                existing.sent_at = now
            existing.notes = (existing.notes or "") + " | " + note_str
        else:
            self._s.add(ApplyRow(
                job_posting_id=jp.id, decision_maker_id=dm.id, attempt_no=attempt_no,
                flank="mass_apply", method="auto_apply", channel=channel,
                relevance_score=50,
                status="sent" if success else "failed",
                apply_url=job_url,
                sent_at=now if success else None,
                generated_at=now,
                notes=note_str,
            ))

    async def mark_apply_sent(
        self, apply_id: UUID, success: bool, notes: str = "",
    ) -> None:
        row = (
            await self._s.execute(select(ApplyRow).where(ApplyRow.id == apply_id))
        ).scalar_one_or_none()
        if not row:
            logger.warning("mark_apply_sent: row {} not found", apply_id)
            return
        now = datetime.utcnow()
        if success:
            row.sent_at = now
            row.status = "sent"
        else:
            row.status = "failed"
        if notes:
            row.notes = (row.notes or "") + " | " + notes[:300]
