from __future__ import annotations

from app.modules.applies.services.score import RelevanceScorer
from app.modules.companies.models import Company
from app.modules.people.models import DecisionMaker, DecisionMakerRole
from app.shared import TechStack


def test_cto_at_hiring_python_rust_startup_scores_high() -> None:
    target = TechStack.from_strings("python", "rust")
    scorer = RelevanceScorer(target)

    company = Company(
        name="BlockFlow",
        tech_stack=TechStack.from_strings("python", "rust", "postgres"),
        is_hiring=True,
    )
    dm = DecisionMaker(
        full_name="Alex Smith",
        role=DecisionMakerRole.CTO,
        company_id=company.id,
        contacts={"email": "alex@blockflow.ai"},
    )

    assert scorer.score(company, dm) >= 80


def test_hr_at_random_company_scores_low() -> None:
    target = TechStack.from_strings("python", "rust")
    scorer = RelevanceScorer(target)

    company = Company(
        name="RandomCo",
        tech_stack=TechStack.from_strings("java", "spring"),
        is_hiring=False,
    )
    dm = DecisionMaker(
        full_name="HR Person",
        role=DecisionMakerRole.HR,
        company_id=company.id,
    )
    assert scorer.score(company, dm) < 30
