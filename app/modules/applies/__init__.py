"""Applies module — public API.

Cross-module callers MUST import from here only:
    from app.modules.applies import Apply, ApplyStatus, MassApplyJournal, ...

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
    ApplyJournal,
    CandidateBundle,
    CandidateBundles,
    LLMGenerator,
    QACache,
)
from app.modules.applies.ports.mass_apply import MassApplyJournal, PendingOutreach
from app.modules.applies.ports.applies_uow import AppliesUoW
from app.modules.applies.services.answer_questions import (
    FormAnswer,
    FormQuestion,
    answer_questions,
)
from app.modules.applies.services.curate import CuratedPair, filter_and_score
from app.modules.applies.services.score import RelevanceScorer


def get_claude_cli_pool(**kwargs):  # noqa: ANN201,ANN003
    """Composition-root helper — returns the production Claude CLI pool.
    Wraps the LLM adapter constructor without making cross-module callers
    reach into `applies/adapters/llm/`."""
    from app.modules.applies.adapters.llm.cli import ClaudeCLIPool
    return ClaudeCLIPool(**kwargs)


def default_uow() -> AppliesUoW:
    """Composition-root helper: instantiate the production SQLA UoW.
    Tests / alt-impl callers pass their own UoW instead."""
    from app.modules.applies.adapters.applies_uow.sqla import SqlaAppliesUoW
    return SqlaAppliesUoW()


__all__ = [
    # entities
    "Apply", "ApplyChannel", "ApplyFlank", "ApplyMethod", "ApplyNotFound", "ApplyStatus",
    "Message", "MessageChannel", "MessageStatus",
    # ports
    "ApplyJournal", "CandidateBundle", "CandidateBundles",
    "LLMGenerator", "MassApplyJournal", "PendingOutreach", "QACache",
    "AppliesUoW",
    # services (use cases)
    "CuratedPair", "FormAnswer", "FormQuestion", "RelevanceScorer",
    "answer_questions", "filter_and_score",
    # composition-root helpers
    "default_uow",
    "get_claude_cli_pool",
]
