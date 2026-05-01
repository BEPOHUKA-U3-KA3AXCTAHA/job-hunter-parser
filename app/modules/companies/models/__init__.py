"""Companies module — domain models. Re-export concepts for the module's public API."""
from app.modules.companies.models.company import Company, CompanyNotFound
from app.modules.companies.models.job_posting import JobPosting, JobPostingNotFound

__all__ = ["Company", "CompanyNotFound", "JobPosting", "JobPostingNotFound"]
