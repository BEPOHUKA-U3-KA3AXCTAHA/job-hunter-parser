"""Pick the right ATS handler for an external apply URL."""
from __future__ import annotations

from app.modules.automation.adapters.external_apply.ashby import AshbyHandler
from app.modules.automation.adapters.external_apply.base import AtsHandler
from app.modules.automation.adapters.external_apply.generic import GenericHandler
from app.modules.automation.adapters.external_apply.greenhouse import GreenhouseHandler
from app.modules.automation.adapters.external_apply.lever import LeverHandler
from app.modules.automation.adapters.external_apply.workday import WorkdayHandler

# Order matters: specific handlers first, generic last as fallback.
_HANDLERS: list[AtsHandler] = [
    GreenhouseHandler(),
    LeverHandler(),
    AshbyHandler(),
    WorkdayHandler(),
    GenericHandler(),
]


def pick_handler(url: str) -> AtsHandler:
    for h in _HANDLERS:
        if h.can_handle(url):
            return h
    return _HANDLERS[-1]  # GenericHandler always returns True; safety net


def channel_for_handler(handler_name: str) -> str:
    """Map handler name → ApplyChannel string for DB persistence."""
    return {
        "greenhouse": "ats_greenhouse",
        "lever": "ats_lever",
        "ashby": "ats_ashby",
        "workday": "ats_workday",
        "generic": "ats_other",
    }.get(handler_name, "ats_other")
