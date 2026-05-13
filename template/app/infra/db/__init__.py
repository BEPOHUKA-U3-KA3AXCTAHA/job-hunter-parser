"""SQLAlchemy async engine + session factory.

Public surface: `get_engine()`, `get_session_maker()`. These are the
ONLY symbols anyone outside this package should touch — and even those
should only be used inside `*_uow/` adapter folders (rule 7).
"""

from app.infra.db.engine import get_engine, get_session_maker

__all__ = ["get_engine", "get_session_maker"]
