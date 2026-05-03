"""Candidate context.

For LLM-driven form autofill: `cv_text` (extracted from resume_en.pdf) is
the primary source of truth. The hand-coded fields below are used by the
outreach generator (Claude DM messages) and the relevance scorer — they
hold structured facts that are easier to compute over than raw CV text.
Visa / work-authorization facts live here too because they're not in the
CV but the LLM needs them for sponsorship questions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path


@cache
def _load_cv_text() -> str:
    """Extract full CV text from resume_en.pdf at the project root.
    Returns empty string if the file is missing — tests work without it."""
    cv_path = Path(__file__).resolve().parents[4] / "resume_en.pdf"
    if not cv_path.exists():
        return ""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(cv_path))
        return "\n".join(p.extract_text() for p in reader.pages).strip()
    except Exception:
        return ""


def load_user_info() -> str:
    """Read free-form profile text from the users table (default user).

    This is the SOURCE OF TRUTH for everything the LLM puts in form fields
    (LinkedIn URL, Telegram, location/visa, etc.) — overrides the CV PDF
    which may carry stale data. Empty string if no users yet.

    Uses a raw sqlite read so the call is safe from any context (sync,
    async, asyncio.run()-nested) — the async repo path used to bail to ""
    whenever a loop was already running, leaving the LLM blind.
    """
    try:
        import sqlite3
        db = Path(__file__).resolve().parents[4] / "jhp.db"
        if not db.exists():
            return ""
        con = sqlite3.connect(str(db))
        try:
            row = con.execute(
                "SELECT info FROM users ORDER BY created_at LIMIT 1"
            ).fetchone()
            return (row[0] or "").strip() if row else ""
        finally:
            con.close()
    except Exception:
        return ""


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """Who is searching."""

    name: str = "Sergey Sergeev"
    email: str = "serzhserg98@gmail.com"
    telegram: str = "@ultravert"
    linkedin: str = "sergey-sergeev"
    location: str = "Montenegro (UTC+2)"

    experience_years: int = 4
    tech_stack: list[str] = field(
        default_factory=lambda: [
            "Python", "Rust", "FastAPI", "SQLAlchemy", "PostgreSQL",
            "Redis", "RabbitMQ", "Kafka", "MQTT", "Docker",
        ]
    )

    summary: str = (
        "Back-end developer with 4+ years of Python and Rust experience. "
        "Architect on a Rust+MQTT IoT prototype (pump equipment monitoring for city water supply), "
        "shipped an algorithmic trading platform with sub-100ms reaction (Actix-web + Tokio + PyO3), "
        "and freight control systems with event-driven architecture (RabbitMQ/Kafka). "
        "Experience as Technical Lead, designing hexagonal microservice systems from scratch."
    )

    highlights: list[str] = field(
        default_factory=lambda: [
            "Reduced trading system reaction time from 2s to 100ms via Rust + PyO3",
            "Architect on a Rust+MQTT IoT prototype for pump equipment monitoring",
            "Technical Lead experience with code review and architecture decisions",
            "Python + Rust combo (rare on the market)",
        ]
    )

    target_roles: list[str] = field(
        default_factory=lambda: [
            "Senior Backend Developer",
            "Rust Developer",
            "Python Backend Engineer",
        ]
    )

    # ---- ATS form-autofill context ----------------------------------------
    # Full CV text — primary source of truth for the answer_questions LLM.
    # Free-form user profile from the `users` table — this is the LLM's
    # ground truth (real LinkedIn URL, Telegram, visa status, etc.) and
    # OVERRIDES anything parsed from the CV PDF (which may carry stale
    # info like a wrong handle).
    user_info: str = field(default_factory=load_user_info)

    # CV text is a secondary source — used when the user_info field is
    # empty or doesn't mention a particular fact (work history, projects,
    # tech depth, etc.). Falls back gracefully if PDF is missing.
    cv_text: str = field(default_factory=_load_cv_text)

    # Visa / work-authorization facts (not in the CV — encode explicitly so
    # the LLM answers sponsorship questions correctly for EU-based roles).
    eu_citizen: bool = False
    work_permit_eu: bool = False
    permit_status: str = (
        "Currently in Montenegro (non-EU). No work permit in any EU country yet "
        "(applying for boravak/residence in Montenegro). Would require visa "
        "sponsorship for any EU-based or in-country role; remote-from-Montenegro "
        "roles need no sponsorship."
    )

    # Salary preferences — fallback when users.info doesn't spell them out.
    salary_floor_eur: int = 55_000
    salary_floor_usd: int = 60_000
