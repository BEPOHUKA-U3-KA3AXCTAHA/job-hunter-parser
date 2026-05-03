"""SQLAlchemy-backed Unit of Work for the users module."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import get_session_maker
from app.modules.users.adapters.accounts.sqla import SqlaAccountsRepository
from app.modules.users.ports.users_uow import UsersUoW


class SqlaUsersUoW(UsersUoW):
    """Implements `app.modules.users.ports.users_uow.UsersUoW`."""

    _session: AsyncSession | None

    def __init__(self) -> None:
        self._session = None

    async def __aenter__(self) -> SqlaUsersUoW:
        self._session = get_session_maker()()
        self.users = SqlaAccountsRepository(self._session)
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
