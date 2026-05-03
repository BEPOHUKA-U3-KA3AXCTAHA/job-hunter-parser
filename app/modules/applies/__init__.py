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
]
