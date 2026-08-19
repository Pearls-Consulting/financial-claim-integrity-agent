from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings

app = FastAPI(title="Claim Integrity Agent", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
