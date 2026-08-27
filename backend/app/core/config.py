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

    # --- `gpt` extractor: GPT vision reads the files natively ---------------
    # The prequalification agent's analyzer-v4 shape: one Responses-API call
    # per file (big PDFs split into page chunks), every file/chunk in flight
    # at once, LOW reasoning effort, page provenance on every field. Azure CU
    # is NOT part of extraction on this engine — it only OCRs the one cited
    # page when the viewer's text-layer highlight fails (locate.py).
    gpt_vision_model: str = ""  # empty = azure_openai_model
    gpt_vision_effort: str = "low"  # Responses-API reasoning effort
    gpt_vision_render_dpi: int = 200  # pages are sent as images rendered at this DPI (never as PDF)
    gpt_vision_chunk_pages: int = 5  # pages per call; smaller = more parallel, shorter outputs, better row recall
    gpt_vision_concurrency: int = 8  # calls in flight; each holds a chunk's page images + request body (~50 MB) — the demo service runs under a memory cap
    gpt_vision_max_output_tokens: int = 24000  # dense BoQ chunks need room
    gpt_vision_timeout_seconds: float = 300.0
    gpt_vision_attachment_max_pages: int = 3  # identity docs: read the first pages only
    # Short units (<= consensus_max_pages) are read `passes` times concurrently
    # and majority-voted; a disagreement gets one tie-break read. Catches the
    # occasional digit slip on an invoice without slowing the run.
    gpt_vision_passes: int = 2
    gpt_vision_consensus_max_pages: int = 2
    # Key-field verify: identifiers and totals (VAT no., invoice/COC number,
    # dates, amounts) are re-read with a small focused prompt on the header
    # page and that value WINS. Measured: the schema-wide read transposes a
    # 15-digit VAT number ~30% of the time; the focused read 0/24.
    gpt_vision_verify: bool = True
    # Disk cache of model reads (gpt_vision / gpt_attachments / gpt_reconcile).
    # Off: every run re-reads the documents — what the demo should show.
    extraction_cache: bool = False
    # Text-only reconcile call (invoice<->BoQ item alignment, end date from a
    # duration + dated anchor). Same model unless overridden.
    gpt_reconcile_model: str = ""
    gpt_reconcile_effort: str = "low"
    # GPT judge write-up: reasoning effort ("" = model default).
    gpt_judge_effort: str = "low"

    # Evidence locate (CU OCR of ONE rendered page, billed per page): when the
    # value is not on the cited page, how many pages in total may be OCR'd —
    # the cited page plus its nearest neighbours, never the whole document.
    locate_max_pages: int = 3

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
