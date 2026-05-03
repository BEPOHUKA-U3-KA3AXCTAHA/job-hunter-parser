"""SQLA implementation of UserRepository.

Cosmic-Python style: takes the AsyncSession in __init__ from the UoW.
NEVER opens its own session, NEVER commits — UoW owns the transaction
lifecycle.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.tables.users import UserRow
from app.modules.users.models.user import User
from app.modules.users.ports.repository import UserRepository


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        info=row.info or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlaUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_email(self, email: str) -> User | None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        return _row_to_user(row) if row else None

    async def get_by_id(self, user_id: UUID) -> User | None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.id == user_id))
        ).scalar_one_or_none()
        return _row_to_user(row) if row else None

    async def get_default(self) -> User | None:
        row = (
            await self._s.execute(
                select(UserRow).order_by(UserRow.created_at.asc()).limit(1)
            )
        ).scalar_one_or_none()
        return _row_to_user(row) if row else None

    async def upsert(self, user: User) -> None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.email == user.email))
        ).scalar_one_or_none()
        if row:
            row.info = user.info
            if user.password_hash:
                row.password_hash = user.password_hash
        else:
            self._s.add(UserRow(
                id=user.id, email=user.email,
                password_hash=user.password_hash, info=user.info,
            ))

    async def update_info(self, email: str, info: str) -> None:
        row = (
            await self._s.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        if not row:
            row = UserRow(email=email, info=info)
            self._s.add(row)
        else:
            row.info = info
            row.updated_at = datetime.utcnow()
