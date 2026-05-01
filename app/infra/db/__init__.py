"""DB infra — single source of truth for engine/session/Base.

Modules import Base to declare their ORM tables. Entrypoints import
get_session_maker / transaction / init_db to do work.
"""
from app.infra.db.engine import (
    Base,
    database_url,
    describe_db,
    get_engine,
    get_session_maker,
    init_db,
    transaction,
)

__all__ = [
    "Base",
    "database_url",
    "describe_db",
    "get_engine",
    "get_session_maker",
    "init_db",
    "transaction",
]
