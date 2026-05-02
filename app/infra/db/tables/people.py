"""SQLAlchemy ORM table for the people domain (DecisionMakerRow)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.engine import Base

if TYPE_CHECKING:
    from app.infra.db.tables.applies import ApplyRow
    from app.infra.db.tables.companies import CompanyRow


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
    contacts: Mapped[dict] = mapped_column(JSON, default=dict)

    company: Mapped["CompanyRow"] = relationship(back_populates="decision_makers")
    applies: Mapped[list["ApplyRow"]] = relationship(back_populates="decision_maker")
