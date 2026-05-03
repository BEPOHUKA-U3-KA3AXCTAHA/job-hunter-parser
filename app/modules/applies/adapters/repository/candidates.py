"""SQLA adapter for CandidateBundleRepository.

Only this module imports CompanyRow / JobPostingRow / DecisionMakerRow.
Returns fully-mapped domain objects so the curate service stays
table-blind.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.infra.db.tables.people import DecisionMakerRow
from app.modules.applies.ports.candidates import CandidateBundle
from app.modules.companies import Company, JobPosting
from app.modules.people import DecisionMaker, DecisionMakerRole
from app.modules.companies import Seniority, TechStack


def _safe_role(role_str: str) -> DecisionMakerRole:
    try:
        return DecisionMakerRole(role_str)
    except ValueError:
        return DecisionMakerRole.OTHER


def _safe_seniority(s: str | None) -> Seniority:
    if not s:
        return Seniority.UNKNOWN
    try:
        return Seniority(s)
    except ValueError:
        return Seniority.UNKNOWN


def _job_row_to_domain(jp: JobPostingRow, comp: CompanyRow) -> JobPosting:
    return JobPosting(
        title=jp.title,
        company_id=comp.id,
        company_name=comp.name,
        description=jp.description,
        tech_stack=TechStack.from_strings(*(jp.tech_stack or "").split(", ")),
        seniority=_safe_seniority(jp.seniority),
        is_remote=jp.is_remote,
        location=jp.location,
        salary_min=jp.salary_min,
        salary_max=jp.salary_max,
        salary_currency=jp.salary_currency,
        source=jp.source,
        source_url=jp.source_url,
        applicants_count=jp.applicants_count,
        posted_at=jp.posted_at,
        apply_email=jp.apply_email,
        id=jp.id,
    )


def _company_row_to_domain(comp: CompanyRow) -> Company:
    return Company(
        name=comp.name,
        website=comp.website,
        description=comp.description,
        tech_stack=TechStack.from_strings(*(comp.tech_stack or "").split(", ")),
        headcount=comp.headcount,
        location=comp.location,
        is_hiring=comp.is_hiring,
        source=comp.source,
        source_url=comp.source_url,
        id=comp.id,
    )


def _dm_row_to_domain(dm: DecisionMakerRow) -> DecisionMaker:
    return DecisionMaker(
        full_name=dm.full_name,
        role=_safe_role(dm.role),
        company_id=dm.company_id,
        title_raw=dm.title_raw,
        location=dm.location,
        contacts=dm.contacts or {},
        id=dm.id,
    )


class SqlaCandidateBundleRepository:
    """Implements CandidateBundleRepository over SQLAlchemy."""
    def __init__(self, session: AsyncSession) -> None:
        self._s = session


    async def load_active_bundles(self) -> list[CandidateBundle]:
        """Fetch every (job, company, dms-of-company) triple. Skips orphan jobs.
        Job ordering: posted_at desc (newest first, NULLs last).
        """
        result = await session.execute(
            select(JobPostingRow, CompanyRow)
            .join(CompanyRow, CompanyRow.id == JobPostingRow.company_id)
            .order_by(JobPostingRow.posted_at.desc().nullslast())
        )
        rows = result.all()

        company_ids = {c.id for _, c in rows}
        dm_result = await session.execute(
            select(DecisionMakerRow).where(DecisionMakerRow.company_id.in_(company_ids))
        )
        dms_by_company: dict = {}
        for dm in dm_result.scalars():
            dms_by_company.setdefault(dm.company_id, []).append(dm)

        out: list[CandidateBundle] = []
        for jp_row, comp_row in rows:
            comp = _company_row_to_domain(comp_row)
            out.append(CandidateBundle(
                job=_job_row_to_domain(jp_row, comp_row),
                company=comp,
                decision_makers=[_dm_row_to_domain(dm) for dm in dms_by_company.get(comp_row.id, [])],
            ))
        return out
