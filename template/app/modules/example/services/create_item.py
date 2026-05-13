"""Example use case showing the Cosmic-Python UoW pattern.

Note this file:
  - imports ONLY ports + models (no infra, no SQLA, no adapters)
  - takes the UoW Protocol, not a concrete class — injectable for tests
  - explicitly awaits `uow.commit()` (default on exit = rollback)
  - has ≤3 args per the rule-8 budget
"""
from __future__ import annotations

from loguru import logger

from app.modules.example.models.item import Item
from app.modules.example.ports.example_uow import ExampleUoW


async def create_item(uow: ExampleUoW, name: str) -> Item:
    """Create an item with the given name + persist."""
    async with uow:
        item = await uow.items.create(name=name)
        await uow.commit()
        logger.info("created item id={} name={!r}", item.id, item.name)
        return item
