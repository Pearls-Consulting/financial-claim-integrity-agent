"""SQLite persistence for submissions, run results, and review progress.

stdlib sqlite3 on purpose — no new runtime deps. Entities are stored as the
pydantic model's JSON (the Claim/RunResult schema is still evolving with the
demo and a document store survives that); real columns exist only where the
app queries by them (verdict, step). WAL + a process-wide write lock is
plenty for a single-instance demo server.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from app.domain.models import Claim, RunResult

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "claims.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS runs (
  claim_id TEXT PRIMARY KEY,
  verdict TEXT NOT NULL,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS progress (
  claim_id TEXT PRIMARY KEY,
  step INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI serves sync endpoints from a threadpool -> share one
        # connection across threads, serialized by _lock.
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        _conn = conn
    return _conn


# ------------------------------------------------------------- submissions
def save_submission(claim: Claim) -> None:
    with _lock:
        _db().execute(
            "INSERT INTO submissions(id, data) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (claim.id, claim.model_dump_json()),
        )
        _db().commit()


def get_submission(claim_id: str) -> Claim | None:
    row = _db().execute("SELECT data FROM submissions WHERE id = ?", (claim_id,)).fetchone()
    return Claim.model_validate_json(row[0]) if row else None


def list_submissions() -> list[Claim]:
    rows = _db().execute("SELECT data FROM submissions ORDER BY created_at, id").fetchall()
    return [Claim.model_validate_json(r[0]) for r in rows]


def next_claim_id() -> str:
    """Submitted claims live in their own VRM-9xxxxx range; numbering continues
    across restarts now that rows persist."""
    with _lock:
        rows = _db().execute("SELECT id FROM submissions WHERE id LIKE 'VRM-9%'").fetchall()
        top = 900000
        for (cid,) in rows:
            m = re.fullmatch(r"VRM-(9\d{5})", cid)
            if m:
                top = max(top, int(m.group(1)))
        return f"VRM-{top + 1}"


# -------------------------------------------------------------------- runs
def save_run(result: RunResult) -> None:
    with _lock:
        _db().execute(
            "INSERT INTO runs(claim_id, verdict, data) VALUES(?, ?, ?) "
            "ON CONFLICT(claim_id) DO UPDATE SET verdict = excluded.verdict, "
            "data = excluded.data, updated_at = datetime('now')",
            (result.claim_id, result.verdict.value, result.model_dump_json()),
        )
        _db().commit()


def get_run(claim_id: str) -> RunResult | None:
    row = _db().execute("SELECT data FROM runs WHERE claim_id = ?", (claim_id,)).fetchone()
    return RunResult.model_validate_json(row[0]) if row else None


def verdict_map() -> dict[str, str]:
    return dict(_db().execute("SELECT claim_id, verdict FROM runs").fetchall())


# ---------------------------------------------------------------- progress
def set_progress(claim_id: str, step: int) -> None:
    with _lock:
        _db().execute(
            "INSERT INTO progress(claim_id, step) VALUES(?, ?) "
            "ON CONFLICT(claim_id) DO UPDATE SET step = excluded.step, "
            "updated_at = datetime('now')",
            (claim_id, step),
        )
        _db().commit()


def get_progress(claim_id: str) -> int:
    row = _db().execute("SELECT step FROM progress WHERE claim_id = ?", (claim_id,)).fetchone()
    return row[0] if row else 0


def progress_map() -> dict[str, int]:
    return dict(_db().execute("SELECT claim_id, step FROM progress").fetchall())
