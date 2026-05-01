"""Alembic env — async-aware, DB URL from DATABASE_URL env, target_metadata from app.modules.applies.db.Base.

When the modular refactor lands, swap the import to `from app.modules.messages import Base`
(or wherever the consolidated metadata ends up).
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Single shared Base — modules register tables by importing it
from app.infra.db import Base

# Import every module's ORM module so its tables are registered with Base.metadata
# before Alembic compares the model schema against the live DB.
import app.modules.applies.adapters.orm  # noqa: F401  ApplyRow
import app.modules.companies.adapters.orm  # noqa: F401  CompanyRow, JobPostingRow
import app.modules.people.adapters.orm  # noqa: F401  DecisionMakerRow

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Pull DB URL from env, fallback to local SQLite (dev default)
db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///jhp.db")
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without an Engine — emits SQL to stdout."""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=db_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=db_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
