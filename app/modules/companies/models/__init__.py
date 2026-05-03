"""Companies module — domain models. Re-export concepts for the module's public API."""
from app.modules.companies.models.company import Company, CompanyNotFound
from app.modules.companies.models.job_posting import JobPosting, JobPostingNotFound
from app.modules.companies.models.search_criteria import SearchCriteria
from app.modules.companies.models.seniority import Seniority
from app.modules.companies.models.tech_stack import TechStack

__all__ = [
    "Company", "CompanyNotFound",
    "JobPosting", "JobPostingNotFound",
    "SearchCriteria",
    "Seniority",
    "TechStack",
]
