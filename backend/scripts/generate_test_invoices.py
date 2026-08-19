"""Generate deterministic Saudi-style test invoice PDFs with printed QR codes.

Dev tool, not runtime code (deps: segno + pymupdf — install manually, they are
deliberately NOT in requirements.txt; pymupdf is AGPL so it must never ship in
the product, generating internal test fixtures with it is fine).

Covers the whole QR fixture matrix against validators/zatca_qr.py:

  valid_phase2      tags 1-5 + genuinely signed 6-8 (secp256k1)     -> "valid"
  phase1_only       tags 1-5 only                                    -> "absent"
  pseudo_phase2     the wild bug: hex-text hash, 32B sig, UUID key   -> "pseudo"
  tampered          real material, signature over a different hash   -> "invalid_signature"
  fake_face         VRM-002402's seeded QR: fake VAT no + amounts    -> qr_authentic FAIL

The private key is derived from a fixed scalar so repeated runs are
byte-stable. Output: supporting_docs/test_scenarios/<vendor-dir>/ (grouped by
vendor entity so UI testing picks one folder per scenario) + manifest.json
recording each file's expected classification (consumed by tests).

Run:  .venv/Scripts/python scripts/generate_test_invoices.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pymupdf
import segno

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, utils  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "supporting_docs" / "test_scenarios"

# Fixed scalar -> reproducible keypair (test material only, obviously never
# a real signing key).
_KEY = ec.derive_private_key(0x1C1A131E6817C0DE, ec.SECP256K1())


def tlv(tag: int, value: bytes | str) -> bytes:
    b = value.encode("utf-8") if isinstance(value, str) else value
    return bytes([tag, len(b)]) + b


def sign(digest: bytes) -> bytes:
    return _KEY.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


def public_der() -> bytes:
    return _KEY.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


VENDORS = {
    "alufuq": {
        "name_ar": "شركة الأفق لتقنية المعلومات",
        "name_en": "Al-Ufuq IT Company",
        "vat": "310122393500003",
        "cr": "1010555001",
        "dir": "alufuq-it",  # scenario folder under supporting_docs/test_scenarios/
    },
    "albina": {
        "name_ar": "شركة البناء الحديث للمقاولات",
        "name_en": "Modern Construction Contracting Co.",
        "vat": "301455678900017",
        "cr": "1010555002",
        "dir": "modern-construction",
    },
}


def phase1_tags(vendor: dict, ts: str, total: str, vat: str) -> bytes:
    return (
        tlv(1, vendor["name_ar"]) + tlv(2, vendor["vat"]) + tlv(3, ts) + tlv(4, total) + tlv(5, vat)
    )


def build_payloads() -> dict[str, dict]:
    """Returns {fixture_key: {payload, expected_phase2, vendor, face:{...}}}."""
    v1, v2 = VENDORS["alufuq"], VENDORS["albina"]

    def face(vendor, no, date, ts, base, vat, lines):
        return {
            "vendor": vendor,
            "invoice_no": no,
            "date": date,
            "ts": ts,
            "base": base,
            "vat": vat,
            "total": round(base + vat, 2),
            "lines": lines,
        }

    f_valid = face(v1, "TINV-2026-1001", "2026-08-10", "2026-08-10T09:15:00Z", 120000.0, 18000.0,
                   [("SW-01", "تطوير وتخصيص بوابة الخدمات", 60000.0, 2)])
    f_p1 = face(v1, "TINV-2026-1002", "2026-08-11", "2026-08-11T11:00:00Z", 40000.0, 6000.0,
                [("SW-02", "دعم فني سنوي", 40000.0, 1)])
    f_pseudo = face(v1, "TINV-2026-1003", "2026-08-12", "2026-08-12T14:30:00Z", 80000.0, 12000.0,
                    [("SW-03", "رخص برمجيات", 20000.0, 4)])
    f_tampered = face(v1, "TINV-2026-1004", "2026-08-13", "2026-08-13T10:45:00Z", 65000.0, 9750.0,
                      [("SW-04", "خدمات استشارية", 65000.0, 1)])
    # Face values MUST mirror the seeded VRM-002402 invoice document.
    f_fake = face(v2, "INV-2026-0117", "2026-05-01", "2026-05-01T09:00:00Z", 380000.0, 57000.0,
                  [("CIV-010", "أعمال ترميم واجهات", 95000.0, 2), ("CIV-014", "أعمال تكييف وتهوية", 63333.33, 3)])

    out: dict[str, dict] = {}

    def hash_of(f) -> bytes:
        # Stand-in for the signed-XML hash: deterministic over the face values.
        return hashlib.sha256(json.dumps(f["lines"], ensure_ascii=False).encode() + f["invoice_no"].encode()).digest()

    d = hash_of(f_valid)
    out["valid_phase2"] = {
        "face": f_valid,
        "expected_phase2": "valid",
        "payload": base64.b64encode(
            phase1_tags(v1, f_valid["ts"], f"{f_valid['total']:.2f}", f"{f_valid['vat']:.2f}")
            + tlv(6, base64.b64encode(d).decode())
            + tlv(7, sign(d))
            + tlv(8, public_der())
        ).decode(),
    }

    out["phase1_only"] = {
        "face": f_p1,
        "expected_phase2": "absent",
        "payload": base64.b64encode(
            phase1_tags(v1, f_p1["ts"], f"{f_p1['total']:.2f}", f"{f_p1['vat']:.2f}")
        ).decode(),
    }

    d = hash_of(f_pseudo)
    out["pseudo_phase2"] = {
        "face": f_pseudo,
        "expected_phase2": "pseudo",
        "payload": base64.b64encode(
            phase1_tags(v1, f_pseudo["ts"], f"{f_pseudo['total']:.2f}", f"{f_pseudo['vat']:.2f}")
            + tlv(6, base64.b64encode(d.hex().encode()).decode())  # the wild double-encoding bug
            + tlv(7, base64.b64encode(hashlib.sha256(d).digest()).decode())  # 32B non-signature
            + tlv(8, "8f2a1c34-77aa-4de2-9b1d-2f80f1e6c9a1")  # UUID posing as a key
        ).decode(),
    }

    d = hash_of(f_tampered)
    out["tampered"] = {
        "face": f_tampered,
        "expected_phase2": "invalid_signature",
        "payload": base64.b64encode(
            phase1_tags(v1, f_tampered["ts"], f"{f_tampered['total']:.2f}", f"{f_tampered['vat']:.2f}")
            + tlv(6, base64.b64encode(hashlib.sha256(b"different-invoice").digest()).decode())
            + tlv(7, sign(d))  # signature over the ORIGINAL hash -> mismatch
            + tlv(8, public_der())
        ).decode(),
    }

    # Same payload already seeded on VRM-002402: declared seller/amounts lie.
    out["fake_face"] = {
        "face": f_fake,
        "expected_phase2": "pseudo_or_absent_irrelevant",
        "payload": "ASbZhdik2LPYs9ipINin2YTZhtis2KfYrSDZhNmE2KrYrNin2LHYqQIFMTIzNDUDFDIwMjYtMDUtMDFUMDk6MDA6MDBaBAk5OTk5OTkuMDAFBDAuMDA=",
    }
    return out


_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1a1a1a; }
h1 { font-size: 15pt; margin: 0; }
table { width: 100%; border-collapse: collapse; margin-top: 8pt; }
th, td { border: 0.5pt solid #999; padding: 4pt 6pt; text-align: right; }
th { background-color: #b9c7e2; }
.meta td { border: none; padding: 1pt 4pt; }
.totals td { border: none; padding: 2pt 6pt; }
.note { color: #555; font-size: 7pt; }
"""


def render_pdf(path: Path, fx: dict) -> None:
    face, vendor = fx["face"], fx["face"]["vendor"]
    rows = "".join(
        f"<tr><td>{c}</td><td>{d}</td><td>{q}</td><td>{u:,.2f}</td><td>{u * q:,.2f}</td></tr>"
        for c, d, u, q in face["lines"]
    )
    html = f"""
<body dir="rtl">
<h1>فاتورة ضريبية &nbsp; TAX INVOICE</h1>
<p><b>{vendor["name_ar"]}</b> — {vendor["name_en"]}<br/>
السجل التجاري: {vendor["cr"]} &nbsp;|&nbsp; الرقم الضريبي: {vendor["vat"]}</p>
<table class="meta">
<tr><td><b>رقم الفاتورة:</b> <span dir="ltr">{face["invoice_no"]}</span></td><td><b>تاريخ الفاتورة:</b> <span dir="ltr">{face["date"]}</span></td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الكمية</th><th>سعر الوحدة</th><th>المجموع</th></tr>
{rows}
</table>
<table class="totals">
<tr><td><b>الإجمالي قبل الضريبة</b></td><td>{face["base"]:,.2f} ر.س</td></tr>
<tr><td><b>ضريبة القيمة المضافة (15%)</b></td><td>{face["vat"]:,.2f} ر.س</td></tr>
<tr><td><b>الإجمالي شامل الضريبة</b></td><td><b>{face["total"]:,.2f} ر.س</b></td></tr>
</table>
<p class="note">مستند اختباري مولّد آليا لأغراض تطوير وكيل سلامة المطالبات — ليس فاتورة حقيقية.<br/>
Synthetic test document generated for the Claim Integrity Agent — not a real invoice.</p>
</body>"""

    qr_png = segno.make(fx["payload"], error="m").png_data_uri(scale=4)
    qr_bytes = base64.b64decode(qr_png.split(",", 1)[1])

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_htmlbox(pymupdf.Rect(36, 130, 559, 720), html, css=_CSS)
    page.insert_image(pymupdf.Rect(36, 30, 126, 120), stream=qr_bytes)
    doc.save(path)
    doc.close()


def main() -> None:
    fixtures = build_payloads()
    manifest = {}
    for key, fx in fixtures.items():
        vendor = fx["face"]["vendor"]
        name = f"{vendor['dir']}/TEST-{key}-{fx['face']['invoice_no']}.pdf"
        (OUT_DIR / vendor["dir"]).mkdir(parents=True, exist_ok=True)
        render_pdf(OUT_DIR / name, fx)
        manifest[name] = {
            "fixture": key,
            "expected_phase2": fx["expected_phase2"],
            "invoice_no": fx["face"]["invoice_no"],
            "vendor_en": vendor["name_en"],
            "vendor_ar": vendor["name_ar"],
            "vat_number": vendor["vat"],
            "total": fx["face"]["total"],
        }
        print(f"wrote {name}")
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
