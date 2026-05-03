"""Unit of Work port for the users module."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.users.ports.repository import UserRepository


@runtime_checkable
class UsersUoW(Protocol):
    """Transactional boundary for one users-module business operation."""

    users: UserRepository

    async def __aenter__(self) -> UsersUoW: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
