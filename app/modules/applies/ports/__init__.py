"""Applies module — driven ports."""
from app.modules.applies.ports.candidates import CandidateBundle, CandidateBundlesRepository
from app.modules.applies.ports.llm import LLMGenerator
from app.modules.applies.ports.qa_cache import QACache
from app.modules.applies.ports.apply_journal import ApplyJournalRepository

__all__ = [
    "ApplyJournalRepository", "CandidateBundle", "CandidateBundlesRepository",
    "LLMGenerator", "QACache",
]
