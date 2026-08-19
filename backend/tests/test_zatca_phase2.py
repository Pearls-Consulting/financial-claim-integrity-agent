"""Phase-2 QR validation.

Real fixtures: the two Pearls invoices in supporting_docs/example_documents —
both genuine invoices whose generator stamps imitation phase-2 tags (hash as
hex text, 32-byte non-signature, UUID in the public-key tag). They must
classify as "pseudo", NEVER as valid or as a hard tampering fail.

Synthetic fixtures: a real secp256k1 keypair proves the "valid" and
"invalid_signature" paths.
"""

import base64
import hashlib
from pathlib import Path

import pytest

from app.services.validators.zatca_qr import validate_phase2

DOCS = Path(__file__).resolve().parents[2] / "supporting_docs" / "example_documents"
FIXTURE_INVOICES = ["Sales-Invoice-00196.pdf", "Sales-Invoice-00218.pdf"]


def tlv(tag: int, value: bytes | str) -> bytes:
    b = value.encode("utf-8") if isinstance(value, str) else value
    return bytes([tag, len(b)]) + b


def phase1_tags() -> bytes:
    return (
        tlv(1, "شركة تجريبية")
        + tlv(2, "310122393500003")
        + tlv(3, "2026-08-01T10:00:00Z")
        + tlv(4, "115.00")
        + tlv(5, "15.00")
    )


@pytest.fixture(scope="module")
def real_payloads() -> dict[str, str]:
    pytest.importorskip("pypdfium2")
    pytest.importorskip("zxingcpp")
    from app.services.extraction.qr import extract_from_pdf

    payloads = {}
    for name in FIXTURE_INVOICES:
        pdf = DOCS / name
        if not pdf.exists():
            pytest.skip(f"fixture invoice missing: {pdf}")
        hits = extract_from_pdf(pdf)
        assert hits, f"no QR found in {name}"
        payloads[name] = hits[0].payload
    return payloads


def test_real_invoices_classify_as_pseudo(real_payloads):
    for name, payload in real_payloads.items():
        result = validate_phase2(payload)
        assert result.status == "pseudo", f"{name}: {result.status} {result.problems}"
        assert not result.has_stamp
        # the specific generator bug must be named, not just "wrong length"
        assert any("double-encoding" in p for p in result.problems), (name, result.problems)


def test_phase1_only_is_absent():
    payload = base64.b64encode(phase1_tags()).decode()
    assert validate_phase2(payload).status == "absent"


def _signed_payload(tamper: bool = False) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    key = ec.generate_private_key(ec.SECP256K1())
    digest = hashlib.sha256(b"<Invoice>...</Invoice>").digest()
    signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    if tamper:
        digest = hashlib.sha256(b"tampered").digest()
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    raw = (
        phase1_tags()
        + tlv(6, base64.b64encode(digest).decode())  # base64-text form, as seen in the wild
        + tlv(7, signature)  # raw-bytes form
        + tlv(8, public_der)
    )
    return base64.b64encode(raw).decode()


def test_genuine_signature_is_valid():
    result = validate_phase2(_signed_payload())
    assert result.status == "valid"
    assert not result.has_stamp  # tag 9 not present in the synthetic payload


def test_tampered_hash_fails_signature():
    result = validate_phase2(_signed_payload(tamper=True))
    assert result.status == "invalid_signature"


def test_uuid_public_key_is_pseudo():
    raw = (
        phase1_tags()
        + tlv(6, base64.b64encode(hashlib.sha256(b"x").digest()).decode())
        + tlv(7, b"\x00" * 64)
        + tlv(8, "50df03a9-de7c-4a59-910b-65d0164c4a65")
    )
    result = validate_phase2(base64.b64encode(raw).decode())
    assert result.status == "pseudo"
    assert any("public key" in p for p in result.problems)
