"""Shared types used across modules (value objects, enums)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_LINKEDIN_RE = re.compile(r"^https?://(www\.)?linkedin\.com/(in|company)/[A-Za-z0-9\-_%]+/?$")


@dataclass(frozen=True, slots=True)
class Email:
    value: str

    def __post_init__(self) -> None:
        if not _EMAIL_RE.match(self.value):
            raise ValueError(f"Invalid email: {self.value}")

    @property
    def domain(self) -> str:
        return self.value.split("@", 1)[1]

    def __str__(self) -> str:
        return self.value


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


@dataclass(frozen=True, slots=True)
class TechStack:
    technologies: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        normalized = frozenset(t.strip().lower() for t in self.technologies if t.strip())
        object.__setattr__(self, "technologies", normalized)

    def has(self, tech: str) -> bool:
        return tech.lower() in self.technologies

    def has_any(self, techs: list[str]) -> bool:
        return any(self.has(t) for t in techs)

    def overlap_score(self, other: TechStack) -> float:
        if not self.technologies or not other.technologies:
            return 0.0
        common = self.technologies & other.technologies
        return len(common) / max(len(self.technologies), len(other.technologies))

    @classmethod
    def from_strings(cls, *techs: str) -> TechStack:
        return cls(frozenset(techs))


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    UNKNOWN = "unknown"

    @classmethod
    def from_text(cls, text: str | None) -> Seniority:
        if not text:
            return cls.UNKNOWN
        t = text.lower()
        for level in (cls.PRINCIPAL, cls.STAFF, cls.SENIOR, cls.MIDDLE, cls.JUNIOR, cls.INTERN):
            if level.value in t:
                return level
        return cls.UNKNOWN
