"""Companies module — driven adapters: ORM tables + scrapers."""
from app.modules.companies.adapters.orm import CompanyRow, JobPostingRow

__all__ = ["CompanyRow", "JobPostingRow"]
