"""Shared SQLAlchemy declarative base.

Every table in this project subclasses `Base`. Keeps `Base.metadata`
singular so Alembic autogenerate picks up everything in one pass.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
