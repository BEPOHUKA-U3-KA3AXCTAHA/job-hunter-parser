from __future__ import annotations

from dataclasses import dataclass, field


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
