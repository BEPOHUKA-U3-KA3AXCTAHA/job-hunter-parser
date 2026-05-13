"""Repository Protocol for Items.

Naming convention: domain-meaningful suffix (`Journal`, `Directory`,
`Accounts`, `Bundles`) plus `Repository` to mark it as a DB-backed
collection. NEVER bare `Repository` — different domains may have
multiple repositories.

The adapter folder MUST match this file's stem:
    ports/item_journal.py  ↔  adapters/item_journal/<impl>.py
Enforced by scripts/lint_arch.py rule 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.modules.example.models.item import Item


@runtime_checkable
class ItemJournalRepository(Protocol):
    """Read + write port for Items.

    Implementations must inherit this Protocol (rule 3 verifies via AST).
    They MUST accept a session-like object in `__init__` from the UoW
    and use it directly — never open sessions or commit themselves
    (rule 7).
    """

    async def get(self, item_id: int) -> Item | None: ...

    async def create(self, name: str, description: str = "") -> Item: ...

    async def list_all(self) -> list[Item]: ...
