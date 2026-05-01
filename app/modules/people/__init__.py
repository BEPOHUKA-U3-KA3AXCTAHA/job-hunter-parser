"""People module — public API.

Cross-module callers MUST import from here only:
    from app.modules.people import DecisionMaker, DecisionMakerRole

Internal sub-packages (models/, ports/, adapters/, services/) are private.
"""
from app.modules.people.models import (
    DecisionMaker,
    DecisionMakerNotFound,
    DecisionMakerRole,
)
from app.modules.people.ports import ContactEnrichment, DecisionMakerSearch

__all__ = [
    "ContactEnrichment",
    "DecisionMaker",
    "DecisionMakerNotFound",
    "DecisionMakerRole",
    "DecisionMakerSearch",
]
