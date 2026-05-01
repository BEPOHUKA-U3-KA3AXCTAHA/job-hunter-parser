from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """Who is searching. Used by LLM to personalize outreach messages."""

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
