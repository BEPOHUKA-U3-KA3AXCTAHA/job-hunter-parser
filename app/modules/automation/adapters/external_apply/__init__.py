"""Per-ATS handlers for non-Easy-Apply jobs that link out to external sites."""
from app.modules.automation.adapters.external_apply.base import AtsContext, AtsHandler, AtsResult
from app.modules.automation.adapters.external_apply.dispatcher import channel_for_handler, pick_handler

__all__ = ["AtsContext", "AtsHandler", "AtsResult", "channel_for_handler", "pick_handler"]
