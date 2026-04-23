"""SQLAlchemy ORM models + async engine for SQLite persistence."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
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
    tech_stack: Mapped[str | None] = mapped_column(String(500))  # comma-separated
    headcount: Mapped[int | None]
    location: Mapped[str | None] = mapped_column(String(200))
    is_hiring: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(String(500))

    discovered_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

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
    source: Mapped[str | None] = mapped_column(String(50))  # theorg / apollo / apify

    discovered_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[CompanyRow] = relationship(back_populates="decision_makers")
    leads: Mapped[list[LeadRow]] = relationship(back_populates="decision_maker")


class LeadRow(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("company_id", "decision_maker_id", name="uq_lead_company_dm"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    decision_maker_id: Mapped[UUID] = mapped_column(ForeignKey("decision_makers.id"), index=True)

    relevance_score: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(30), default="new")
    notes: Mapped[str] = mapped_column(String(2000), default="")

    generated_message: Mapped[str | None] = mapped_column(String(4000))
    generated_message_channel: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    decision_maker: Mapped[DecisionMakerRow] = relationship(back_populates="leads")


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

    posted_at: Mapped[datetime | None]
    discovered_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# --- Engine & session factory ---

_DB_PATH = "jhp.db"
_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
