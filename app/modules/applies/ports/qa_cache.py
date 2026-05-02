"""Port: cache layer for form-question answers.

Service depends on this Protocol; adapter (SQLA-backed) implements it.
Keeps service code ORM-blind per the project's infra-import rule.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable


@runtime_checkable
class QACacheRepository(Protocol):
    """Persisted (normalized question text → answer) pairs.

    Implementations: SqlaQACacheRepository (form_answers table).
    Could grow alternates later: in-memory for tests, file-backed for portability.
    """

    async def get_cached(self, label: str) -> tuple[str, str, float] | None:
        """Return (answer, source, confidence) if cached, else None."""
        ...

    async def save_to_cache(
        self,
        label: str,
        answer: str,
        options: list[str] | None,
        source: Literal["user", "llm"],
        confidence: float,
        company: str = "",
        job_title: str = "",
    ) -> None: ...
