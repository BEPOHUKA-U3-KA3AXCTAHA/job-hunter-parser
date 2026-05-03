"""SQLAlchemy-backed Unit of Work for the companies module."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db import get_session_maker
from app.modules.companies.adapters.company_directory.sqla import SqlaCompanyDirectory
from app.modules.companies.ports.companies_uow import CompaniesUoW


class SqlaCompaniesUoW(CompaniesUoW):
    """Implements `app.modules.companies.ports.companies_uow.CompaniesUoW`."""

    _session: AsyncSession | None

    def __init__(self) -> None:
        self._session = None

    async def __aenter__(self) -> SqlaCompaniesUoW:
        self._session = get_session_maker()()
        self.companies = SqlaCompanyDirectory(self._session)
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
