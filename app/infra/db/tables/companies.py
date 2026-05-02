"""SQLAlchemy tables for the companies domain (CompanyRow, JobPostingRow).

Lives in infra/db/tables/ (not in modules/companies/adapters/) because the
schema is shared infrastructure: foreign keys cross modules and the Alembic
autogenerate diff has to see one coherent metadata. Modules' repository
adapters import these classes from here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.engine import Base

if TYPE_CHECKING:
    from app.infra.db.tables.people import DecisionMakerRow


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
    last_dm_scan_at: Mapped[datetime | None]

    decision_makers: Mapped[list["DecisionMakerRow"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
    )

    def is_dm_data_fresh(self, max_age_days: int = 30) -> bool:
        if self.last_dm_scan_at is None:
            return False
        return (datetime.utcnow() - self.last_dm_scan_at) < timedelta(days=max_age_days)


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

    applicants_count: Mapped[int | None]
    posted_at: Mapped[datetime | None]
    apply_email: Mapped[str | None] = mapped_column(String(200))
