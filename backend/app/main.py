import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.routes import router
from app.core.config import get_settings

# The app's own INFO lines (per-chunk read timings, retries, key-field and
# text-layer corrections, judge/reconcile fallbacks) go to stdout → journald
# next to uvicorn's access log. Without this a 10-minute run shows as one
# access-log line and nothing else. The HTTP client libraries stay quiet.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore", "openai", "azure"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

app = FastAPI(title="Claim Integrity Agent", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "extractor_engine": s.extractor_engine,
        "judge_engine": s.judge_engine,
        "erp_source": s.erp_source,
    }
