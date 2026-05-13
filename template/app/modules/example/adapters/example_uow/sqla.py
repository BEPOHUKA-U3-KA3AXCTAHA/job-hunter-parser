"""SQLAlchemy Unit of Work — the ONE place sessions/commits/rollbacks
are allowed for the `example` module (rule 7).

The folder name `example_uow` MUST match the port file name
`ports/example_uow.py` (rule 3).

Default behavior on `__aexit__` is ROLLBACK. Calling code must
explicitly `await uow.commit()` to persist. This protects against
"oops I returned early and the changes I made got committed anyway".
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import get_session_maker
from app.modules.example.adapters.item_journal.sqla import SqlaItemJournalRepository
from app.modules.example.ports.example_uow import ExampleUoW


class SqlaExampleUoW(ExampleUoW):
    """Concrete UoW. Wire any additional repositories here."""

    _session: AsyncSession | None

    def __init__(self) -> None:
        self._session = None

    async def __aenter__(self) -> SqlaExampleUoW:
        self._session = get_session_maker()()
        self.items = SqlaItemJournalRepository(self._session)
        # Wire more repositories here as the module grows.
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
            raise RuntimeError("UoW.commit() called outside `async with` scope")
        await self._session.commit()

    async def rollback(self) -> None:
        if self._session is None:
            return
        await self._session.rollback()
