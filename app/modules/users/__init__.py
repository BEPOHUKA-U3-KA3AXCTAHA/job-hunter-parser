"""Users module — public API.

Cross-module callers MUST import from here only:
    from app.modules.users import User, AccountsRepository, default_user_repo
"""
from app.modules.users.models.candidate_profile import CandidateProfile, load_user_info
from app.modules.users.models.user import User, UserNotFound
from app.modules.users.ports.accounts import AccountsRepository
from app.modules.users.ports.users_uow import UsersUoW


def default_uow() -> UsersUoW:
    """Composition-root helper — production SQLA-backed UoW."""
    from app.modules.users.adapters.users_uow.sqla import SqlaUsersUoW
    return SqlaUsersUoW()


__all__ = [
    "CandidateProfile", "User", "UserNotFound",
    "AccountsRepository", "UsersUoW", "default_uow", "load_user_info",
]
