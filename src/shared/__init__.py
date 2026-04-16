"""Shared types used across modules (value objects, enums)."""
from src.shared.email import Email
from src.shared.linkedin_url import LinkedInUrl
from src.shared.seniority import Seniority
from src.shared.tech_stack import TechStack

__all__ = ["Email", "LinkedInUrl", "Seniority", "TechStack"]
