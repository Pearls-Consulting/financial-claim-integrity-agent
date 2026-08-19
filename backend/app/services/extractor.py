"""Extractor seam — documents in, structured fields out.

Division of labor proven in the prequalification agent: specialist OCR
(Azure CU Layout) READS, the LLM ORGANIZES into the field schema, Python
VALIDATES. LLM-vision alone was shown to misread high-stakes Arabic digits,
so it is never the primary reader.

The mock passes through the structured documents already on the seeded claim
(as if extraction had run), which keeps the demo deterministic and free.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import get_settings
from app.domain.models import Claim, ClaimDocuments


class Extractor(Protocol):
    def extract(self, claim: Claim) -> ClaimDocuments: ...


class MockExtractor:
    def extract(self, claim: Claim) -> ClaimDocuments:
        return claim.documents


def get_extractor() -> Extractor:
    engine = get_settings().extractor_engine
    if engine == "mock":
        return MockExtractor()
    if engine == "azure":
        from app.services.extraction.azure_extractor import AzureCuExtractor

        return AzureCuExtractor()
    # claude engine: port from pre-qualification-agent services/analyzer/claude4_analyzer.py
    raise NotImplementedError(f"Extractor engine '{engine}' not implemented yet (mock | azure)")
