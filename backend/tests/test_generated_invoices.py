"""Round-trip the generated fixture invoices (scripts/generate_test_invoices.py)
through the REAL extraction path: rendered PDF -> QR bitmap decode -> TLV ->
phase-2 classification, asserting each file's manifest expectation."""

import json
from pathlib import Path

import pytest

from app.services.validators.zatca_qr import decode_tlv, validate_phase2

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "supporting_docs" / "test_scenarios"


def _manifest() -> dict:
    manifest = FIXTURE_DIR / "manifest.json"
    if not manifest.exists():
        pytest.skip("generated fixtures absent — run scripts/generate_test_invoices.py")
    return json.loads(manifest.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(_manifest().keys()) if (FIXTURE_DIR / "manifest.json").exists() else [])
def test_generated_invoice_roundtrip(name: str):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("zxingcpp")
    from app.services.extraction.qr import extract_from_pdf

    meta = _manifest()[name]
    hits = extract_from_pdf(FIXTURE_DIR / name)
    assert hits, f"no QR decoded from {name}"
    payload = hits[0].payload

    decoded = decode_tlv(payload)
    assert decoded.valid_tlv
    if meta["fixture"] == "fake_face":
        # This fixture's QR deliberately lies about the seller — mismatch expected.
        assert decoded.fields["vat_number"] != meta["vat_number"]
    else:
        assert decoded.fields["vat_number"] == meta["vat_number"]

    expected = meta["expected_phase2"]
    if expected in ("valid", "absent", "pseudo", "invalid_signature"):
        assert validate_phase2(payload).status == expected, name


def test_fake_face_mismatches_declared_totals():
    """The fake_face fixture's QR must NOT match its printed totals — that
    mismatch is the finding it exists to trigger."""
    manifest = _manifest()
    name = next(n for n, m in manifest.items() if m["fixture"] == "fake_face")
    meta = manifest[name]
    pytest.importorskip("pypdfium2")
    from app.services.extraction.qr import extract_from_pdf

    payload = extract_from_pdf(FIXTURE_DIR / name)[0].payload
    fields = decode_tlv(payload).fields
    assert float(fields["total"]) != meta["total"]
    assert fields["vat_number"] != meta["vat_number"]
