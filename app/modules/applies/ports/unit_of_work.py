"""Unit of Work port for the applies module.

Cosmic Python's UoW pattern: holds the repository contracts as attributes,
provides a single transactional scope for a service-layer business
operation. Default behavior on `__aexit__` is rollback — services MUST
call `await uow.commit()` explicitly.

Usage in services:

    async def record_outcome(uow: UnitOfWork, ...):
        async with uow:
            await uow.mass_apply.upsert_apply(...)
            await uow.mass_apply.mark_apply_sent(...)
            await uow.commit()        # explicit; default would rollback

Adapters live under `adapters/unit_of_work/<impl>.py` (rule 3 — adapter
folder mirrors port name).
"""
from __future__ import annotations

from typing import Protocol

from app.modules.applies.ports.candidates import CandidateBundleRepository
from app.modules.applies.ports.mass_apply import MassApplyRepository
from app.modules.applies.ports.qa_cache import QACacheRepository
from app.modules.applies.ports.repository import ApplyRepository


class UnitOfWork(Protocol):
    """Transactional boundary for one applies-module business operation."""

    apply: ApplyRepository
    mass_apply: MassApplyRepository
    candidates: CandidateBundleRepository
    qa_cache: QACacheRepository

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
