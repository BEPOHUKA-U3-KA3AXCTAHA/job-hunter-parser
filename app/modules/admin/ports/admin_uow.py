"""Unit of Work + repository port for the admin module.

Cross-table diagnostic queries (db-status, dump-companies, etc) and the
local API server's stats/result endpoints. Repositories of other modules
target a single aggregate root each; the admin repo cuts across all of
them, so it lives in its own module rather than bloating any one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.admin.models import (
    CompanyDump,
    DbStatus,
    JobDump,
    PersonDump,
    StaleCompany,
)


@runtime_checkable
class AdminRepository(Protocol):
    """Read-mostly cross-table queries needed by CLI admin commands and
    the local HTTP API."""

    async def db_status(self) -> DbStatus: ...

    async def list_companies(
        self, limit: int, hiring_only: bool = False,
    ) -> list[CompanyDump]: ...

    async def list_people(self, limit: int) -> list[PersonDump]: ...

    async def list_jobs(self, limit: int) -> list[JobDump]: ...

    async def stale_companies(
        self, max_age_days: int, limit: int = 50,
    ) -> list[StaleCompany]: ...

    # ---- Writes (used by the API result endpoint to record an apply
    #      attempt as one atomic op across companies + people + applies)

    async def record_external_apply(
        self,
        company_name: str,
        job_url: str,
        job_title: str,
        channel: str,
        outcome: str,
        detail: str,
    ) -> None: ...


@runtime_checkable
class AdminUoW(Protocol):
    admin: AdminRepository

    async def __aenter__(self) -> AdminUoW: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
