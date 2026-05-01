"""DEPRECATED — transitional re-export shim.

History: this file used to define ALL ORM tables + the engine for the project.
The classfieds-style modular refactor split them per-module:

- Base, get_engine, get_session_maker, transaction → app.infra.db
- CompanyRow, JobPostingRow                       → app.modules.companies.adapters.orm
- DecisionMakerRow                                → app.modules.people.adapters.orm
- ApplyRow                                        → app.modules.applies.adapters.orm

The old auto-migration init_db() is replaced by Alembic
(`alembic -c app/infra/db/alembic.ini upgrade head`). It's kept here as a
no-op compatibility wrapper that defers to alembic, so legacy callers like
`await init_db()` continue to work during the transition.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from loguru import logger

# Re-export Base, engine helpers, ORM tables for legacy `from src.messages.db import X`
from app.infra.db import (  # noqa: F401
    Base,
    database_url,
    describe_db,
    get_engine,
    get_session_maker,
    transaction,
)
from app.modules.applies.adapters.orm import ApplyRow  # noqa: F401
from app.modules.companies.adapters.orm import CompanyRow, JobPostingRow  # noqa: F401
from app.modules.people.adapters.orm import DecisionMakerRow  # noqa: F401


_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "app" / "infra" / "db" / "alembic.ini"


async def init_db() -> None:
    """Compat: run `alembic upgrade head` instead of the old auto-migration.

    Legacy callers (`await init_db()` from CLI / orchestrators) still work.
    Real schema changes belong in `alembic revision --autogenerate -m "..."`.
    """
    cmd = [sys.executable, "-m", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"]
    logger.debug("init_db (compat): {}", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[2],
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("alembic upgrade head failed: {}", (err or b"").decode()[:500])
