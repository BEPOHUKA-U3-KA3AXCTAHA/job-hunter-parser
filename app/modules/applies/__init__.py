"""Applies module — public API.

Cross-module callers MUST import from here only:
    from app.modules.applies import Apply, ApplyStatus, MassApplyRepository, ...

Internal sub-packages (models/, ports/, adapters/, services/) are private.
Composition roots (entrypoints/) may also import concrete adapters from
adapters/<provider>.py for wiring.
"""
from app.modules.applies.models import (
    Apply,
    ApplyChannel,
    ApplyFlank,
    ApplyMethod,
    ApplyNotFound,
    ApplyStatus,
    Message,           # legacy alias
    MessageChannel,
    MessageStatus,
)
from app.modules.applies.ports import (
    ApplyRepository,
    CandidateBundle,
    CandidateBundleRepository,
    LLMGenerator,
    QACacheRepository,
)
from app.modules.applies.ports.mass_apply import MassApplyRepository, PendingOutreach
from app.modules.applies.ports.unit_of_work import UnitOfWork
from app.modules.applies.services.answer_questions import (
    FormAnswer,
    FormQuestion,
    answer_questions,
)
from app.modules.applies.services.curate import CuratedPair, filter_and_score
from app.modules.applies.services.score import RelevanceScorer


def default_uow() -> UnitOfWork:
    """Composition-root helper: instantiate the production SQLA UoW.
    Tests / alt-impl callers pass their own UoW instead."""
    from app.modules.applies.adapters.unit_of_work.sqla import SqlaUnitOfWork
    return SqlaUnitOfWork()


def _legacy_apply_repo() -> ApplyRepository:
    """DEPRECATED — for `pipeline` / `curate` / `retry` CLI commands that
    haven't been migrated to UoW yet. Opens its own session per call,
    bypassing the UoW pattern. Will be removed once those commands are
    refactored.

    DO NOT USE in services or new code.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.infra.db import get_session_maker
    from app.modules.applies.adapters.repository.sqla import SqliteApplyRepository

    class _PerCallShim:
        """Each method call opens its own session and commits — restores
        the pre-UoW behavior for legacy callers."""

        def __getattr__(self, name: str):
            method = getattr(SqliteApplyRepository, name)

            async def _wrap(*args, **kwargs):
                Session = get_session_maker()
                async with Session() as session:
                    real = SqliteApplyRepository(session)
                    result = await method(real, *args, **kwargs)
                    await session.commit()
                    return result
            return _wrap

    return _PerCallShim()


__all__ = [
    # entities
    "Apply", "ApplyChannel", "ApplyFlank", "ApplyMethod", "ApplyNotFound", "ApplyStatus",
    "Message", "MessageChannel", "MessageStatus",
    # ports
    "ApplyRepository", "CandidateBundle", "CandidateBundleRepository",
    "LLMGenerator", "MassApplyRepository", "PendingOutreach", "QACacheRepository",
    "UnitOfWork",
    # services (use cases)
    "CuratedPair", "FormAnswer", "FormQuestion", "RelevanceScorer",
    "answer_questions", "filter_and_score",
    # composition-root helpers
    "default_uow",
    "_legacy_apply_repo",
]
