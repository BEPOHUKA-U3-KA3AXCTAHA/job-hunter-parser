"""SQLA implementation of UserRepository.

Only place importing UserRow.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.infra.db import get_session_maker
from app.infra.db.tables.users import UserRow
from app.modules.users.models.user import User


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        info=row.info or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlaUserRepository:
    async def get_by_email(self, email: str) -> User | None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.email == email))
            ).scalar_one_or_none()
            return _row_to_user(row) if row else None

    async def get_by_id(self, user_id: UUID) -> User | None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.id == user_id))
            ).scalar_one_or_none()
            return _row_to_user(row) if row else None

    async def get_default(self) -> User | None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(
                    select(UserRow).order_by(UserRow.created_at.asc()).limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_user(row) if row else None

    async def upsert(self, user: User) -> None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.email == user.email))
            ).scalar_one_or_none()
            if row:
                row.info = user.info
                if user.password_hash:
                    row.password_hash = user.password_hash
            else:
                session.add(UserRow(
                    id=user.id, email=user.email,
                    password_hash=user.password_hash, info=user.info,
                ))
            await session.commit()

    async def update_info(self, email: str, info: str) -> None:
        Session = get_session_maker()
        async with Session() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.email == email))
            ).scalar_one_or_none()
            if not row:
                row = UserRow(email=email, info=info)
                session.add(row)
            else:
                row.info = info
                row.updated_at = datetime.utcnow()
            await session.commit()
