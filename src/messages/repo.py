"""SQLite-backed MessageRepository using SQLAlchemy async.

Deduplicates by (decision_maker, attempt_no).
Updates last_seen_at / last_seen_at on re-scrape.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select

from src.companies.models import Company
from src.messages.db import (
    CompanyRow,
    DecisionMakerRow,
    JobPostingRow,
    MessageRow,
    get_session_maker,
)
from src.messages.models import Message, MessageChannel, MessageStatus
from src.messages.ports import MessageRepository
from src.people.models import DecisionMaker, DecisionMakerRole
from src.shared import TechStack


class SqliteMessageRepository(MessageRepository):
    """Persists messages in SQLite. Deduplicates on re-scrape.

    Hunt always creates/updates attempt_no=1.
    To re-target someone, use `create_retry()` which bumps attempt_no.
    """

    async def save(self, message: Message) -> None:
        await self.save_many([message])

    async def save_many(self, messages: list[Message]) -> None:
        if not messages:
            return

        Session = get_session_maker()
        saved_companies = 0
        saved_dms = 0
        saved_messages = 0

        async with Session() as session:
            for msg in messages:
                company_row = await _upsert_company(session, msg.company)
                if company_row._is_new:
                    saved_companies += 1

                dm_row = await _upsert_decision_maker(session, company_row, msg.decision_maker)
                if dm_row._is_new:
                    saved_dms += 1

                msg_row = await _upsert_message(session, dm_row.id, msg)
                if msg_row._is_new:
                    saved_messages += 1

            await session.commit()

        logger.info(
            "DB saved: {} new companies, {} new contacts, {} new messages",
            saved_companies, saved_dms, saved_messages,
        )

    async def create_retry(self, dm_id: UUID, score: int = 0) -> MessageRow | None:
        """Create a new attempt for an existing dm. attempt_no = max+1."""
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(MessageRow).where(MessageRow.decision_maker_id == dm_id).order_by(MessageRow.attempt_no.desc())
            )
            existing = result.scalars().first()
            if existing is None:
                return None
            row = MessageRow(
                decision_maker_id=dm_id,
                attempt_no=existing.attempt_no + 1,
                relevance_score=score or existing.relevance_score,
                status="new",
            )
            session.add(row)
            await session.commit()
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
        Session = get_session_maker()
        async with Session() as session:
            comp_result = await session.execute(
                select(CompanyRow).where(CompanyRow.name == company_name)
            )
            comp_row = comp_result.scalar_one_or_none()
            if comp_row is None or comp_row.last_dm_scan_at is None:
                return None
            if comp_row.last_dm_scan_at < cutoff:
                return None

            dm_result = await session.execute(
                select(DecisionMakerRow).where(DecisionMakerRow.company_id == comp_row.id)
            )
            dm_rows = dm_result.scalars().all()
            if not dm_rows:
                return None

            return [_dm_row_to_domain(r) for r in dm_rows]

    async def mark_dm_scan_done(self, company_name: str) -> None:
        """Update company.last_dm_scan_at = now after a successful TheOrg/Apollo scan."""
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(CompanyRow).where(CompanyRow.name == company_name)
            )
            row = result.scalar_one_or_none()
            if row:
                row.last_dm_scan_at = datetime.utcnow()
                await session.commit()

    async def get_by_id(self, message_id: UUID) -> Message | None:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(MessageRow).where(MessageRow.id == message_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return await _row_to_message(session, row)

    async def find_by_status(self, status: MessageStatus) -> AsyncIterator[Message]:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(MessageRow).where(MessageRow.status == status.value)
            )
            for row in result.scalars():
                yield await _row_to_message(session, row)

    async def find_worth_outreach(self, min_score: int = 60) -> AsyncIterator[Message]:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(
                select(MessageRow).where(MessageRow.relevance_score >= min_score)
            )
            for row in result.scalars():
                yield await _row_to_message(session, row)

    async def update_status(self, message_id: UUID, status: MessageStatus) -> None:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(select(MessageRow).where(MessageRow.id == message_id))
            row = result.scalar_one_or_none()
            if row:
                row.status = status.value
                await session.commit()

    async def count(self) -> int:
        Session = get_session_maker()
        async with Session() as session:
            result = await session.execute(select(func.count(MessageRow.id)))
            return result.scalar() or 0

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
        Session = get_session_maker()
        new_count = 0
        # Mutable copy so we can extend with stubs we create
        name_to_id = dict(company_name_to_id)
        async with Session() as session:
            for jp in postings:
                if not jp.source_url:
                    continue
                result = await session.execute(
                    select(JobPostingRow).where(JobPostingRow.source_url == jp.source_url)
                )
                row = result.scalar_one_or_none()
                now = datetime.utcnow()
                tech_str = ", ".join(sorted(jp.tech_stack.technologies)) if jp.tech_stack.technologies else None

                jp_name = getattr(jp, "company_name", "") or ""
                company_id = name_to_id.get(jp_name)
                if company_id is None and jp_name:
                    company_id = await _upsert_company_stub(session, jp_name, jp.source, jp.location)
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
                        first_seen_at=now,
                        last_seen_at=now,
                        is_active=True,
                    )
                    session.add(row)
                    new_count += 1
            await session.commit()
        return new_count


# --- helpers ---

async def _upsert_company_stub(session, name: str, source: str | None, location: str | None) -> UUID:
    """Upsert a minimal CompanyRow when we only know the name (from a job posting).
    Returns the company id. Used by save_job_postings to avoid orphan FKs.
    """
    result = await session.execute(select(CompanyRow).where(CompanyRow.name == name))
    row = result.scalar_one_or_none()
    if row:
        return row.id
    row = CompanyRow(name=name, source=source, location=location, is_hiring=True)
    session.add(row)
    await session.flush()
    return row.id


async def _upsert_company(session, company: Company) -> CompanyRow:
    result = await session.execute(select(CompanyRow).where(CompanyRow.name == company.name))
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
        session.add(row)
        await session.flush()
        row._is_new = True

    return row


async def _upsert_decision_maker(session, company_row: CompanyRow, dm: DecisionMaker) -> DecisionMakerRow:
    result = await session.execute(
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
        session.add(row)
        await session.flush()
        row._is_new = True

    return row


async def _upsert_message(session, dm_id: UUID, msg: Message) -> MessageRow:
    """Upsert message at attempt_no=1, keyed by (job_posting_id, dm_id, attempt_no).

    job_posting_id may be None for bare DM outreach not tied to a specific posting.
    Hunt only ever touches attempt 1; bumps go through `create_retry`.
    """
    jp_id = msg.job_posting.id if msg.job_posting else None
    where = [
        MessageRow.decision_maker_id == dm_id,
        MessageRow.attempt_no == 1,
    ]
    if jp_id is None:
        where.append(MessageRow.job_posting_id.is_(None))
    else:
        where.append(MessageRow.job_posting_id == jp_id)

    result = await session.execute(select(MessageRow).where(*where))
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
        row = MessageRow(
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
        session.add(row)
        await session.flush()
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


async def _row_to_message(session, row: MessageRow) -> Message:
    dm_result = await session.execute(select(DecisionMakerRow).where(DecisionMakerRow.id == row.decision_maker_id))
    dm_row = dm_result.scalar_one()
    comp_result = await session.execute(select(CompanyRow).where(CompanyRow.id == dm_row.company_id))
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
