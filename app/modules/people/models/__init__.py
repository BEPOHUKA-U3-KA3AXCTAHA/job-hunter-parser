"""People module — domain models."""
from app.modules.people.models.decision_maker import (
    DecisionMaker,
    DecisionMakerNotFound,
    DecisionMakerRole,
)

__all__ = ["DecisionMaker", "DecisionMakerNotFound", "DecisionMakerRole"]
