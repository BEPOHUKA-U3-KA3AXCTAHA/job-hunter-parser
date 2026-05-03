"""Unit of Work port for the companies module."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.companies.ports.company_directory import CompanyDirectory


@runtime_checkable
class CompaniesUoW(Protocol):
    companies: CompanyDirectory

    async def __aenter__(self) -> CompaniesUoW: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
