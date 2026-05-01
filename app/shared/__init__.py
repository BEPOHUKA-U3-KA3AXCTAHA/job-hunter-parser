"""Shared types used across modules."""
from app.shared.candidate_profile import CandidateProfile
from app.shared.email import Email
from app.shared.linkedin_url import LinkedInUrl
from app.shared.search_criteria import SearchCriteria
from app.shared.seniority import Seniority
from app.shared.tech_stack import TechStack

__all__ = [
    "CandidateProfile",
    "Email",
    "LinkedInUrl",
    "SearchCriteria",
    "Seniority",
    "TechStack",
]
