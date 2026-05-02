"""Users module — public API.

Cross-module callers MUST import from here only:
    from app.modules.users import User, UserRepository, default_user_repo
"""
from app.modules.users.models.user import User, UserNotFound
from app.modules.users.ports.repository import UserRepository


def default_user_repo() -> UserRepository:
    """Composition-root helper — production SQLA-backed repository."""
    from app.modules.users.adapters.repository.sqla import SqlaUserRepository
    return SqlaUserRepository()


__all__ = ["User", "UserNotFound", "UserRepository", "default_user_repo"]
