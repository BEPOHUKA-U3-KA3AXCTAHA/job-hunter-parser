from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # Enrichment
    apollo_api_key: str = Field(default="", alias="APOLLO_API_KEY")
    hunter_api_key: str = Field(default="", alias="HUNTER_API_KEY")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://jobhunter:jobhunter@localhost:5432/jobhunter",
        alias="DATABASE_URL",
    )

    # LinkedIn
    linkedin_email: str = Field(default="", alias="LINKEDIN_EMAIL")
    linkedin_password: str = Field(default="", alias="LINKEDIN_PASSWORD")

    # Google Sheets
    google_sheets_credentials_path: str = Field(
        default="", alias="GOOGLE_SHEETS_CREDENTIALS_PATH"
    )
    google_sheets_id: str = Field(default="", alias="GOOGLE_SHEETS_ID")

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="development", alias="ENVIRONMENT")


def get_settings() -> Settings:
    return Settings()
