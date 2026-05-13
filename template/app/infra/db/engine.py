"""Async SQLAlchemy engine + session maker.

Both are lazy-cached: the engine is built on first call so importing
this module is side-effect-free.

Config via `DATABASE_URL` env var:
    - empty/unset → `sqlite+aiosqlite:///app.db`
    - production: `postgresql+asyncpg://user:pw@host/dbname`
"""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine, async_sessionmaker, create_async_engine,
)


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite+aiosqlite:///app.db"
    return url


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Build (once) and return the process-wide async engine."""
    return create_async_engine(_db_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_maker() -> async_sessionmaker:
    """Process-wide session factory. UoW adapters call this to mint a
    fresh session per `async with uow:` block.

    Reminder (rule 7): only `*_uow/` adapter files may call this. If
    you find yourself reaching for it elsewhere, you're bypassing the
    UoW and should refactor.
    """
    return async_sessionmaker(get_engine(), expire_on_commit=False)
