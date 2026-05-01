"""DEPRECATED legacy shim — re-exports app.modules.companies for old callers."""
from app.modules.companies import Company, CompanyNotFound, CompanySource, JobPosting, JobPostingNotFound  # noqa: F401
