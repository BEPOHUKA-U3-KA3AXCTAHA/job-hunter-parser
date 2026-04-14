from __future__ import annotations

from src.shared.kernel.tech_stack import TechStack


def test_normalization() -> None:
    ts = TechStack.from_strings("Python", "RUST", " FastAPI ")
    assert "python" in ts.technologies
    assert "rust" in ts.technologies
    assert "fastapi" in ts.technologies


def test_has() -> None:
    ts = TechStack.from_strings("Python", "Rust")
    assert ts.has("python")
    assert ts.has("RUST")
    assert not ts.has("go")


def test_overlap_score() -> None:
    a = TechStack.from_strings("python", "rust", "postgres")
    b = TechStack.from_strings("python", "rust", "kafka")
    assert 0.6 < a.overlap_score(b) < 0.7


def test_empty_overlap() -> None:
    a = TechStack()
    b = TechStack.from_strings("python")
    assert a.overlap_score(b) == 0.0
