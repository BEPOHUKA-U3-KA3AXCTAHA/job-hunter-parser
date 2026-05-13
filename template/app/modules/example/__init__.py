"""Example module — copy this folder when scaffolding a new domain.

This module demonstrates the conventions enforced by the linters:

  ports/                         — Protocol interfaces (no impl, no infra)
    example_uow.py               — UoW Protocol (suffix `_uow` is REQUIRED)
    item_journal.py              — Repository Protocol

  adapters/                      — concrete impls (folder name = port file stem)
    example_uow/sqla.py          — SqlaExampleUoW (only place sessions are made)
    item_journal/sqla.py         — SqlaItemJournalRepository

  models/                        — domain objects (DTOs, value objects)
    item.py

  services/                      — use cases (orchestrate ports + commit)
    create_item.py

  __init__.py (this file)        — public surface of the module

External code imports the module's public symbols VIA __init__ ONLY.
Reaching into `app.modules.example.adapters.X` from outside is a
rule-4 violation (caught by scripts/lint_arch.py).
"""
from app.modules.example.models.item import Item
from app.modules.example.ports.example_uow import ExampleUoW
from app.modules.example.ports.item_journal import ItemJournalRepository
from app.modules.example.services.create_item import create_item


def default_uow() -> ExampleUoW:
    """Composition root — bind the Protocol to its SQLA implementation.

    Lazy-imports the adapter to keep `app.modules.example` itself
    infra-clean: a test that imports the public surface doesn't pull
    SQLAlchemy into its dependency graph until it actually needs a UoW.
    """
    from app.modules.example.adapters.example_uow.sqla import SqlaExampleUoW
    return SqlaExampleUoW()


__all__ = [
    "Item",
    "ExampleUoW",
    "ItemJournalRepository",
    "create_item",
    "default_uow",
]
