"""SQLA implementation of CompanyRepository.

Only place importing CompanyRow / JobPostingRow.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.infra.db import get_session_maker
from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.modules.companies.ports.repository import JobApplyTarget


class SqlaCompanyRepository:
    """Implements `app.modules.companies.ports.repository.CompanyRepository`."""

    async def load_apply_targets(self, job_ids: list[UUID]) -> list[JobApplyTarget]:
        if not job_ids:
            return []
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(JobPostingRow, CompanyRow.name)
                .join(CompanyRow, CompanyRow.id == JobPostingRow.company_id)
                .where(JobPostingRow.id.in_(job_ids))
            )
            return [
                JobApplyTarget(
                    job_id=jp.id,
                    company_name=comp_name or "",
                    source_url=jp.source_url,
                    current_apply_email=jp.apply_email,
                )
                for jp, comp_name in result.all()
            ]

    async def set_apply_email(self, job_id: UUID, email: str) -> None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(select(JobPostingRow).where(JobPostingRow.id == job_id))
            ).scalar_one_or_none()
            if row and not row.apply_email:
                row.apply_email = email
                await session.commit()
