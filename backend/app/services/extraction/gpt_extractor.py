"""The `gpt` extractor engine: GPT vision reads -> merge -> reconcile.

Speed comes from shape, not from a faster model: every file and every chunk
of the claim is one independent call, all in flight together, at LOW
reasoning effort, cached per file content. A 76-page scanned contract reads
in ~35 s wall-clock (chunks of 10-20 pages in parallel), a one-page invoice
in ~7 s — and a cumulative gate run that adds one document re-reads only that
document.

Azure CU is not called here at all; see extraction/locate.py for the only
remaining CU use (one-page polygons for the evidence viewer).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.domain.models import Claim, ClaimDocuments
from app.services.extraction.common import inject_qr, merge_erp
from app.services.extraction.gpt_vision import merge_reads, read_files, to_documents
from app.services.extraction.reconcile import reconcile

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Merge priority: the file staged for a document type is the authority for
# that type's header; later files only fill gaps (a contract that also prints
# an invoice-like summary must not override the invoice).
_PRIORITY = {"invoice": 0, "contract_boq": 1, "coc": 2, "delivery_note": 3, "other": 4}


class GptVisionExtractor:
    def extract(self, claim: Claim) -> ClaimDocuments:
        # Pre-finance attachment files are identified at upload time
        # (extraction/attachments.py) — not part of the claim-structuring read.
        files = [
            (f, PROJECT_ROOT / f.path)
            for f in claim.source_files
            if not f.doc_type.startswith("attachment")
        ]
        missing = [str(p) for _, p in files if not p.exists()]
        if missing:
            raise FileNotFoundError(f"claim {claim.id}: staged files not found: {missing}")
        if not files:
            return claim.documents  # nothing staged — the documents exist only as ERP data

        files.sort(key=lambda fp: _PRIORITY.get(fp[0].doc_type, 9))
        reads = read_files([(path, meta.doc_type) for meta, path in files])
        ok = [r for r in reads if r]
        for (meta, path), r in zip(files, reads):
            if r is None:
                logger.warning("GPT reader produced nothing for %s (%s)", path.name, meta.doc_type)
        if not ok:
            raise RuntimeError(f"claim {claim.id}: the GPT reader produced nothing readable")

        merged = merge_reads(ok)
        extracted = to_documents(merged)
        extracted = reconcile(extracted, merged.get("anchors") or {})

        inject_qr(extracted, [p for m, p in files if m.doc_type == "invoice" and p.suffix.lower() == ".pdf"])
        return merge_erp(extracted, claim)
