"""Companies module — public API.

Cross-module callers MUST import from here only:
    from app.modules.companies import Company, JobPosting, CompanySource

Internal sub-packages (models/, ports/, adapters/, services/) are private.
"""
from app.modules.companies.models import (
    Company,
    CompanyNotFound,
    JobPosting,
    JobPostingNotFound,
)
from app.modules.companies.ports import CompanySource

__all__ = [
    "Company",
    "CompanyNotFound",
    "CompanySource",
    "JobPosting",
    "JobPostingNotFound",
]
