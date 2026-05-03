"""Unit of Work port for the applies module.

Cosmic Python's UoW pattern: holds the repository contracts as attributes,
provides a single transactional scope for a service-layer business
operation. Default behavior on `__aexit__` is rollback — services MUST
call `await uow.commit()` explicitly.

Naming convention: port files exposing a UoW Protocol END WITH `_uow.py`
so the linter (rule 7) can pin sessions/commits to the matching adapter
folder (`adapters/<port_stem>/<impl>.py`, here `adapters/applies_uow/`).

Usage in services:

    async def record_outcome(uow: AppliesUoW, ...):
        async with uow:
            await uow.mass_apply.upsert_apply(...)
            await uow.mass_apply.mark_apply_sent(...)
            await uow.commit()        # explicit; default would rollback
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.applies.ports.candidates import CandidateBundlesRepository
from app.modules.applies.ports.mass_apply import MassApplyJournalRepository
from app.modules.applies.ports.qa_cache import QACache
from app.modules.applies.ports.apply_journal import ApplyJournalRepository


@runtime_checkable
class AppliesUoW(Protocol):
    """Transactional boundary for one applies-module business operation."""

    apply: ApplyJournalRepository
    mass_apply: MassApplyJournalRepository
    candidates: CandidateBundlesRepository
    qa_cache: QACache

    async def __aenter__(self) -> AppliesUoW: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
