"""SQLAlchemy-backed Unit of Work for the applies module.

Cosmic Python pattern (Chapter 6). The UoW owns the AsyncSession lifecycle:
- `__aenter__` opens a session and instantiates each repository with it.
- `__aexit__` rolls back by default — services must call `commit()`
  explicitly. This is intentional: silent commits are a footgun.

Per architecture rule 7: sessions, commits, and rollbacks live ONLY in
files like this one (under adapters/unit_of_work/). Repositories receive
a session in __init__ and use it directly; they never open or close it.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import get_session_maker
from app.modules.applies.adapters.repository.candidates import (
    SqlaCandidateBundleRepository,
)
from app.modules.applies.adapters.repository.mass_apply import (
    SqlaMassApplyRepository,
)
from app.modules.applies.adapters.repository.qa_cache import (
    SqlaQACacheRepository,
)
from app.modules.applies.adapters.repository.sqla import SqliteApplyRepository


class SqlaUnitOfWork:
    """Implements `app.modules.applies.ports.unit_of_work.UnitOfWork`."""

    _session: AsyncSession | None

    def __init__(self) -> None:
        self._session = None

    async def __aenter__(self) -> SqlaUnitOfWork:
        self._session = get_session_maker()()
        # Wire each repo to share THIS session — that's how multi-repo
        # operations end up in one transaction.
        self.apply = SqliteApplyRepository(self._session)
        self.mass_apply = SqlaMassApplyRepository(self._session)
        self.candidates = SqlaCandidateBundleRepository(self._session)
        self.qa_cache = SqlaQACacheRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        try:
            await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UoW.commit() outside of `async with` scope")
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is None:
            return
        await self._session.rollback()
