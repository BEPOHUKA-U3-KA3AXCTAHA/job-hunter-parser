"""Port for external-ATS apply handlers.

Each handler claims a URL family (Greenhouse / Lever / Ashby / Workday /
generic) and knows how to drive that ATS's apply form. The dispatcher
picks the first handler whose `can_handle(url)` returns True.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class AtsContext:
    """Per-job context handed to each ATS handler.

    Profile fields are populated from the DB (single source of truth);
    handler implementations should never hardcode candidate data.
    """

    company: str
    job_title: str
    job_url: str        # the LinkedIn job URL we came from (for logs)
    ats_url: str        # the external URL the Apply button took us to
    profile_first_name: str = ""
    profile_last_name: str = ""
    profile_email: str = ""
    profile_phone: str = ""
    profile_location: str = ""
    profile_linkedin: str = ""
    resume_path: Path | None = None


@dataclass(slots=True)
class AtsResult:
    """Outcome of one external ATS apply attempt."""

    success: bool
    detail: str = ""
    pages: int = 0
    fields_filled: int = 0
    ats_name: str = ""


@runtime_checkable
class AtsHandler(Protocol):
    """One concrete handler per ATS family.

    Implementations: app.modules.automation.adapters.external_apply.{
    greenhouse, lever, ashby, workday, generic}.
    """

    name: str  # 'greenhouse' / 'lever' / 'ashby' / 'workday' / 'generic'

    def can_handle(self, url: str) -> bool: ...

    def apply(self, driver, ctx: AtsContext) -> AtsResult: ...
