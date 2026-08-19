"""Claim integrity pipeline: extract -> run every gate's rulepack -> judge.

Runs synchronously for the demo (deterministic checks are instant). When real
extraction lands, this moves behind a background job with progress polling —
the prequalification agent's jobs/queue.py pattern.
"""

from __future__ import annotations

from app.domain.models import Claim, InvoiceDoc, QrField, QrSummary, RunResult
from app.domain.stages import STAGES
from app.services import store
from app.services.extractor import get_extractor
from app.services.judge import get_judge
from app.services.rules.engine import run_rulepack
from app.services.validators import zatca_qr


def _money(v: float) -> str:
    return f"{v:,.2f}"


# OCR & PDF text layers make visually-identical Arabic strings differ in
# codepoints: bidi controls, tatweel/diacritics, and Farsi letter variants
# (ک U+06A9 for ك, ی U+06CC for ي). Names that read the same must match.
_NAME_FOLD = dict.fromkeys(
    [*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2066, 0x206A), 0xFEFF,
     0x0640, *range(0x064B, 0x0653)],  # tatweel + harakat diacritics
    None,
) | {
    0x06A9: 0x0643,  # keheh -> kaf
    0x06CC: 0x064A,  # farsi yeh -> yeh
    0x0649: 0x064A,  # alef maqsura -> yeh (OCR confusion in names)
    0x0622: 0x0627, 0x0623: 0x0627, 0x0625: 0x0627, 0x0671: 0x0627,  # alef variants
}


def _norm_name(value: str) -> str:
    # split() with no args also folds NBSP/thin-space and friends into breaks.
    return " ".join(value.translate(_NAME_FOLD).split())


def build_qr_summary(invoice: InvoiceDoc | None) -> QrSummary:
    """Decode the invoice QR and pair every field with its invoice-face value.

    Deterministic (Python decodes, never a model) — this is the "extracted
    fields from the QR" section of the evidence view.
    """
    if invoice is None or not invoice.qr_payload:
        return QrSummary(present=False)
    decoded = zatca_qr.decode_tlv(invoice.qr_payload)
    if not decoded.valid_tlv:
        return QrSummary(present=True, valid_tlv=False, error=decoded.error)

    f = decoded.fields
    fields = [
        QrField(
            key="seller_name",
            label_en="Seller name",
            label_ar="اسم البائع",
            value=f.get("seller_name", ""),
            expected=invoice.seller_name_ar,
            match=(_norm_name(f.get("seller_name", "")) == _norm_name(invoice.seller_name_ar))
            if invoice.seller_name_ar
            else None,
        ),
        QrField(
            key="vat_number",
            label_en="VAT number",
            label_ar="الرقم الضريبي",
            value=f.get("vat_number", ""),
            expected=invoice.seller_vat_number,
            match=(f.get("vat_number") == invoice.seller_vat_number) if invoice.seller_vat_number else None,
        ),
        QrField(
            key="timestamp",
            label_en="Timestamp",
            label_ar="طابع الوقت",
            value=f.get("timestamp", ""),
            expected=invoice.invoice_date,
            match=f.get("timestamp", "").startswith(invoice.invoice_date) if invoice.invoice_date else None,
        ),
        QrField(
            key="total",
            label_en="Total (incl. VAT)",
            label_ar="الإجمالي شامل الضريبة",
            value=f.get("total", ""),
            expected=_money(invoice.total_with_vat),
            match=zatca_qr.amounts_match(f.get("total", ""), invoice.total_with_vat)
            if invoice.total_with_vat
            else None,
        ),
        QrField(
            key="vat",
            label_en="VAT amount",
            label_ar="قيمة الضريبة",
            value=f.get("vat", ""),
            expected=_money(invoice.vat_amount),
            match=zatca_qr.amounts_match(f.get("vat", ""), invoice.vat_amount) if invoice.vat_amount else None,
        ),
    ]
    p2 = zatca_qr.validate_phase2(invoice.qr_payload)
    return QrSummary(
        present=True,
        valid_tlv=True,
        fields=fields,
        phase2_status=p2.status,
        phase2_problems=p2.problems,
        phase2_notes=p2.notes,
        has_stamp=p2.has_stamp,
    )


def run_claim(claim: Claim, gate_ids: set[str] | None = None) -> RunResult:
    """Run the pipeline over all gates, or a subset (the guided step-by-step
    review runs gates cumulatively as each document arrives)."""
    claim = claim.model_copy(deep=True)
    claim.documents = get_extractor().extract(claim)
    stages = sorted(STAGES, key=lambda s: s.order)
    if gate_ids is not None:
        stages = [s for s in stages if s.id in gate_ids]
    gates = [run_rulepack(stage.id, stage.rulepack, claim) for stage in stages]
    result = get_judge().judge(claim, gates)
    result.extracted = claim.documents
    result.qr = build_qr_summary(claim.documents.invoice)
    store.save_run(result)
    return result


def latest_run(claim_id: str) -> RunResult | None:
    return store.get_run(claim_id)
