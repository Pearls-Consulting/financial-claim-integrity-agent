"""Pre-finance identity / validity cross-checks — the mismatch layer over the
attachment extraction (ported from the prequalification agent's
declared-vs-printed validation-flag pattern: deterministic comparisons,
digit-script-normalised, skip rather than guess)."""

from app.domain.models import Claim, DetectedAttachment, InvoiceDoc, Severity
from app.services.extraction.attachments import witness_identity_fields
from app.services.rules.engine import CHECKS, run_rulepack


def _att(doc_key: str, **fields: str) -> DetectedAttachment:
    return DetectedAttachment(file_name=f"{doc_key}.pdf", doc_key=doc_key, fields=dict(fields))


def _claim(dets, invoice=None, vendor_name_ar="", claim_date="2026-06-01") -> Claim:
    c = Claim(id="VRM-TEST", vendor_name_ar=vendor_name_ar, claim_date=claim_date)
    c.documents.detected_attachments = list(dets)
    c.documents.invoice = invoice
    return c


# ── identity consistency across the vendor file ────────────────────────────


def test_identity_consistent_across_digit_scripts():
    claim = _claim([
        _att("commercial registration", cr_number="1010123456"),
        _att("zakat certificate", cr_number="١٠١٠١٢٣٤٥٦"),  # same number, Arabic-Indic
    ])
    out = CHECKS["attachment_identity_consistent"](claim, {})
    assert out.ok is True


def test_identity_mismatch_fails_and_names_both_documents():
    claim = _claim([
        _att("commercial registration", cr_number="1010123456"),
        _att("gosi certificate", cr_number="1010999999"),
    ])
    out = CHECKS["attachment_identity_consistent"](claim, {})
    assert out.ok is False
    assert "1010123456" in out.detail_en and "1010999999" in out.detail_en


def test_identity_single_document_skips():
    claim = _claim([_att("commercial registration", cr_number="1010123456")])
    assert CHECKS["attachment_identity_consistent"](claim, {}).ok is None


# ── VAT number vs the invoice ──────────────────────────────────────────────


def test_vat_matches_invoice_ok():
    inv = InvoiceDoc(invoice_no="INV-1", seller_vat_number="310122393500003")
    claim = _claim([_att("zakat certificate", vat_number="310122393500003")], invoice=inv)
    assert CHECKS["attachment_vat_matches_invoice"](claim, {}).ok is True


def test_vat_differs_from_invoice_fails():
    inv = InvoiceDoc(invoice_no="INV-1", seller_vat_number="310122393500003")
    claim = _claim([_att("zakat certificate", vat_number="311111111100003")], invoice=inv)
    out = CHECKS["attachment_vat_matches_invoice"](claim, {})
    assert out.ok is False


def test_vat_check_skips_without_invoice():
    claim = _claim([_att("zakat certificate", vat_number="310122393500003")])
    assert CHECKS["attachment_vat_matches_invoice"](claim, {}).ok is None


# ── vendor name consistency ────────────────────────────────────────────────


def test_vendor_name_suffix_variant_counts_as_agreement():
    claim = _claim(
        [_att("commercial registration", vendor_name_ar="شركة النخيل للمقاولات المحدودة")],
        vendor_name_ar="شركة النخيل للمقاولات",
    )
    assert CHECKS["attachment_vendor_name_consistent"](claim, {}).ok is True


def test_vendor_name_different_entity_flags():
    claim = _claim(
        [_att("commercial registration", vendor_name_ar="مؤسسة الصقر الذهبي للتجارة")],
        vendor_name_ar="شركة النخيل للمقاولات",
    )
    out = CHECKS["attachment_vendor_name_consistent"](claim, {})
    assert out.ok is False


def test_vendor_name_single_source_skips():
    claim = _claim([_att("boq")], vendor_name_ar="شركة النخيل للمقاولات")
    assert CHECKS["attachment_vendor_name_consistent"](claim, {}).ok is None


# ── identifier format plausibility (misread tripwire) ──────────────────────


def test_id_formats_ok_for_official_shapes():
    claim = _claim([
        _att("commercial registration", cr_number="١٠١٠١٢٣٤٥٦"),
        _att("zakat certificate", vat_number="310122393500003"),
    ])
    assert CHECKS["attachment_id_formats"](claim, {}).ok is True


def test_id_formats_flags_dropped_digit():
    claim = _claim([_att("commercial registration", cr_number="101012345")])  # 9 digits
    out = CHECKS["attachment_id_formats"](claim, {})
    assert out.ok is False
    assert "101012345" in out.detail_en


def test_id_formats_flags_bad_vat():
    claim = _claim([_att("zakat certificate", vat_number="1234567890")])
    assert CHECKS["attachment_id_formats"](claim, {}).ok is False


# ── certificate validity at the claim date ─────────────────────────────────


def test_expired_zakat_certificate_fails():
    claim = _claim([_att("zakat certificate", expiry_date="2026-01-31")], claim_date="2026-06-01")
    out = CHECKS["attachment_certificates_valid"](claim, {})
    assert out.ok is False
    assert "2026-01-31" in out.detail_en


def test_valid_certificates_pass():
    claim = _claim(
        [
            _att("zakat certificate", expiry_date="2026-12-31"),
            _att("gosi certificate", expiry_date="2027-01-15"),
        ],
        claim_date="2026-06-01",
    )
    assert CHECKS["attachment_certificates_valid"](claim, {}).ok is True


def test_hijri_printed_expiry_is_left_to_the_human_eye():
    claim = _claim([_att("zakat certificate", expiry_date="1447-11-20")], claim_date="2026-06-01")
    assert CHECKS["attachment_certificates_valid"](claim, {}).ok is None


# ── gate behaviour: ERP claims without detections are untouched ────────────


def test_prefinance_gate_skips_cross_checks_without_detections():
    claim = _claim([])
    claim.documents.attachments = [
        "contract", "boq", "award letter", "work commencement",
        "commercial registration", "zakat certificate", "gosi certificate",
    ]
    run = run_rulepack("prefinance", "prefinance.yaml", claim)
    new_rules = {
        "prefinance.identity_consistent", "prefinance.vat_matches_invoice",
        "prefinance.vendor_name_consistent", "prefinance.identifier_format",
        "prefinance.certificates_valid",
    }
    assert not new_rules & {f.rule_id for f in run.findings}


def test_prefinance_gate_surfaces_identity_mismatch():
    inv = InvoiceDoc(invoice_no="INV-1", seller_vat_number="310122393500003")
    claim = _claim(
        [
            _att("commercial registration", cr_number="1010123456", vat_number="310122393500003"),
            _att("zakat certificate", cr_number="1010999999", vat_number="310122393500003"),
        ],
        invoice=inv,
    )
    run = run_rulepack("prefinance", "prefinance.yaml", claim)
    by_rule = {f.rule_id: f for f in run.findings}
    assert by_rule["prefinance.identity_consistent"].severity is Severity.fail
    assert by_rule["prefinance.vat_matches_invoice"].severity is Severity.ok


# ── text-layer witness on the extracted identity fields ────────────────────

_PAGE_TEXT = (
    "شهادة رقم 78901\n"
    "السجل التجاري: ١٠١٠١٢٣٤٥٦\n"
    "الرقم الضريبي 310122393500003\n"
    "صالحة حتى 2026-12-31\n"
)


def test_witness_fixes_transposed_cr_digits():
    fields = {"cr_number": "1010123465", "vat_number": "310122393500003"}  # last two CR digits swapped
    out = witness_identity_fields(fields, _PAGE_TEXT)
    assert out["cr_number"] == "1010123456"
    assert out["vat_number"] == "310122393500003"


def test_witness_leaves_confirmed_values_alone():
    fields = {"cr_number": "1010123456", "reference_no": "78901"}
    assert witness_identity_fields(fields, _PAGE_TEXT) == fields


def test_witness_does_nothing_without_text_layer():
    fields = {"cr_number": "1010123465"}
    assert witness_identity_fields(fields, "") == fields
