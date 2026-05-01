"""DEPRECATED legacy shim — use app.modules.people."""
from app.modules.people import (  # noqa: F401
    ContactEnrichment,
    DecisionMaker,
    DecisionMakerNotFound,
    DecisionMakerRole,
    DecisionMakerSearch,
)
