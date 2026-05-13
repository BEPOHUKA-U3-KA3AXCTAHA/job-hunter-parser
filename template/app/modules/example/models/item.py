"""Domain entity. Pure Python — no SQLA imports allowed (rule 1)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Item:
    """Minimal domain object. Replace with real fields."""

    id: int
    name: str
    description: str = ""
