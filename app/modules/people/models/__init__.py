"""People module — domain models."""
from app.modules.people.models.decision_maker import (
    DecisionMaker,
    DecisionMakerNotFound,
    DecisionMakerRole,
)
from app.modules.people.models.email import Email
from app.modules.people.models.linkedin_url import LinkedInUrl

__all__ = [
    "DecisionMaker", "DecisionMakerNotFound", "DecisionMakerRole",
    "Email", "LinkedInUrl",
]
