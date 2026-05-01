"""Applies module — driven ports."""
from app.modules.applies.ports.llm import LLMGenerator
from app.modules.applies.ports.repository import ApplyRepository

__all__ = ["ApplyRepository", "LLMGenerator"]
