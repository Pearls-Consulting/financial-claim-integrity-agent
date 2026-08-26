"""The `azure` extractor engine: CU Layout OCR -> GPT structuring -> merge.

Division of labor (proven in the prequalification agent):
- CU reads the files into faithful markdown (concurrently, disk-cached),
- the deterministic QR decoder reads barcode payloads off the invoice PDF,
- GPT only organizes the markdown into the ClaimDocuments schema,
- ERP-owned data (penalties, attachment list, and any document that has no
  staged file — e.g. a COC that exists only as ERP data) falls back to the
  claim's seeded/ERP documents.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.domain.models import Claim, ClaimDocuments
from app.services.extraction.cu_client import analyze_layout
from app.services.extraction.qr import extract_from_pdf
from app.services.extraction.structuring import structure_documents

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class AzureCuExtractor:
    def extract(self, claim: Claim) -> ClaimDocuments:
        # Pre-finance attachment files are classified at upload time
        # (extraction/attachments.py) — re-OCR'ing them here would only slow
        # the run and pollute the claim-structuring corpus.
        files = [
            (f, PROJECT_ROOT / f.path)
            for f in claim.source_files
            if not f.doc_type.startswith("attachment")
        ]
        missing = [str(p) for _, p in files if not p.exists()]
        if missing:
            raise FileNotFoundError(f"claim {claim.id}: staged files not found: {missing}")
        if not files:
            # Nothing staged — the claim's documents exist only as ERP data.
            return claim.documents

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda fp: analyze_layout(fp[1]), files))

        ocr_ok: list[tuple[str, str, str]] = []
        for (meta, path), result in zip(files, results):
            if result.ok:
                ocr_ok.append((path.name, meta.doc_type, result.markdown))
            else:
                logger.warning("CU failed on %s: %s", path.name, result.error)
        if not ocr_ok:
            raise RuntimeError(f"claim {claim.id}: OCR produced nothing readable")

        extracted = structure_documents(ocr_ok)

        # Deterministic QR injection: the payload comes from the printed code,
        # never from the model. Empty means "file scanned, no QR found" — that
        # absence is itself the intake finding.
        invoice_files = [p for m, p in files if m.doc_type == "invoice" and p.suffix.lower() == ".pdf"]
        if extracted.invoice is not None and invoice_files:
            hits = extract_from_pdf(invoice_files[0])
            extracted.invoice.qr_payload = hits[0].payload if hits else ""

        # Merge with ERP-owned data.
        seeded = claim.documents
        if extracted.invoice is None:
            extracted.invoice = seeded.invoice
        if extracted.coc is None:
            extracted.coc = seeded.coc
        # The product receipt is ERP-owned: when the ERP has one it is the
        # authority and always wins; a receipt read from an uploaded delivery
        # note only fills in when no ERP record exists (wizard submissions).
        if seeded.receipt is not None:
            extracted.receipt = seeded.receipt
        if not extracted.boq:
            extracted.boq = seeded.boq
        if extracted.contract is None:
            extracted.contract = seeded.contract
        extracted.penalties = seeded.penalties
        extracted.attachments = seeded.attachments
        extracted.detected_attachments = seeded.detected_attachments
        return extracted
