from __future__ import annotations

from src.companies.models import Company
from src.people.models import DecisionMaker
from src.shared import TechStack


class RelevanceScorer:
    """Calculates relevance score 0-100 for a (company, decision_maker) pair.

    Higher = better outreach target.
    """

    def __init__(self, target_tech_stack: TechStack) -> None:
        self._target = target_tech_stack

    def score(self, company: Company, decision_maker: DecisionMaker) -> int:
        score = 0

        # Role priority (0-40)
        score += decision_maker.role.priority * 8

        # Tech stack match (0-40)
        overlap = company.tech_stack.overlap_score(self._target)
        score += int(overlap * 40)

        # Hiring flag (0-10)
        if company.is_actively_hiring():
            score += 10

        # Has contact (0-10)
        if decision_maker.has_any_contact():
            score += 10

        return min(score, 100)
