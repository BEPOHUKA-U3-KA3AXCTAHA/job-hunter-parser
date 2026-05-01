"""DEPRECATED legacy shim — re-exports app.shared so old `from src.shared import X` imports still work.

Will be deleted after all imports are flipped to `from app.shared import X`.
"""
from app.shared import (  # noqa: F401
    CandidateProfile,
    Email,
    LinkedInUrl,
    SearchCriteria,
    Seniority,
    TechStack,
)
