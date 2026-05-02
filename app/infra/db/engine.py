"""Async SQLAlchemy engine, session factory, declarative Base.

Per classfieds-style layout this is the ONE place that knows about the engine.
ORM tables live in `app/modules/<m>/adapters/orm.py` and inherit from `Base` here.

DATABASE_URL conventions (auto-fixed):
- bare `postgres://` → `postgresql+asyncpg://`
- bare `sqlite:///` → `sqlite+aiosqlite:///`
- empty/unset → `sqlite+aiosqlite:///jhp.db`
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base shared across all module adapters/orm.py files."""


def _normalize_db_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "sqlite+aiosqlite:///jhp.db"
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("sqlite:///") and "+aiosqlite" not in url:
        return "sqlite+aiosqlite:///" + url[len("sqlite:///"):]
    return url


def database_url() -> str:
    return _normalize_db_url(os.environ.get("DATABASE_URL", ""))


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url(), echo=False)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    """Open a session, commit on clean exit, rollback on exception."""
    session = get_session_maker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def describe_db() -> str:
    url = database_url()
    if url.startswith("sqlite"):
        return f"SQLite file: {url.split('///')[-1]}"
    return url.split("@")[-1] if "@" in url else url


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


async def init_db() -> None:
    """Apply pending Alembic migrations (`migrate upgrade head`).

    CLI commands and orchestrators call this on startup so a fresh checkout /
    blank database boots with the full schema. Real schema changes belong in
    `python -m app.entrypoints.cli.migrate revision -m "..."`.
    """
    cmd = [sys.executable, "-m", "app.entrypoints.cli.migrate", "upgrade", "head"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=_PROJECT_ROOT,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("migrate upgrade head failed: {}", (err or b"").decode()[:500])
