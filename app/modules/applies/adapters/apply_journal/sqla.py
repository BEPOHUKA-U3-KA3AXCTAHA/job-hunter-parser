"""SQLite-backed ApplyJournalRepository using SQLAlchemy async.

Deduplicates by (decision_maker, attempt_no).
Updates last_seen_at / last_seen_at on re-scrape.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.tables.applies import ApplyRow
from app.modules.applies.models import (
    Apply as Message,  # legacy alias used in this file
    ApplyChannel as MessageChannel,
    ApplyStatus as MessageStatus,
)
from app.modules.applies.ports import ApplyJournalRepository
from app.infra.db.tables.companies import CompanyRow, JobPostingRow
from app.modules.companies import Company
from app.infra.db.tables.people import DecisionMakerRow
from app.modules.people import DecisionMaker, DecisionMakerRole
from app.modules.companies import TechStack


class SqlaApplyJournalRepository(ApplyJournalRepository):
    """Persists messages in SQLite. Deduplicates on re-scrape.

    Hunt always creates/updates attempt_no=1.
    To re-target someone, use `create_retry()` which bumps attempt_no.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def save(self, message: Message) -> None:
        await self.save_many([message])

    async def save_many(self, messages: list[Message]) -> None:
        if not messages:
            return

        saved_companies = 0
        saved_dms = 0
        saved_messages = 0

        for msg in messages:
            company_row = await _upsert_company(self._s, msg.company)
            if company_row._is_new:
                saved_companies += 1

            dm_row = await _upsert_decision_maker(self._s, company_row, msg.decision_maker)
            if dm_row._is_new:
                saved_dms += 1

            msg_row = await _upsert_message(self._s, dm_row.id, msg)
            if msg_row._is_new:
                saved_messages += 1
        logger.info(
            "DB saved: {} new companies, {} new contacts, {} new messages",
            saved_companies, saved_dms, saved_messages,
        )

    async def create_retry(self, dm_id: UUID, score: int = 0) -> ApplyRow | None:
        """Create a new attempt for an existing dm. attempt_no = max+1."""
        result = await self._s.execute(
            select(ApplyRow).where(ApplyRow.decision_maker_id == dm_id).order_by(ApplyRow.attempt_no.desc())
        )
        existing = result.scalars().first()
        if existing is None:
            return None
        row = ApplyRow(
            decision_maker_id=dm_id,
            attempt_no=existing.attempt_no + 1,
            relevance_score=score or existing.relevance_score,
            status="new",
        )
        self._s.add(row)
        return row

    async def get_fresh_contacts(
        self, company_name: str, max_age_days: int
    ) -> list[DecisionMaker] | None:
        """Return cached contacts if the company's last_dm_scan_at is within N days.
        Returns None if company not in DB, never scanned, or scan is stale.
        Freshness tracked at company level (not per-dm) — we either scanned the whole
        company recently or we didn't.
        """
        if max_age_days <= 0:
            return None

        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        comp_result = await self._s.execute(
            select(CompanyRow).where(CompanyRow.name == company_name)
        )
        comp_row = comp_result.scalar_one_or_none()
        if comp_row is None or comp_row.last_dm_scan_at is None:
            return None
        if comp_row.last_dm_scan_at < cutoff:
            return None

        dm_result = await self._s.execute(
            select(DecisionMakerRow).where(DecisionMakerRow.company_id == comp_row.id)
        )
        dm_rows = dm_result.scalars().all()
        if not dm_rows:
            return None

        return [_dm_row_to_domain(r) for r in dm_rows]

    async def mark_dm_scan_done(self, company_name: str) -> None:
        """Update company.last_dm_scan_at = now after a successful TheOrg/Apollo scan."""
        result = await self._s.execute(
            select(CompanyRow).where(CompanyRow.name == company_name)
        )
        row = result.scalar_one_or_none()
        if row:
            row.last_dm_scan_at = datetime.utcnow()
    async def get_by_id(self, message_id: UUID) -> Message | None:
        result = await self._s.execute(
            select(ApplyRow).where(ApplyRow.id == message_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return await _row_to_message(self._s, row)

    async def find_by_status(self, status: MessageStatus) -> AsyncIterator[Message]:
        result = await self._s.execute(
            select(ApplyRow).where(ApplyRow.status == status.value)
        )
        for row in result.scalars():
            yield await _row_to_message(self._s, row)

    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Message]:
        result = await self._s.execute(
            select(ApplyRow).where(ApplyRow.relevance_score >= min_score)
        )
        for row in result.scalars():
            yield await _row_to_message(self._s, row)

    async def update_status(self, message_id: UUID, status: MessageStatus) -> None:
        result = await self._s.execute(select(ApplyRow).where(ApplyRow.id == message_id))
        row = result.scalar_one_or_none()
        if row:
            row.status = status.value
    async def count(self) -> int:
        result = await self._s.execute(select(func.count(ApplyRow.id)))
        return result.scalar() or 0

    async def list_by_status(self, status: str, limit: int = 50) -> list[ApplyRow]:
        result = await self._s.execute(
            select(ApplyRow).where(ApplyRow.status == status).limit(limit)
        )
        return list(result.scalars())

    async def upsert_company_with_dm(self, company: Company, dm: DecisionMaker) -> None:
        """Persist a (company, dm) pair without an apply — pipeline path for
        when LLM didn't generate a body but enrichment did happen."""
        comp_row = await _upsert_company(self._s, company)
        await _upsert_decision_maker(self._s, comp_row, dm)

    async def company_name_to_id(self, names: list[str]) -> dict[str, UUID]:
        """Batch-resolve company names → row IDs in one SELECT."""
        if not names:
            return {}
        result = await self._s.execute(
            select(CompanyRow).where(CompanyRow.name.in_(names))
        )
        return {r.name: r.id for r in result.scalars()}

    async def save_job_postings(self, postings: list, company_name_to_id: dict) -> int:
        """Persist job postings, dedup by source_url. Returns count of new rows.

        If a posting's company_name isn't in the lookup map, we upsert a stub
        CompanyRow on the fly so the FK is never NULL. This happens when scrapers'
        fetch_companies / fetch_job_postings filter sets diverge (e.g. salary filter
        on jobs only, or limit_per_source cap drift).
        """
        if not postings:
            return 0
        from datetime import datetime
        new_count = 0
        # Mutable copy so we can extend with stubs we create
        name_to_id = dict(company_name_to_id)
        for jp in postings:
            if not jp.source_url:
                continue
            result = await self._s.execute(
                select(JobPostingRow).where(JobPostingRow.source_url == jp.source_url)
            )
            row = result.scalar_one_or_none()
            now = datetime.utcnow()
            tech_str = ", ".join(sorted(jp.tech_stack.technologies)) if jp.tech_stack.technologies else None

            jp_name = getattr(jp, "company_name", "") or ""
            company_id = name_to_id.get(jp_name)
            if company_id is None and jp_name:
                company_id = await _upsert_company_stub(self._s, jp_name, jp.source, jp.location)
                name_to_id[jp_name] = company_id

            if row:
                row.last_seen_at = now
                row.is_active = True
                if row.company_id is None and company_id:
                    row.company_id = company_id
                # Refresh competition signals if scraper provided new data
                if jp.applicants_count is not None:
                    row.applicants_count = jp.applicants_count
                if jp.posted_at is not None and row.posted_at is None:
                    row.posted_at = jp.posted_at
                if jp.apply_email and not row.apply_email:
                    row.apply_email = jp.apply_email
            else:
                row = JobPostingRow(
                    company_id=company_id,
                    title=jp.title,
                    description=jp.description,
                    tech_stack=tech_str,
                    seniority=jp.seniority.value if jp.seniority else None,
                    is_remote=jp.is_remote,
                    location=jp.location,
                    salary_min=jp.salary_min,
                    salary_max=jp.salary_max,
                    salary_currency=jp.salary_currency,
                    source=jp.source if hasattr(jp, "source") else None,
                    source_url=jp.source_url,
                    applicants_count=jp.applicants_count,
                    posted_at=jp.posted_at,
                    apply_email=jp.apply_email,
                    first_seen_at=now,
                    last_seen_at=now,
                    is_active=True,
                )
                self._s.add(row)
                new_count += 1
        return new_count


# --- helpers ---

async def _upsert_company_stub(session, name: str, source: str | None, location: str | None) -> UUID:
    """Upsert a minimal CompanyRow when we only know the name (from a job posting).
    Returns the company id. Used by save_job_postings to avoid orphan FKs.
    """
    result = await self._s.execute(select(CompanyRow).where(CompanyRow.name == name))
    row = result.scalar_one_or_none()
    if row:
        return row.id
    row = CompanyRow(name=name, source=source, location=location, is_hiring=True)
    self._s.add(row)
    await self._s.flush()
    return row.id


async def _upsert_company(session, company: Company) -> CompanyRow:
    result = await self._s.execute(select(CompanyRow).where(CompanyRow.name == company.name))
    row = result.scalar_one_or_none()

    tech_str = ", ".join(sorted(company.tech_stack.technologies))

    if row:
        row.website = company.website or row.website
        row.description = company.description or row.description
        row.tech_stack = tech_str or row.tech_stack
        row.location = company.location or row.location
        row.is_hiring = company.is_hiring or row.is_hiring
        row.source_url = company.source_url or row.source_url
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
        self._s.add(row)
        await self._s.flush()
        row._is_new = True

    return row


async def _upsert_decision_maker(session, company_row: CompanyRow, dm: DecisionMaker) -> DecisionMakerRow:
    result = await self._s.execute(
        select(DecisionMakerRow).where(
            DecisionMakerRow.company_id == company_row.id,
            DecisionMakerRow.full_name == dm.full_name,
        )
    )
    row = result.scalar_one_or_none()

    if row:
        row.title_raw = dm.title_raw or row.title_raw
        # merge contacts: new dict over old (new wins where present)
        merged = dict(row.contacts or {})
        merged.update(dm.contacts or {})
        row.contacts = merged
        row._is_new = False
    else:
        row = DecisionMakerRow(
            company_id=company_row.id,
            full_name=dm.full_name,
            role=dm.role.value,
            title_raw=dm.title_raw,
            location=dm.location,
            contacts=dict(dm.contacts) if dm.contacts else {},
        )
        self._s.add(row)
        await self._s.flush()
        row._is_new = True

    return row


async def _upsert_message(session, dm_id: UUID, msg: Message) -> ApplyRow:
    """Upsert message at attempt_no=1, keyed by (job_posting_id, dm_id, attempt_no).

    job_posting_id may be None for bare DM outreach not tied to a specific posting.
    Hunt only ever touches attempt 1; bumps go through `create_retry`.
    """
    jp_id = msg.job_posting.id if msg.job_posting else None
    where = [
        ApplyRow.decision_maker_id == dm_id,
        ApplyRow.attempt_no == 1,
    ]
    if jp_id is None:
        where.append(ApplyRow.job_posting_id.is_(None))
    else:
        where.append(ApplyRow.job_posting_id == jp_id)

    result = await self._s.execute(select(ApplyRow).where(*where))
    row = result.scalar_one_or_none()

    if row:
        if msg.relevance_score > row.relevance_score:
            row.relevance_score = msg.relevance_score
        if msg.body and not row.body:  # only set body if not already there
            row.body = msg.body
            row.subject = msg.subject
            row.channel = msg.channel.value if msg.channel else None
            row.generated_at = msg.generated_at
        row._is_new = False
    else:
        row = ApplyRow(
            job_posting_id=jp_id,
            decision_maker_id=dm_id,
            attempt_no=1,
            relevance_score=msg.relevance_score,
            status=msg.status.value,
            notes=msg.notes,
            subject=msg.subject,
            body=msg.body or None,
            channel=msg.channel.value if msg.channel else None,
            generated_at=msg.generated_at,
        )
        self._s.add(row)
        await self._s.flush()
        row._is_new = True

    return row


def _dm_row_to_domain(dm_row: DecisionMakerRow) -> DecisionMaker:
    try:
        role = DecisionMakerRole(dm_row.role)
    except ValueError:
        role = DecisionMakerRole.OTHER

    return DecisionMaker(
        full_name=dm_row.full_name,
        role=role,
        company_id=dm_row.company_id,
        title_raw=dm_row.title_raw,
        location=dm_row.location,
        contacts=dict(dm_row.contacts) if dm_row.contacts else {},
    )


async def _row_to_message(session, row: ApplyRow) -> Message:
    dm_result = await self._s.execute(select(DecisionMakerRow).where(DecisionMakerRow.id == row.decision_maker_id))
    dm_row = dm_result.scalar_one()
    comp_result = await self._s.execute(select(CompanyRow).where(CompanyRow.id == dm_row.company_id))
    comp_row = comp_result.scalar_one()

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

    dm = _dm_row_to_domain(dm_row)

    channel = None
    if row.channel:
        try:
            channel = MessageChannel(row.channel)
        except ValueError:
            pass

    try:
        status = MessageStatus(row.status)
    except ValueError:
        status = MessageStatus.NEW

    return Message(
        decision_maker=dm,
        company=company,
        attempt_no=row.attempt_no,
        relevance_score=row.relevance_score,
        status=status,
        notes=row.notes,
        body=row.body or "",
        subject=row.subject,
        channel=channel,
        generated_at=row.generated_at,
    )
