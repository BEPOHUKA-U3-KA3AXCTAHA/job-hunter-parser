"""SQLAlchemy implementation of ItemJournalRepository.

This adapter:
  - takes a session in __init__ from the UoW (rule 7)
  - never commits, rolls back, or closes the session (rule 7)
  - MUST inherit from `ItemJournalRepository` Protocol (rule 3)

To wire a real table, define `ItemRow` in
`app/infra/db/tables/item.py` and replace the stub queries below.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.example.models.item import Item
from app.modules.example.ports.item_journal import ItemJournalRepository


class SqlaItemJournalRepository(ItemJournalRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_id: int) -> Item | None:
        # Replace with a real SELECT once you define the table:
        # row = (await self._session.execute(
        #     select(ItemRow).where(ItemRow.id == item_id)
        # )).scalar_one_or_none()
        # return Item(id=row.id, name=row.name, description=row.description) if row else None
        raise NotImplementedError("define your ItemRow table + uncomment the query")

    async def create(self, name: str, description: str = "") -> Item:
        # row = ItemRow(name=name, description=description)
        # self._session.add(row)
        # await self._session.flush()  # populate row.id without commit
        # return Item(id=row.id, name=row.name, description=row.description)
        raise NotImplementedError

    async def list_all(self) -> list[Item]:
        # rows = (await self._session.execute(select(ItemRow))).scalars().all()
        # return [Item(id=r.id, name=r.name, description=r.description) for r in rows]
        raise NotImplementedError
