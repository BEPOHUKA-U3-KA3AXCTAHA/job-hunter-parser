"""SQLAlchemy ORM models + async engine.

Supports both SQLite (default, zero config) and PostgreSQL via DATABASE_URL env.

SQLite:     DATABASE_URL=sqlite+aiosqlite:///jhp.db  (default)
PostgreSQL: DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/jhp
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint
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

    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    last_hiring_verified_at: Mapped[datetime | None]

    decision_makers: Mapped[list[DecisionMakerRow]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class DecisionMakerRow(Base):
    __tablename__ = "decision_makers"
    __table_args__ = (UniqueConstraint("company_id", "full_name", name="uq_dm_company_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)

    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))
    title_raw: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    twitter_handle: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(50))

    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)  # last time source confirmed them

    company: Mapped[CompanyRow] = relationship(back_populates="decision_makers")
    messages: Mapped[list[MessageRow]] = relationship(back_populates="decision_maker")

    def is_fresh(self, max_age_days: int = 30) -> bool:
        return (datetime.utcnow() - self.last_seen_at) < timedelta(days=max_age_days)


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
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def describe_db() -> str:
    url = _get_database_url()
    if url.startswith("sqlite"):
        return f"SQLite file: {url.split('///')[-1]}"
    return url.split("@")[-1] if "@" in url else url
