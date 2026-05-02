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
from app.modules.applies.services.answer_questions import (
    FormAnswer,
    FormQuestion,
    answer_questions,
)
from app.modules.applies.services.curate import CuratedPair, filter_and_score
from app.modules.applies.services.score import RelevanceScorer


def default_mass_apply_repo() -> MassApplyRepository:
    """Composition-root helper: instantiate the production SQLA MassApply
    adapter without making the caller reach into `applies/adapters/`.
    Tests / alt-impl callers pass their own repo instead."""
    from app.modules.applies.adapters.repository.mass_apply import SqlaMassApplyRepository
    return SqlaMassApplyRepository()


def default_qa_cache() -> QACacheRepository:
    from app.modules.applies.adapters.repository.qa_cache import SqlaQACacheRepository
    return SqlaQACacheRepository()


def default_candidate_bundle_repo() -> CandidateBundleRepository:
    from app.modules.applies.adapters.repository.candidates import SqlaCandidateBundleRepository
    return SqlaCandidateBundleRepository()


__all__ = [
    # entities
    "Apply", "ApplyChannel", "ApplyFlank", "ApplyMethod", "ApplyNotFound", "ApplyStatus",
    "Message", "MessageChannel", "MessageStatus",
    # ports
    "ApplyRepository", "CandidateBundle", "CandidateBundleRepository",
    "LLMGenerator", "MassApplyRepository", "PendingOutreach", "QACacheRepository",
    # services (use cases)
    "CuratedPair", "FormAnswer", "FormQuestion", "RelevanceScorer",
    "answer_questions", "filter_and_score",
    # composition-root helpers (default SQLA-backed instances)
    "default_candidate_bundle_repo", "default_mass_apply_repo", "default_qa_cache",
]
