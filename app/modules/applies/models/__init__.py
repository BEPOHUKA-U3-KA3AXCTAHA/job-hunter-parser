"""Applies module — domain models."""
from app.modules.applies.models.apply import (
    Apply,
    ApplyChannel,
    ApplyFlank,
    ApplyMethod,
    ApplyNotFound,
    ApplyStatus,
)

# Legacy aliases — to be removed once all callers stop using Message*
Message = Apply
MessageStatus = ApplyStatus
MessageChannel = ApplyChannel

__all__ = [
    "Apply",
    "ApplyChannel",
    "ApplyFlank",
    "ApplyMethod",
    "ApplyNotFound",
    "ApplyStatus",
    "Message",
    "MessageChannel",
    "MessageStatus",
]
