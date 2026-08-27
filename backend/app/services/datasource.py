"""ERP data-source seam.

All claim data lives in the ERP (D365 F&O — SDB's VRM claims module). The
demo runs on a seeded mock; the production adapter implements the same
interface against D365 OData/custom services. Selection via ERP_SOURCE.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.domain.models import Claim

DATA_FILE = Path(__file__).parent.parent / "data" / "sample_claims.json"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)


class ClaimSource(Protocol):
    def list_claims(self) -> list[Claim]: ...

    def get_claim(self, claim_id: str) -> Claim | None: ...


class MockErpSource:
    """Seeded claims mirroring the shape of the D365 استلام المطالبات list."""

    def __init__(self) -> None:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        self._claims = {c["id"]: Claim.model_validate(c) for c in raw}

    def list_claims(self) -> list[Claim]:
        """Only claims whose staged documents exist here. supporting_docs is
        not shipped to the demo server (size, real client papers), so a
        seeded claim pointing at an unshipped file would sit in the list
        and fail on click — better absent than broken. get_claim still
        resolves it (tests, direct links)."""
        out = []
        for c in self._claims.values():
            missing = [f.path for f in c.source_files if not (PROJECT_ROOT / f.path).exists()]
            if missing:
                logger.warning("seeded claim %s hidden: documents not on this host: %s", c.id, missing)
                continue
            out.append(c)
        return out

    def get_claim(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)


class EmptyErpSource:
    """No ERP feed at all: the claims list holds only what reviewers submit
    through the guided intake. The client-facing demo instance runs this —
    seeded example claims must not reappear on every deploy."""

    def list_claims(self) -> list[Claim]:
        return []

    def get_claim(self, claim_id: str) -> Claim | None:
        return None


_source: ClaimSource | None = None


def get_source() -> ClaimSource:
    global _source
    if _source is None:
        engine = get_settings().erp_source
        if engine == "mock":
            _source = MockErpSource()
        elif engine == "none":
            _source = EmptyErpSource()
        else:
            raise NotImplementedError(f"ERP source '{engine}' not implemented yet (mock | none)")
    return _source
