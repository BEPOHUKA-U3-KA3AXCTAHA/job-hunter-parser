from __future__ import annotations

from src.companies.models import Company
from src.leads.scorer import LeadScorer
from src.people.models import DecisionMaker, DecisionMakerRole
from src.shared import Email, TechStack


def test_cto_at_hiring_python_rust_startup_scores_high() -> None:
    target = TechStack.from_strings("python", "rust")
    scorer = LeadScorer(target)

    company = Company(
        name="BlockFlow",
        tech_stack=TechStack.from_strings("python", "rust", "postgres"),
        is_hiring=True,
    )
    dm = DecisionMaker(
        full_name="Alex Smith",
        role=DecisionMakerRole.CTO,
        company_id=company.id,
        email=Email("alex@blockflow.ai"),
    )

    assert scorer.score(company, dm) >= 80


def test_hr_at_random_company_scores_low() -> None:
    target = TechStack.from_strings("python", "rust")
    scorer = LeadScorer(target)

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
