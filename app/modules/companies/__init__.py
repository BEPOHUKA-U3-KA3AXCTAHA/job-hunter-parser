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
from app.modules.companies.ports.companies_uow import CompaniesUoW
from app.modules.companies.ports.company_directory import CompanyDirectoryRepository, JobApplyTarget


def default_uow() -> CompaniesUoW:
    """Composition-root helper — production SQLA-backed UoW."""
    from app.modules.companies.adapters.companies_uow.sqla import SqlaCompaniesUoW
    return SqlaCompaniesUoW()

__all__ = [
    "CompaniesUoW",
    "Company",
    "CompanyNotFound",
    "CompanyDirectoryRepository",
    "CompanySource",
    "default_uow",
    "JobApplyTarget",
    "JobPosting",
    "JobPostingNotFound",
    "SearchCriteria",
    "Seniority",
    "TechStack",
]
