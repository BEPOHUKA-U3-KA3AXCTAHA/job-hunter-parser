"""Shared types used across modules."""
from src.shared.candidate_profile import CandidateProfile
from src.shared.email import Email
from src.shared.linkedin_url import LinkedInUrl
from src.shared.search_criteria import SearchCriteria
from src.shared.seniority import Seniority
from src.shared.tech_stack import TechStack

__all__ = [
    "CandidateProfile",
    "Email",
    "LinkedInUrl",
    "SearchCriteria",
    "Seniority",
    "TechStack",
]
