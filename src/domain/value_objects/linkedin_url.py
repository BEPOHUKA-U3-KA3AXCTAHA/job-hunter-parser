from __future__ import annotations

import re
from dataclasses import dataclass

_LINKEDIN_RE = re.compile(r"^https?://(www\.)?linkedin\.com/(in|company)/[A-Za-z0-9\-_%]+/?$")


@dataclass(frozen=True, slots=True)
class LinkedInUrl:
    value: str

    def __post_init__(self) -> None:
        if not _LINKEDIN_RE.match(self.value):
            raise ValueError(f"Invalid LinkedIn URL: {self.value}")

    @property
    def is_profile(self) -> bool:
        return "/in/" in self.value

    @property
    def is_company(self) -> bool:
        return "/company/" in self.value

    @property
    def slug(self) -> str:
        return self.value.rstrip("/").rsplit("/", 1)[-1]

    def __str__(self) -> str:
        return self.value
