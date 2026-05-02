"""User entity — the person behind the bot's runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class User:
    email: str
    info: str = ""                          # free-form profile text — LLM ground truth
    password_hash: str | None = None        # filled when registration/login lands
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class UserNotFound(Exception):
    """Raised by repository when a lookup misses."""
