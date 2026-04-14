from __future__ import annotations

from enum import StrEnum


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
