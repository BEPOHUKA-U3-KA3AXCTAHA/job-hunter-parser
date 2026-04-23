"""SQLite-backed LeadRepository using SQLAlchemy async.

Deduplicates by (company name, decision maker name).
Updates last_seen_at on re-scrape.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select

from src.companies.models import Company
from src.leads.db import CompanyRow, DecisionMakerRow, JobPostingRow, LeadRow, get_session_maker
from src.leads.models import Lead, LeadStatus
from src.leads.ports import LeadRepository
from src.people.models import DecisionMaker, DecisionMakerRole
from src.shared import Email, LinkedInUrl, TechStack


class SqliteLeadRepository(LeadRepository):
    """Persists leads in SQLite. Deduplicates on re-scrape."""

    async def save(self, lead: Lead) -> None:
        await self.save_many([lead])

    async def save_many(self, leads: list[Lead]) -> None:
        if not leads:
            return

        Session = get_session_maker()
        saved_companies = 0
        saved_dms = 0
        saved_leads = 0

        async with Session() as session:
            for lead in leads:
                company_row = await _upsert_company(session, lead.company)
                if company_row._is_new:
                    saved_companies += 1

                dm_row = await _upsert_decision_maker(session, company_row, lead.decision_maker)
                if dm_row._is_new:
                    saved_dms += 1

                lead_row = await _upsert_lead(session, company_row.id, dm_row.id, lead)
                if lead_row._is_new:
                    saved_leads += 1

            await session.commit()

        logger.info(
            "DB saved: {} new companies, {} new contacts, {} new leads",
            saved_companies, saved_dms, saved_leads,
        )

    async def get_by_id(self, lead_id: UUID) -> Lead | None:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(LeadRow).where(LeadRow.id == lead_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return await _row_to_lead(session, row)

    async def find_by_status(self, status: LeadStatus) -> AsyncIterator[Lead]:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(LeadRow).where(LeadRow.status == status.value)
            )
            for row in result.scalars():
                yield await _row_to_lead(session, row)

    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Lead]:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(LeadRow).where(LeadRow.relevance_score >= min_score)
            )
            for row in result.scalars():
                yield await _row_to_lead(session, row)

    async def update_status(self, lead_id: UUID, status: LeadStatus) -> None:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(select(LeadRow).where(LeadRow.id == lead_id))
            row = result.scalar_one_or_none()
            if row:
                row.status = status.value
                row.updated_at = datetime.utcnow()
                await session.commit()

    async def count(self) -> int:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(select(func.count(LeadRow.id)))
            return result.scalar() or 0


# --- helpers ---

async def _upsert_company(session, company: Company) -> CompanyRow:
    result = await session.execute(select(CompanyRow).where(CompanyRow.name == company.name))
    row = result.scalar_one_or_none()

    tech_str = ", ".join(sorted(company.tech_stack.technologies))

    if row:
        # update existing - refresh fields that may change
        row.website = company.website or row.website
        row.description = company.description or row.description
        row.tech_stack = tech_str or row.tech_stack
        row.location = company.location or row.location
        row.is_hiring = company.is_hiring or row.is_hiring
        row.source_url = company.source_url or row.source_url
        row.last_seen_at = datetime.utcnow()
        row._is_new = False
    else:
        row = CompanyRow(
            name=company.name,
            website=company.website,
            description=company.description,
            tech_stack=tech_str or None,
            headcount=company.headcount,
            location=company.location,
            is_hiring=company.is_hiring,
            source=company.source,
            source_url=company.source_url,
        )
        session.add(row)
        await session.flush()
        row._is_new = True

    return row


async def _upsert_decision_maker(session, company_row: CompanyRow, dm: DecisionMaker) -> DecisionMakerRow:
    if dm.full_name == "Unknown (find manually)":
        # skip placeholder - don't pollute DB
        # but we need to return something usable; create or reuse a marker row
        pass

    result = await session.execute(
        select(DecisionMakerRow).where(
            DecisionMakerRow.company_id == company_row.id,
            DecisionMakerRow.full_name == dm.full_name,
        )
    )
    row = result.scalar_one_or_none()

    if row:
        # update if we have better data now
        row.title_raw = dm.title_raw or row.title_raw
        row.email = (str(dm.email) if dm.email else None) or row.email
        row.linkedin_url = (str(dm.linkedin_url) if dm.linkedin_url else None) or row.linkedin_url
        row.twitter_handle = dm.twitter_handle or row.twitter_handle
        row.last_seen_at = datetime.utcnow()
        row._is_new = False
    else:
        row = DecisionMakerRow(
            company_id=company_row.id,
            full_name=dm.full_name,
            role=dm.role.value,
            title_raw=dm.title_raw,
            email=str(dm.email) if dm.email else None,
            linkedin_url=str(dm.linkedin_url) if dm.linkedin_url else None,
            twitter_handle=dm.twitter_handle,
            location=dm.location,
        )
        session.add(row)
        await session.flush()
        row._is_new = True

    return row


async def _upsert_lead(session, company_id: UUID, dm_id: UUID, lead: Lead) -> LeadRow:
    result = await session.execute(
        select(LeadRow).where(
            LeadRow.company_id == company_id,
            LeadRow.decision_maker_id == dm_id,
        )
    )
    row = result.scalar_one_or_none()

    if row:
        # update score if higher
        if lead.relevance_score > row.relevance_score:
            row.relevance_score = lead.relevance_score
        row.updated_at = datetime.utcnow()
        row._is_new = False
    else:
        row = LeadRow(
            company_id=company_id,
            decision_maker_id=dm_id,
            relevance_score=lead.relevance_score,
            status=lead.status.value,
            notes=lead.notes,
        )
        session.add(row)
        await session.flush()
        row._is_new = True

    return row


async def _row_to_lead(session, row: LeadRow) -> Lead:
    """Hydrate Lead domain object from ORM row."""
    comp_result = await session.execute(select(CompanyRow).where(CompanyRow.id == row.company_id))
    comp_row = comp_result.scalar_one()
    dm_result = await session.execute(select(DecisionMakerRow).where(DecisionMakerRow.id == row.decision_maker_id))
    dm_row = dm_result.scalar_one()

    company = Company(
        name=comp_row.name,
        website=comp_row.website,
        tech_stack=TechStack.from_strings(*(comp_row.tech_stack or "").split(", ")),
        headcount=comp_row.headcount,
        location=comp_row.location,
        is_hiring=comp_row.is_hiring,
        source=comp_row.source,
        source_url=comp_row.source_url,
    )

    email = None
    if dm_row.email:
        try:
            email = Email(dm_row.email)
        except ValueError:
            pass

    linkedin = None
    if dm_row.linkedin_url:
        try:
            linkedin = LinkedInUrl(dm_row.linkedin_url)
        except ValueError:
            pass

    try:
        role = DecisionMakerRole(dm_row.role)
    except ValueError:
        role = DecisionMakerRole.OTHER

    dm = DecisionMaker(
        full_name=dm_row.full_name,
        role=role,
        company_id=comp_row.id,
        title_raw=dm_row.title_raw,
        email=email,
        linkedin_url=linkedin,
        twitter_handle=dm_row.twitter_handle,
        location=dm_row.location,
    )

    return Lead(
        company=company,
        decision_maker=dm,
        relevance_score=row.relevance_score,
        status=LeadStatus(row.status) if row.status in [s.value for s in LeadStatus] else LeadStatus.NEW,
        notes=row.notes,
    )
