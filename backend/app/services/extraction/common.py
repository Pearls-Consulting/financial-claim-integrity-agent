"""Post-read steps shared by every real extractor engine (azure | gpt):
deterministic QR injection and the merge with ERP-owned data."""

from __future__ import annotations

from pathlib import Path

from app.domain.models import Claim, ClaimDocuments
from app.services.extraction.qr import extract_from_pdf


def inject_qr(extracted: ClaimDocuments, invoice_files: list[Path]) -> None:
    """The QR payload comes from the printed code, never from the model. Empty
    means "file scanned, no QR found" — that absence is itself the intake
    finding."""
    if extracted.invoice is None or not invoice_files:
        return
    hits = extract_from_pdf(invoice_files[0])
    extracted.invoice.qr_payload = hits[0].payload if hits else ""


def merge_erp(extracted: ClaimDocuments, claim: Claim) -> ClaimDocuments:
    """ERP-owned data (penalties, attachment list, any document that has no
    staged file — e.g. a COC that exists only as ERP data) falls back to the
    claim's seeded/ERP documents."""
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
