from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    frontend_origin: str = "http://localhost:5173"

    # Adapter seams — see .env.example for the full engine matrix.
    extractor_engine: str = "mock"
    judge_engine: str = "mock"
    erp_source: str = "mock"

    anthropic_api_key: str = ""
    claude_judge_model: str = "claude-sonnet-5"
    azure_openai_base_url: str = ""
    azure_openai_api_key: str = ""
    azure_openai_model: str = "gpt-5.4"
    azure_cu_endpoint: str = ""
    azure_cu_key: str = ""
    azure_cu_analyzer: str = "prebuilt-layout"
    azure_cu_api_version: str = "2025-11-01"


@lru_cache
def get_settings() -> Settings:
    return Settings()
