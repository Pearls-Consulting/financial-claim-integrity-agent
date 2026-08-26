"""Ad-hoc claim submissions — the integration-agnostic intake (demo approach 1).

Instead of pulling the claim from D365, the reviewer submits the same header
fields the ERP form carries (استلام المطالبات overview) plus the document
files themselves. The resulting ``Claim`` is shaped identically to an
ERP-sourced one, so the pipeline downstream cannot tell the difference —
swapping this intake for the D365 adapter later changes nothing.

Rows persist in SQLite (services/store.py) so the guided review survives
restarts. Uploaded files land under ``backend/uploads/<claim_id>/`` with paths
stored relative to the project root, which is how the extractor resolves them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from fastapi import UploadFile

from app.domain.models import Claim, ClaimFile
from app.services import store

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOAD_DIR = PROJECT_ROOT / "backend" / "uploads"


def next_claim_id() -> str:
    return store.next_claim_id()


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    return re.sub(r"[^\w.\-؀-ۿ ]", "_", name) or "file"


def stage_file(claim_id: str, upload: UploadFile, doc_type: str) -> ClaimFile:
    dest_dir = UPLOAD_DIR / claim_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _safe_name(upload.filename or doc_type)
    dest.write_bytes(upload.file.read())
    return ClaimFile(path=str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/"), doc_type=doc_type)


def drop_files(claim: Claim, predicate: Callable[[ClaimFile], bool]) -> None:
    """Remove matching source files from the claim AND from the upload dir, so a
    replaced document leaves no stale record or file behind. A file that is
    still referenced by another (kept) slot stays on disk."""
    dropped = [f for f in claim.source_files if predicate(f)]
    claim.source_files = [f for f in claim.source_files if not predicate(f)]
    kept_paths = {f.path for f in claim.source_files}
    for f in dropped:
        if f.path in kept_paths:
            continue
        path = (PROJECT_ROOT / f.path).resolve()
        if path.is_relative_to(UPLOAD_DIR.resolve()) and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def add(claim: Claim) -> None:
    store.save_submission(claim)


def get(claim_id: str) -> Claim | None:
    return store.get_submission(claim_id)


def list_claims() -> list[Claim]:
    return store.list_submissions()
