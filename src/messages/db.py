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
    """One outreach attempt per (decision_maker, attempt_no).

    To message the same person again: bump attempt_no via `jhp retry`.
    Company is reachable via decision_maker.company_id.
    """
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("decision_maker_id", "attempt_no", name="uq_message_dm_attempt"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
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
    """Create tables if missing, then auto-migrate any newly added columns.

    Lets us evolve models in place without losing data - no reset-db needed for
    additive changes. Removed columns simply become dead columns in the DB
    (SQLite has no DROP COLUMN before 3.35; we don't bother).
    """
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sync_columns)


def _sync_columns(sync_conn) -> None:
    """ALTER TABLE ADD COLUMN for any model column not yet present in the DB.

    Uses sqlalchemy.inspect to compare model schema vs live DB schema.
    Only adds columns - never drops. Safe across all SQLite versions.
    """
    from loguru import logger
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue  # create_all() already handled it

        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue

            # Build ALTER TABLE ADD COLUMN statement.
            col_type = col.type.compile(dialect=sync_conn.dialect)
            nullable = "" if col.nullable else " NOT NULL DEFAULT ''"
            default = ""
            if col.default is not None and col.default.is_scalar:
                val = col.default.arg
                if isinstance(val, str):
                    default = f" DEFAULT '{val}'"
                elif isinstance(val, (int, float, bool)):
                    default = f" DEFAULT {int(val) if isinstance(val, bool) else val}"

            stmt = f'ALTER TABLE {table_name} ADD COLUMN "{col.name}" {col_type}{default}{nullable}'
            try:
                sync_conn.execute(text(stmt))
                logger.info("Auto-migration: added {}.{} ({})", table_name, col.name, col_type)
            except Exception as e:
                logger.warning("Auto-migration failed for {}.{}: {}", table_name, col.name, e)


def describe_db() -> str:
    url = _get_database_url()
    if url.startswith("sqlite"):
        return f"SQLite file: {url.split('///')[-1]}"
    return url.split("@")[-1] if "@" in url else url
