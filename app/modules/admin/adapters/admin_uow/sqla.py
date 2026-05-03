"""SQLAlchemy-backed admin UoW + repository.

The admin module is the legitimate place for cross-table SQL — diagnostic
counts, listings, cleanup queries — so it's the only adapter (alongside
the per-module *_uow adapters) that may import session machinery.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import get_session_maker
from app.infra.db.tables.applies import ApplyRow
from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.infra.db.tables.people import DecisionMakerRow
from app.modules.admin.models import (
    CompanyDump,
    DbStatus,
    JobDump,
    PersonDump,
    StaleCompany,
)
from app.modules.admin.ports.admin_uow import AdminRepository, AdminUoW


class SqlaAdminRepository(AdminRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def db_status(self) -> DbStatus:
        total_companies = (await self._s.execute(select(func.count(CompanyRow.id)))).scalar() or 0
        total_dms = (await self._s.execute(select(func.count(DecisionMakerRow.id)))).scalar() or 0
        total_applies = (await self._s.execute(select(func.count(ApplyRow.id)))).scalar() or 0
        cutoff = datetime.utcnow() - timedelta(hours=24)
        sent_today = (
            await self._s.execute(
                select(func.count(ApplyRow.id)).where(
                    and_(ApplyRow.sent_at.is_not(None), ApplyRow.sent_at >= cutoff)
                )
            )
        ).scalar() or 0
        return DbStatus(
            total_companies=int(total_companies),
            total_dms=int(total_dms),
            total_applies=int(total_applies),
            sent_today=int(sent_today),
        )

    async def list_companies(
        self, limit: int, hiring_only: bool = False,
    ) -> list[CompanyDump]:
        stmt = (
            select(CompanyRow)
            .order_by(CompanyRow.last_dm_scan_at.desc().nullslast(), CompanyRow.name)
            .limit(limit)
        )
        if hiring_only:
            stmt = stmt.where(CompanyRow.is_hiring.is_(True))
        rows = (await self._s.execute(stmt)).scalars()
        return [
            CompanyDump(
                id=r.id, name=r.name, source=r.source,
                is_hiring=r.is_hiring, last_dm_scan_at=r.last_dm_scan_at,
            )
            for r in rows
        ]

    async def list_people(self, limit: int) -> list[PersonDump]:
        stmt = (
            select(DecisionMakerRow, CompanyRow)
            .join(CompanyRow)
            .order_by(CompanyRow.last_dm_scan_at.desc().nullslast(), CompanyRow.name)
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            PersonDump(
                dm_id=dm.id, full_name=dm.full_name, role=dm.role,
                company_name=comp.name, contacts=dm.contacts or {},
            )
            for dm, comp in rows
        ]

    async def list_jobs(self, limit: int) -> list[JobDump]:
        stmt = (
            select(JobPostingRow, CompanyRow)
            .outerjoin(CompanyRow, CompanyRow.id == JobPostingRow.company_id)
            .order_by(JobPostingRow.posted_at.desc().nullslast(), JobPostingRow.first_seen_at.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            JobDump(
                job_id=jp.id, title=jp.title,
                company_name=(comp.name if comp else None),
                posted_at=jp.posted_at, first_seen_at=jp.first_seen_at,
                source=jp.source, source_url=jp.source_url,
                applicants_count=jp.applicants_count,
            )
            for jp, comp in rows
        ]

    async def stale_companies(
        self, max_age_days: int, limit: int = 50,
    ) -> list[StaleCompany]:
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        stmt = (
            select(CompanyRow)
            .where(or_(CompanyRow.last_dm_scan_at < cutoff, CompanyRow.last_dm_scan_at.is_(None)))
            .order_by(CompanyRow.last_dm_scan_at.asc().nullsfirst())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars()
        return [
            StaleCompany(id=r.id, name=r.name, last_dm_scan_at=r.last_dm_scan_at)
            for r in rows
        ]

    async def record_external_apply(
        self,
        company_name: str,
        job_url: str,
        job_title: str,
        channel: str,
        outcome: str,
        detail: str,
    ) -> None:
        """One atomic upsert of Company + synthetic 'Hiring Team' DM +
        JobPosting + ApplyRow — the API /apply-result endpoint's path."""
        comp = (
            await self._s.execute(select(CompanyRow).where(CompanyRow.name == company_name))
        ).scalar_one_or_none()
        if not comp:
            comp = CompanyRow(name=company_name, source="firefox_extension", is_hiring=True)
            self._s.add(comp)
            await self._s.flush()
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
                contacts={"channel": "firefox_extension"},
            )
            self._s.add(dm)
            await self._s.flush()
        jp = (
            await self._s.execute(
                select(JobPostingRow).where(JobPostingRow.source_url == job_url)
            )
        ).scalar_one_or_none()
        if not jp:
            jp = JobPostingRow(
                title=job_title, company_id=comp.id,
                source="firefox_extension", source_url=job_url, is_active=True,
            )
            self._s.add(jp)
            await self._s.flush()
        now = datetime.utcnow()
        success = outcome in ("applied", "interest_signaled")
        self._s.add(ApplyRow(
            job_posting_id=jp.id, decision_maker_id=dm.id, attempt_no=1,
            flank="mass_apply", method="auto_apply", channel=channel,
            relevance_score=50,
            status="sent" if success else "failed",
            apply_url=job_url,
            sent_at=now if success else None,
            generated_at=now,
            notes=detail[:300],
        ))


class SqlaAdminUoW(AdminUoW):
    _session: AsyncSession | None

    def __init__(self) -> None:
        self._session = None

    async def __aenter__(self) -> SqlaAdminUoW:
        self._session = get_session_maker()()
        self.admin = SqlaAdminRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        try:
            await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UoW.commit() outside of `async with` scope")
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is None:
            return
        await self._session.rollback()
