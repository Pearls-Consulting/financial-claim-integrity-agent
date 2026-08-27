"""Extractor seam — documents in, structured fields out.

Engines:
  mock  - passes through the structured documents already on the seeded claim
          (as if extraction had run); deterministic and free.
  gpt   - GPT vision reads the pages natively, every file/chunk in parallel,
          with page provenance (the prequalification agent's analyzer-v4
          shape). Azure CU is used only by the evidence viewer's highlight
          fallback. The engine for real documents.
  azure - the earlier CU Layout OCR -> GPT structuring path, kept as fallback.

Whatever reads, Python VALIDATES: the deterministic QR decode cross-checks the
invoice face, and every gate rule is code, not a model.
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
    if engine == "gpt":
        from app.services.extraction.gpt_extractor import GptVisionExtractor

        return GptVisionExtractor()
    if engine == "azure":
        from app.services.extraction.azure_extractor import AzureCuExtractor

        return AzureCuExtractor()
    # claude engine: port from pre-qualification-agent services/analyzer/claude4_analyzer.py
    raise NotImplementedError(f"Extractor engine '{engine}' not implemented yet (mock | gpt | azure)")
