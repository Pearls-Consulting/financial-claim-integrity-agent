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

    # --- auth / session ---------------------------------------------------
    # Single-role demo login (the vendor-management specialist who reviews
    # claims). One account, configured here rather than in a users table -
    # the prequalification agent's multi-role admin/user CRUD is deliberately
    # not carried over. Session mechanics are identical to that agent:
    # stateless HS256 JWT in an httpOnly cookie, sliding idle window.
    auth_email: str = "reviewer@sdb.local"
    auth_name: str = "Vendor Management Specialist"
    auth_name_ar: str = "أخصائي إدارة الموردين"
    auth_password: str = ""  # blank = login disabled (every attempt is 401)
    jwt_secret: str = "dev-insecure-change-me-set-a-real-secret-in-production"
    session_cookie_name: str = "cia_session"
    session_idle_hours: int = 168  # 7 days, sliding
    session_absolute_hours: int = 720  # 30 days from login; 0 = no cap
    cookie_secure: bool = False  # True behind HTTPS in production


@lru_cache
def get_settings() -> Settings:
    return Settings()
