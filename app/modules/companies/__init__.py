"""Companies module — public API.

Cross-module callers MUST import from here only:
    from app.modules.companies import Company, JobPosting, CompanySource, SearchCriteria

Internal sub-packages (models/, ports/, adapters/, services/) are private.
"""
from app.modules.companies.models import (
    Company,
    CompanyNotFound,
    JobPosting,
    JobPostingNotFound,
    SearchCriteria,
    Seniority,
    TechStack,
)
from app.modules.companies.ports import CompanySource
from app.modules.companies.ports.repository import CompanyRepository, JobApplyTarget

__all__ = [
    "Company",
    "CompanyNotFound",
    "CompanyRepository",
    "CompanySource",
    "JobApplyTarget",
    "JobPosting",
    "JobPostingNotFound",
    "SearchCriteria",
    "Seniority",
    "TechStack",
]
