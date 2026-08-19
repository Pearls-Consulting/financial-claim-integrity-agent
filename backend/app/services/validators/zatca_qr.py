"""Deterministic ZATCA phase-1 QR validation.

A compliant فاتورة ضريبية carries a QR whose payload is base64-wrapped TLV
with tags 1–5: seller name, seller VAT number, ISO timestamp, invoice total
(with VAT), VAT amount. Vendors submitting non-tax invoices sometimes print a
decorative/fake QR — decoding the TLV and cross-checking its values against
the invoice face catches most of these without any AI involvement.

Python decodes and compares; no model is ever asked to "judge" a QR.
(Extraction of the QR bitmap from the PDF is the extractor's job — the
prequalification agent's pypdfium2 + zxing-cpp path will be ported for that.)
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field

ZATCA_TAGS = {1: "seller_name", 2: "vat_number", 3: "timestamp", 4: "total", 5: "vat"}
PHASE2_TAGS = {6: "invoice_hash", 7: "signature", 8: "public_key", 9: "zatca_stamp"}


@dataclass
class QrDecodeResult:
    valid_tlv: bool
    fields: dict[str, str] = field(default_factory=dict)
    error: str = ""


def decode_tlv(payload_b64: str) -> QrDecodeResult:
    """Decode a base64 TLV payload. Returns valid_tlv=False on any malformation."""
    try:
        raw = base64.b64decode(payload_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        return QrDecodeResult(valid_tlv=False, error=f"not base64: {e}")

    fields: dict[str, str] = {}
    i = 0
    while i < len(raw):
        if i + 2 > len(raw):
            return QrDecodeResult(valid_tlv=False, fields=fields, error="truncated TLV header")
        tag, length = raw[i], raw[i + 1]
        value = raw[i + 2 : i + 2 + length]
        if len(value) != length:
            return QrDecodeResult(valid_tlv=False, fields=fields, error="truncated TLV value")
        name = ZATCA_TAGS.get(tag)
        if name:
            try:
                fields[name] = value.decode("utf-8")
            except UnicodeDecodeError:
                return QrDecodeResult(valid_tlv=False, fields=fields, error=f"tag {tag} not utf-8")
        i += 2 + length

    missing = [n for n in ZATCA_TAGS.values() if n not in fields]
    if missing:
        return QrDecodeResult(valid_tlv=False, fields=fields, error=f"missing tags: {missing}")
    return QrDecodeResult(valid_tlv=True, fields=fields)


# ---------------------------------------------------------------------------
# Phase-2 (tags 6-9) validation
#
# Obligation to carry REAL phase-2 tags arrives per-taxpayer by ZATCA
# integration wave, so their absence is a policy warn, never an automatic
# fail — but material that pretends to be cryptographic and isn't ("pseudo"),
# or well-formed material whose signature fails ("invalid_signature"), are
# distinct, stronger findings. Both fixture invoices in
# supporting_docs/example_documents are real-world "pseudo" specimens.
# ---------------------------------------------------------------------------


@dataclass
class Phase2Result:
    # "valid" | "invalid_signature" | "pseudo" | "absent"
    status: str
    problems: list[str] = field(default_factory=list)
    has_stamp: bool = False  # tag 9 present (attestation verify needs ZATCA's CA cert)
    # Non-fatal observations on a "valid" result (signing convention used,
    # non-ZATCA curve, …) — surfaced as finding evidence, never a failure.
    notes: list[str] = field(default_factory=list)


def _raw_tags(payload_b64: str) -> dict[int, bytes]:
    raw = base64.b64decode(payload_b64, validate=True)
    tags: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(raw):
        tag, length = raw[i], raw[i + 1]
        value = raw[i + 2 : i + 2 + length]
        if len(value) != length:
            break
        tags[tag] = value
        i += 2 + length
    return tags


def _as_bytes(value: bytes) -> bytes:
    """Tags 6/7 appear in the wild both as raw bytes and as base64 TEXT of the
    bytes — normalize to the raw form."""
    try:
        text = value.decode("ascii")
        return base64.b64decode(text, validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return value


def _looks_hex(data: bytes) -> bool:
    return len(data) in (64, 96) and all(c in b"0123456789abcdefABCDEF" for c in data)


def _load_public_key(value: bytes):
    """Accept DER SubjectPublicKeyInfo or a raw uncompressed secp256k1 point."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    if value[:1] == b"\x30":
        key = load_der_public_key(value)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise ValueError("not an EC public key")
        return key
    if value[:1] == b"\x04" and len(value) == 65:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), value)
    raise ValueError("not DER or an uncompressed EC point")


def validate_phase2(payload_b64: str) -> Phase2Result:
    """Structural + cryptographic validation of tags 6-9. Deterministic, offline."""
    try:
        tags = _raw_tags(payload_b64)
    except (binascii.Error, ValueError):
        return Phase2Result(status="absent", problems=["payload not decodable"])

    if not any(t in tags for t in PHASE2_TAGS):
        return Phase2Result(status="absent")

    problems: list[str] = []
    has_stamp = 9 in tags

    digest = _as_bytes(tags[6]) if 6 in tags else b""
    if 6 not in tags:
        problems.append("invoice hash (tag 6) missing")
    elif _looks_hex(digest):
        problems.append("invoice hash is hex text, not raw bytes (double-encoding bug)")
    elif len(digest) != 32:
        problems.append(f"invoice hash is {len(digest)} bytes, expected 32 (SHA-256)")

    signature = _as_bytes(tags[7]) if 7 in tags else b""
    if 7 not in tags:
        problems.append("signature (tag 7) missing")
    elif not (len(signature) == 64 or (signature[:1] == b"\x30" and 66 <= len(signature) <= 74)):
        problems.append(f"signature is {len(signature)} bytes — not a plausible ECDSA signature")

    public_key = None
    if 8 not in tags:
        problems.append("public key (tag 8) missing")
    else:
        try:
            public_key = _load_public_key(tags[8])
        except Exception:
            problems.append("tag 8 does not parse as an EC public key")

    if problems:
        return Phase2Result(status="pseudo", problems=problems, has_stamp=has_stamp)

    # Structure is genuine phase-2 material — verify the signature.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    assert public_key is not None
    if len(signature) == 64:  # raw r||s -> DER
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        signature = utils.encode_dss_signature(r, s)

    # Real-world signers disagree on what exactly tag 7 signs. All three
    # conventions below are cryptographically sound bindings of the signature
    # to the tag-6 invoice hash — observed in the wild, so any of them counts
    # as verified (the convention used is recorded as a note):
    #   prehashed      — tag-6 digest passed to ECDSA as the message hash
    #   digest-as-msg  — tag-6 digest treated as a message, SHA-256'd again
    #   b64-text-as-msg— tag-6's base64 text signed as the message
    conventions = [
        ("prehashed digest", digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))),
        ("digest signed as message", digest, ec.ECDSA(hashes.SHA256())),
        ("base64 hash text signed as message", tags[6], ec.ECDSA(hashes.SHA256())),
    ]
    verified = None
    for name, message, algo in conventions:
        try:
            public_key.verify(signature, message, algo)
            verified = name
            break
        except InvalidSignature:
            continue
    if verified is None:
        return Phase2Result(
            status="invalid_signature",
            problems=["ECDSA signature does not verify over the invoice hash (any known convention)"],
            has_stamp=has_stamp,
        )

    notes = [f"signature convention: {verified}"]
    curve = public_key.curve.name
    if curve != "secp256k1":
        notes.append(
            f"key on {curve} — ZATCA CSIDs use secp256k1, so this attests internal "
            "consistency only; confirm ZATCA attestation via the Fatoora app"
        )
    return Phase2Result(status="valid", has_stamp=has_stamp, notes=notes)


def vat_number_ok(vat_number: str) -> bool:
    """ZATCA VAT registration number: 15 digits, starts with 3, ends with 3."""
    return len(vat_number) == 15 and vat_number.isdigit() and vat_number[0] == "3" and vat_number[-1] == "3"


def amounts_match(qr_value: str, invoice_value: float, tolerance: float = 0.01) -> bool:
    try:
        return abs(float(qr_value) - invoice_value) <= tolerance
    except ValueError:
        return False
