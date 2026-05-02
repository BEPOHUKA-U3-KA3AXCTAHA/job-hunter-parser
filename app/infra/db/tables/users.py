"""SQLAlchemy table for application users.

Single-user mode for now (just Sergey). `password_hash` lives here so that
when registration/login lands later we don't need a schema migration —
just plug auth into the existing column.

`info` is a free-form text field — this is what the LLM sees when
filling forms / generating outreach. Source of truth (real LinkedIn URL,
real Telegram, work-auth status, etc.) — overrides anything parsed from
the resume PDF.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.engine import Base


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)

    # Empty until the registration/login flow lands.
    password_hash: Mapped[str | None] = mapped_column(String(200))

    # Free-form profile text the LLM uses as ground truth when filling
    # ATS forms (LinkedIn URL, Telegram, GitHub, residence/visa status,
    # short bio, salary expectations, …). Edited via `jhp user edit-info`.
    info: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
