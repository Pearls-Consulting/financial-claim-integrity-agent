"""ERP data-source seam.

All claim data lives in the ERP (D365 F&O — SDB's VRM claims module). The
demo runs on a seeded mock; the production adapter implements the same
interface against D365 OData/custom services. Selection via ERP_SOURCE.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.domain.models import Claim

DATA_FILE = Path(__file__).parent.parent / "data" / "sample_claims.json"


class ClaimSource(Protocol):
    def list_claims(self) -> list[Claim]: ...

    def get_claim(self, claim_id: str) -> Claim | None: ...


class MockErpSource:
    """Seeded claims mirroring the shape of the D365 استلام المطالبات list."""

    def __init__(self) -> None:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        self._claims = {c["id"]: Claim.model_validate(c) for c in raw}

    def list_claims(self) -> list[Claim]:
        return list(self._claims.values())

    def get_claim(self, claim_id: str) -> Claim | None:
        return self._claims.get(claim_id)


_source: ClaimSource | None = None


def get_source() -> ClaimSource:
    global _source
    if _source is None:
        engine = get_settings().erp_source
        if engine != "mock":
            raise NotImplementedError(f"ERP source '{engine}' not implemented yet (only 'mock')")
        _source = MockErpSource()
    return _source
