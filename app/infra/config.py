"""Split config:
- Secrets (.env): API keys, DB connection strings with passwords
- App config (config.toml): feature flags, limits, URLs, preferences

12-factor app approach.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """Sensitive data — loaded from .env, never committed."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    apollo_api_key: str = Field(default="", alias="APOLLO_API_KEY")
    apify_api_key: str = Field(default="", alias="APIFY_API_KEY")
    hunter_api_key: str = Field(default="", alias="HUNTER_API_KEY")

    database_url: str = Field(
        default="sqlite+aiosqlite:///jhp.db",
        alias="DATABASE_URL",
    )

    linkedin_email: str = Field(default="", alias="LINKEDIN_EMAIL")
    linkedin_password: str = Field(default="", alias="LINKEDIN_PASSWORD")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="development", alias="ENVIRONMENT")


class AppConfig:
    """Non-sensitive app configuration — loaded from config.toml."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        current = self._data
        for part in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(part)
            if current is None:
                return default
        return current

    @property
    def skip_fresh_days(self) -> int:
        return int(self.get("enrichment.skip_fresh_days", 30))

    @property
    def target_roles(self) -> list[str]:
        return list(self.get("enrichment.target_roles", ["cto", "founder"]))

    @property
    def max_contacts_per_company(self) -> int:
        return int(self.get("enrichment.max_contacts_per_company", 3))

    @property
    def default_limit(self) -> int:
        return int(self.get("pipeline.default_limit", 50))

    @property
    def default_channel(self) -> str:
        return str(self.get("pipeline.default_channel", "linkedin"))

    @property
    def default_tech(self) -> list[str]:
        return list(self.get("pipeline.default_tech", ["python", "rust"]))

    @property
    def llm_model(self) -> str:
        return str(self.get("llm.model", "claude-sonnet-4-20250514"))


def load_app_config(path: str | Path = "config.toml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        return AppConfig({})
    return AppConfig(tomllib.loads(p.read_text(encoding="utf-8")))


def get_secrets() -> Secrets:
    return Secrets()
