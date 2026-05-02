"""Companies module — driven ports."""
from app.modules.companies.ports.repository import CompanyRepository, JobApplyTarget
from app.modules.companies.ports.scraper import CompanySource

__all__ = ["CompanyRepository", "CompanySource", "JobApplyTarget"]
