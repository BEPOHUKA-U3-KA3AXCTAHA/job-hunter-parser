"""SQLAlchemy ORM models + async engine.

Supports both SQLite (default, zero config) and PostgreSQL via DATABASE_URL env.

SQLite:     DATABASE_URL=sqlite+aiosqlite:///jhp.db  (default)
PostgreSQL: DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/jhp
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(2000))
    tech_stack: Mapped[str | None] = mapped_column(String(500))
    headcount: Mapped[int | None]
    location: Mapped[str | None] = mapped_column(String(200))
    is_hiring: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(String(500))

    # Only date that drives logic: when we last hit TheOrg/Apollo for this company's DMs.
    # Used by freshness cache to skip re-enrichment within skip_fresh_days window.
    last_dm_scan_at: Mapped[datetime | None]

    decision_makers: Mapped[list[DecisionMakerRow]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    def is_dm_data_fresh(self, max_age_days: int = 30) -> bool:
        if self.last_dm_scan_at is None:
            return False
        return (datetime.utcnow() - self.last_dm_scan_at) < timedelta(days=max_age_days)


class DecisionMakerRow(Base):
    __tablename__ = "decision_makers"
    __table_args__ = (UniqueConstraint("company_id", "full_name", name="uq_dm_company_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)

    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))
    title_raw: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(50))

    # All contact channels in one JSON field. Easily extensible without schema migrations.
    # Example: {"email": "x@y.com", "linkedin": "https://...", "twitter": "@handle",
    #           "github": "ghuser", "website": "https://...", "phone": "+1..."}
    contacts: Mapped[dict] = mapped_column(JSON, default=dict)

    # No dates on dm — freshness is tracked at company level via last_dm_scan_at.
    company: Mapped[CompanyRow] = relationship(back_populates="decision_makers")
    messages: Mapped[list[MessageRow]] = relationship(back_populates="decision_maker")


class MessageRow(Base):
    """One outreach attempt per (job_posting, decision_maker, attempt_no).

    A DM can be reached for multiple postings (different roles at the same company),
    and a posting can be pitched to multiple DMs (CEO + CTO + Hiring Manager).
    Bump attempt_no via `jhp retry` to re-target the same (job, dm) pair.

    Bare DM-targeted outreach (not tied to a specific posting) leaves
    job_posting_id NULL — the unique key still allows attempt_no bumps.
    """
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "job_posting_id", "decision_maker_id", "attempt_no",
            name="uq_message_job_dm_attempt",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_posting_id: Mapped[UUID | None] = mapped_column(ForeignKey("job_postings.id"), index=True)
    decision_maker_id: Mapped[UUID] = mapped_column(ForeignKey("decision_makers.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(default=1)

    relevance_score: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(30), default="new")
    notes: Mapped[str] = mapped_column(String(2000), default="")

    # Body of the message — generated once at this attempt
    subject: Mapped[str | None] = mapped_column(String(500))   # email only
    body: Mapped[str | None] = mapped_column(String(4000))
    channel: Mapped[str | None] = mapped_column(String(20))
    generated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)  # the only date

    decision_maker: Mapped[DecisionMakerRow] = relationship(back_populates="messages")


class JobPostingRow(Base):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("source_url", name="uq_jp_source_url"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), index=True)

    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(5000))
    tech_stack: Mapped[str | None] = mapped_column(String(500))
    seniority: Mapped[str | None] = mapped_column(String(30))
    is_remote: Mapped[bool] = mapped_column(default=False)
    location: Mapped[str | None] = mapped_column(String(200))
    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    salary_currency: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(String(500), index=True)

    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Competition signals from the source page (best effort, may be NULL)
    applicants_count: Mapped[int | None]
    posted_at: Mapped[datetime | None]


# --- Engine & session factory ---

def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite:///") and "+aiosqlite" not in url:
            url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return url
    return "sqlite+aiosqlite:///jhp.db"


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(_get_database_url(), echo=False)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """Create tables if missing, then auto-migrate columns + unique constraints.

    Order matters: drop tables with stale uniques FIRST (so create_all rebuilds them),
    then run column reconciliation on what remains.
    Caveat: stale-unique drop wipes data in that table — only safe for tables we
    know are empty or transient (e.g. messages, before bodies were ever generated).
    """
    async with get_engine().begin() as conn:
        await conn.run_sync(_drop_stale_unique_tables)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sync_columns)


def _drop_stale_unique_tables(sync_conn) -> None:
    """If the live unique constraints don't match the model, drop the table.

    SQLite can't ALTER constraints, and recreating with new uniques while keeping
    data is hairy. We only do this for tables that are safe to wipe:
    currently `messages` (outreach attempts get regenerated anyway).
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    SAFE_TO_WIPE = {"messages"}
    inspector = inspect(sync_conn)

    for table_name, table in Base.metadata.tables.items():
        if table_name not in SAFE_TO_WIPE or not inspector.has_table(table_name):
            continue

        live_uniques = {
            tuple(sorted(u["column_names"]))
            for u in inspector.get_unique_constraints(table_name)
        }
        model_uniques = {
            tuple(sorted(c.name for c in constraint.columns))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        if live_uniques != model_uniques:
            row_count = sync_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            logger.warning(
                "Auto-migration: stale unique on {} (live={}, model={}). Dropping ({} rows).",
                table_name, live_uniques, model_uniques, row_count,
            )
            sync_conn.execute(text(f"DROP TABLE {table_name}"))


def _sync_columns(sync_conn) -> None:
    """Diff model schema vs live DB schema. Add missing columns, drop extra ones.

    Uses sqlalchemy.inspect for the comparison.
    SQLite 3.35+ supports both ADD COLUMN and DROP COLUMN without data loss.
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue  # create_all() already built it

        model_cols = {col.name: col for col in table.columns}
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}

        # 1. ADD COLUMN for new fields in the model
        for name, col in model_cols.items():
            if name in existing_cols:
                continue

            col_type = col.type.compile(dialect=sync_conn.dialect)
            nullable = "" if col.nullable else " NOT NULL DEFAULT ''"
            default = ""
            if col.default is not None and col.default.is_scalar:
                val = col.default.arg
                if isinstance(val, str):
                    default = f" DEFAULT '{val}'"
                elif isinstance(val, (int, float, bool)):
                    default = f" DEFAULT {int(val) if isinstance(val, bool) else val}"

            stmt = f'ALTER TABLE {table_name} ADD COLUMN "{name}" {col_type}{default}{nullable}'
            try:
                sync_conn.execute(text(stmt))
                logger.info("Auto-migration: added {}.{} ({})", table_name, name, col_type)
            except Exception as e:
                logger.warning("Auto-migration ADD failed for {}.{}: {}", table_name, name, e)

        # 2. DROP COLUMN for columns that are no longer in the model.
        # SQLite refuses to drop a column if it's part of an index or PK -
        # those will fail safely with a warning and be left in place.
        for name in existing_cols - set(model_cols.keys()):
            stmt = f'ALTER TABLE {table_name} DROP COLUMN "{name}"'
            try:
                sync_conn.execute(text(stmt))
                logger.info("Auto-migration: dropped {}.{}", table_name, name)
            except Exception as e:
                logger.warning("Auto-migration DROP failed for {}.{}: {}", table_name, name, e)


def describe_db() -> str:
    url = _get_database_url()
    if url.startswith("sqlite"):
        return f"SQLite file: {url.split('///')[-1]}"
    return url.split("@")[-1] if "@" in url else url
