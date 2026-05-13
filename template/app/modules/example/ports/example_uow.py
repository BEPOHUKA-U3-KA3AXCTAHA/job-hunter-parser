"""Unit of Work port for the example module.

Naming convention: file name MUST end with `_uow` — that's what
scripts/lint_arch.py rule 7 uses to whitelist session/commit calls in
the matching `adapters/<name>_uow/` folder.

The UoW holds repositories as attributes and owns the session
lifecycle. Services type-hint against THIS Protocol, not the concrete
SqlaExampleUoW — keeps the application layer infra-blind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.modules.example.ports.item_journal import ItemJournalRepository


@runtime_checkable
class ExampleUoW(Protocol):
    """Unit of Work — Cosmic Python pattern.

    Usage in service code:

        async def some_use_case(uow: ExampleUoW) -> None:
            async with uow:
                await uow.items.create(name="foo")
                await uow.commit()        # required — default on exit is rollback

    The async-context-manager block scopes one session.
    `commit()` must be called explicitly; an exception OR a missing
    commit triggers rollback. This is the "explicit is safer than
    implicit" choice from the Cosmic Python book.
    """

    items: ItemJournalRepository

    async def __aenter__(self) -> ExampleUoW: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
