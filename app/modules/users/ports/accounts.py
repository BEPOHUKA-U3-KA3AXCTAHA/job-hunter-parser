"""Driven port for users persistence."""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.users.models.user import User


@runtime_checkable
class AccountsRepository(Protocol):
    async def get_by_email(self, email: str) -> User | None: ...
    async def get_by_id(self, user_id: UUID) -> User | None: ...
    async def get_default(self) -> User | None:
        """Return the (currently single) user, or None if the table is empty."""
        ...
    async def upsert(self, user: User) -> None:
        """Insert or update by email."""
        ...
    async def update_info(self, email: str, info: str) -> None: ...
