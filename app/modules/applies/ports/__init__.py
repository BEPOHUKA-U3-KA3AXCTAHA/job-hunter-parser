"""Applies module — driven ports."""
from app.modules.applies.ports.candidates import CandidateBundle, CandidateBundleRepository
from app.modules.applies.ports.llm import LLMGenerator
from app.modules.applies.ports.qa_cache import QACacheRepository
from app.modules.applies.ports.repository import ApplyRepository

__all__ = [
    "ApplyRepository", "CandidateBundle", "CandidateBundleRepository",
    "LLMGenerator", "QACacheRepository",
]
