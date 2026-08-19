"""Generate the fully-synthetic `demo-vendor` scenario — an imaginary vendor
(Al-Waha Office Supplies) with a complete claim package, so demos never show
real company data:

  Invoice-Alwaha-INV-2026-0342_real.pdf      QR tags 1-5 + genuinely signed
                                             6-8 (secp256k1) -> phase-2 "valid"
  Invoice-Alwaha-INV-2026-0342_tampered.pdf  signature over a different hash
                                             -> "invalid_signature" (intake fail)
  BoQ-Alwaha-RFQ26-042_real.pdf              unit prices match the invoice
  BoQ-Alwaha-RFQ26-042_tampered.pdf          OF-205 priced 290.00 vs invoiced
                                             320.00 (boq.lines_match fail)
  COC-Alwaha-00342_real.pdf                  totals match the claim (149,500.00)
  COC-Alwaha-00342_tampered.pdf              totals 139,500.00 (coc.amount fail)

Demo form values: contract value 299,000 (incl. VAT), claim type periodic,
payment no. 1, prior payments 0 — see the README written next to the PDFs.

Both invoices are registered in test_scenarios/manifest.json so the pinned
round-trip test (tests/test_generated_invoices.py) covers their QR
classification.

Dev tool (segno + pymupdf + cryptography — AGPL pymupdf never ships in the
product). Run:  .venv/Scripts/python scripts/generate_demo_scenario.py
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pymupdf
import segno
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

OUT_ROOT = Path(__file__).resolve().parents[2] / "supporting_docs" / "test_scenarios"
OUT_DIR = OUT_ROOT / "demo-vendor"

# Fixed scalar -> reproducible keypair (test material only, never a real key).
_KEY = ec.derive_private_key(0xA17A7A0FF1CE5CA1E, ec.SECP256K1())

VENDOR = {
    "name_ar": "شركة الواحة لتجهيزات المكاتب",
    "name_en": "Al-Waha Office Supplies Co.",
    "vat": "310987654300003",
    "cr": "1010777045",
}
INVOICE_NO = "INV-2026-0342"
CONTRACT_NO = "RFQ26/042"
PO_NO = "PO26-00214"
DATE, TS = "2026-06-15", "2026-06-15T10:30:00Z"

# (item_code, description, unit, unit_price, invoice_qty, contract_qty)
ITEMS = [
    ("OF-101", "مكتب إداري خشبي مع أدراج", "قطعة", 1850.00, 40, 80),
    ("OF-205", "كرسي اجتماعات جلد", "قطعة", 320.00, 120, 240),
    ("OF-310", "خزانة ملفات معدنية", "قطعة", 704.00, 25, 50),
]
BASE = round(sum(p * q for _, _, _, p, q, _ in ITEMS), 2)  # 130,000.00
VAT = round(BASE * 0.15, 2)  # 19,500.00
TOTAL = round(BASE + VAT, 2)  # 149,500.00
CONTRACT_BASE = round(sum(p * cq for _, _, _, p, _, cq in ITEMS), 2)  # 260,000.00
CONTRACT_TOTAL = round(CONTRACT_BASE * 1.15, 2)  # 299,000.00 incl. VAT

_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1a1a1a; }
h1 { font-size: 14pt; margin: 0 0 4pt 0; text-align: center; }
h2 { font-size: 10pt; margin: 10pt 0 2pt 0; }
table { width: 100%; border-collapse: collapse; margin-top: 6pt; }
th, td { border: 0.5pt solid #444; padding: 4pt 6pt; text-align: right; }
th { background-color: #1f3a63; color: #ffffff; font-weight: bold; }
.kv td { border: none; padding: 1.5pt 4pt; }
.q { background-color: #eef1f7; }
.note { color: #555; font-size: 7pt; margin-top: 10pt; text-align: center; }
.sig td { border: none; padding-top: 18pt; text-align: center; }
"""

DISCLAIMER = (
    '<p class="note">مستند اختباري مولّد آليا لأغراض تطوير وكيل سلامة المطالبات — جهة وبيانات خيالية بالكامل.<br/>'
    "Synthetic test document generated for the Claim Integrity Agent — fictional vendor and data.</p>"
)


# ------------------------------------------------------------------ QR build
def tlv(tag: int, value: bytes | str) -> bytes:
    b = value.encode("utf-8") if isinstance(value, str) else value
    return bytes([tag, len(b)]) + b


def phase1_tags() -> bytes:
    return (
        tlv(1, VENDOR["name_ar"])
        + tlv(2, VENDOR["vat"])
        + tlv(3, TS)
        + tlv(4, f"{TOTAL:.2f}")
        + tlv(5, f"{VAT:.2f}")
    )


def invoice_hash() -> bytes:
    return hashlib.sha256(
        json.dumps([i[:2] for i in ITEMS], ensure_ascii=False).encode() + INVOICE_NO.encode()
    ).digest()


def sign(digest: bytes) -> bytes:
    return _KEY.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))


def public_der() -> bytes:
    return _KEY.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def payload_valid() -> str:
    d = invoice_hash()
    return base64.b64encode(
        phase1_tags() + tlv(6, base64.b64encode(d).decode()) + tlv(7, sign(d)) + tlv(8, public_der())
    ).decode()


def payload_tampered() -> str:
    # Signature over the ORIGINAL hash, but tag 6 carries a different one.
    return base64.b64encode(
        phase1_tags()
        + tlv(6, base64.b64encode(hashlib.sha256(b"someone-else's-invoice").digest()).decode())
        + tlv(7, sign(invoice_hash()))
        + tlv(8, public_der())
    ).decode()


# ------------------------------------------------------------------ renderers
def render(path: Path, html: str, qr_payload: str | None = None) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    top = 130 if qr_payload else 36
    page.insert_htmlbox(pymupdf.Rect(36, top, 559, 806), html, css=_CSS)
    if qr_payload:
        qr_png = segno.make(qr_payload, error="m").png_data_uri(scale=4)
        page.insert_image(pymupdf.Rect(36, 30, 126, 120), stream=base64.b64decode(qr_png.split(",", 1)[1]))
    doc.save(path)
    doc.close()
    print(f"wrote {path.relative_to(OUT_ROOT)}")


def invoice_html() -> str:
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{q}</td><td>{p:,.2f}</td><td>{p * q:,.2f}</td></tr>'
        for c, d, _, p, q, _ in ITEMS
    )
    return f"""
<body dir="rtl">
<h1>فاتورة ضريبية — TAX INVOICE</h1>
<p style="text-align:center"><b>{VENDOR["name_ar"]}</b> — {VENDOR["name_en"]}<br/>
السجل التجاري: <span dir="ltr">{VENDOR["cr"]}</span> &nbsp;|&nbsp; الرقم الضريبي: <span dir="ltr">{VENDOR["vat"]}</span></p>
<table class="kv">
<tr><td><b>رقم الفاتورة:</b> <span dir="ltr">{INVOICE_NO}</span></td>
    <td><b>تاريخ الفاتورة:</b> <span dir="ltr">{DATE}</span></td></tr>
<tr><td><b>أمر الشراء:</b> <span dir="ltr">{PO_NO}</span></td>
    <td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الكمية</th><th>سعر الوحدة (ر.س)</th><th>المجموع (ر.س)</th></tr>
{rows}
</table>
<table class="kv">
<tr><td><b>الإجمالي قبل الضريبة:</b> {BASE:,.2f} ر.س</td>
    <td><b>ضريبة القيمة المضافة (15٪):</b> {VAT:,.2f} ر.س</td>
    <td><b>الإجمالي شامل الضريبة:</b> <b>{TOTAL:,.2f} ر.س</b></td></tr>
</table>
{DISCLAIMER}
</body>"""


def boq_html(price_of205: float) -> str:
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{cq}</td>'
        f"<td>{(price_of205 if c == 'OF-205' else p):,.2f}</td>"
        f"<td>{(price_of205 if c == 'OF-205' else p) * cq:,.2f}</td></tr>"
        for c, d, u, p, _, cq in ITEMS
    )
    return f"""
<body dir="rtl">
<h1>جدول الكميات — Bill of Quantities</h1>
<table class="kv">
<tr><td><b>المشروع:</b> توريد وتركيب أثاث مكتبي للفروع</td>
    <td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td></tr>
<tr><td><b>المورد:</b> {VENDOR["name_ar"]}</td>
    <td><b>أمر الشراء:</b> <span dir="ltr">{PO_NO}</span></td></tr>
<tr><td><b>تاريخ العقد:</b> <span dir="ltr">2026-05-01</span></td>
    <td><b>قيمة العقد شاملة الضريبة:</b> {CONTRACT_TOTAL:,.2f} ر.س</td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الوحدة</th><th>الكمية التعاقدية</th><th>سعر الوحدة (ر.س)</th><th>الإجمالي (ر.س)</th></tr>
{rows}
<tr><td colspan="5"><b>المجموع قبل الضريبة</b></td><td><b>{CONTRACT_BASE:,.2f}</b></td></tr>
</table>
<p>ملاحظة: تصرف المستخلصات الدورية حسب الكميات الموردة فعلياً وبأسعار الوحدات المعتمدة أعلاه،
ولا يجوز تجاوز أسعار الوحدات المتعاقد عليها.</p>
{DISCLAIMER}
</body>"""


def coc_html(base: float, vat: float, total: float) -> str:
    return f"""
<body dir="rtl">
<h1>محضر الإنجاز — Certificate of Completion</h1>
<table class="kv">
<tr><td><b>التاريخ:</b> <span dir="ltr">2026-06-18</span></td>
    <td><b>اسم المؤسسة / الشركة:</b> {VENDOR["name_ar"]}</td></tr>
<tr><td><b>رقم المطالبة المالية:</b> <span dir="ltr">{INVOICE_NO}</span></td>
    <td><b>عدد دفعات التعميد / العقد:</b> دفعتان</td></tr>
<tr><td><b>نوع المستخلص:</b> دوري</td>
    <td><b>رقم محضر الإنجاز:</b> <span dir="ltr">COC-000000355</span></td></tr>
<tr><td><b>قيمة طلب التغيير:</b> 0.00</td>
    <td><b>قيمة العقد النهائية (شامل الضريبة):</b> {CONTRACT_TOTAL:,.2f}</td></tr>
</table>

<table>
<tr><th>رقم التعميد / العقد</th><th>تاريخ التعميد / العقد</th><th>تاريخ نهاية التعميد / العقد</th><th>قيمة التعميد / العقد (شامل الضريبة)</th></tr>
<tr><td><span dir="ltr">{CONTRACT_NO}</span></td><td><span dir="ltr">2026-05-01</span></td><td><span dir="ltr">2026-11-30</span></td><td>{CONTRACT_TOTAL:,.2f}</td></tr>
<tr><th>ترتيب الدفعة الحالية</th><th>قيمة المطالبة الحالية</th><th>مبلغ الضريبة</th><th>إجمالي المطالبة</th></tr>
<tr><td>الدفعة الأولى</td><td>{base:,.2f}</td><td>{vat:,.2f}</td><td>{total:,.2f}</td></tr>
<tr><th colspan="2">رقم خطاب الترسية</th><th colspan="2">تاريخ محضر تسليم الموقع للمورد / بداية الخدمة</th></tr>
<tr><td colspan="2"><span dir="ltr">26400871</span></td><td colspan="2"><span dir="ltr">2026-05-10</span></td></tr>
</table>

<table>
<tr class="q"><th colspan="2">هل يوجد إيقاف و إعادة استئناف للمشروع؟</th><th colspan="2">هل هناك تأخير في تنفيذ الأعمال المطلوبة؟</th></tr>
<tr><td>نعم ☐</td><td>لا ☑</td><td>نعم ☐ &nbsp; (عدد الأيام: 0)</td><td>لا ☑</td></tr>
<tr class="q"><th colspan="4">هل هناك ملاحظات على التنفيذ؟</th></tr>
<tr><td colspan="2">نعم ☐</td><td colspan="2">لا ☑</td></tr>
</table>

<p>إشارة إلى أمر التوريد الموضح بياناته أعلاه، نفيدكم بأن الكميات المذكورة بالفاتورة تم
توريدها واستلامها بالكامل وفقاً للمواصفات المتفق عليها، دون أي تأخير. لذا نأمل صرف
كامل قيمة الفاتورة للمورد.</p>

<table class="sig">
<tr><td><b>مدير المشروع</b><br/>الاسم: ماجد عبدالله السالم<br/>التاريخ: <span dir="ltr">2026-06-18</span></td>
    <td><b>المدير التنفيذي للإدارة المشرفة</b><br/>الاسم: نورة سعد العتيق<br/>التاريخ: <span dir="ltr">2026-06-18</span></td></tr>
</table>
{DISCLAIMER}
</body>"""


README = f"""# demo-vendor — fully synthetic claim scenario

Imaginary vendor ({VENDOR["name_en"]} / {VENDOR["name_ar"]}) — safe for demos,
no real company data. Generated by backend/scripts/generate_demo_scenario.py.

## Files
- Invoice-Alwaha-{INVOICE_NO}_real.pdf      — QR phase-2 signature VERIFIES (intake passes)
- Invoice-Alwaha-{INVOICE_NO}_tampered.pdf  — QR signature does NOT verify (intake fails)
- BoQ-Alwaha-RFQ26-042_real.pdf             — prices match the invoice
- BoQ-Alwaha-RFQ26-042_tampered.pdf         — OF-205 priced 290.00 vs invoiced 320.00
- COC-Alwaha-00342_real.pdf                 — totals match ({TOTAL:,.2f})
- COC-Alwaha-00342_tampered.pdf             — totals 139,500.00 (mismatch)

## Form values for the guided review
(the invoice upload autofills vendor / invoice no. / date / amounts)

- Purchase order:        {PO_NO}
- Project / contract no: {CONTRACT_NO}
- Contract value (base): 299000        (incl. VAT ceiling, as on the BoQ/COC)
- Claim type:            Periodic — payment no. 1, prior payments 0
- Expected result:       _real set -> Recommend approve; any _tampered file
                         flips its gate to Fail.
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render(OUT_DIR / f"Invoice-Alwaha-{INVOICE_NO}_real.pdf", invoice_html(), payload_valid())
    render(OUT_DIR / f"Invoice-Alwaha-{INVOICE_NO}_tampered.pdf", invoice_html(), payload_tampered())
    render(OUT_DIR / "BoQ-Alwaha-RFQ26-042_real.pdf", boq_html(price_of205=320.00))
    render(OUT_DIR / "BoQ-Alwaha-RFQ26-042_tampered.pdf", boq_html(price_of205=290.00))
    render(OUT_DIR / "COC-Alwaha-00342_real.pdf", coc_html(BASE, VAT, TOTAL))
    render(OUT_DIR / "COC-Alwaha-00342_tampered.pdf", coc_html(120000.00, 19500.00, 139500.00))
    (OUT_DIR / "README.md").write_text(README, encoding="utf-8")

    # Register the QR-bearing files in the shared manifest so the pinned
    # round-trip test covers their classification.
    manifest_path = OUT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    common = {
        "invoice_no": INVOICE_NO,
        "vendor_en": VENDOR["name_en"],
        "vendor_ar": VENDOR["name_ar"],
        "vat_number": VENDOR["vat"],
        "total": TOTAL,
    }
    manifest[f"demo-vendor/Invoice-Alwaha-{INVOICE_NO}_real.pdf"] = {
        "fixture": "valid_phase2", "expected_phase2": "valid", **common,
    }
    manifest[f"demo-vendor/Invoice-Alwaha-{INVOICE_NO}_tampered.pdf"] = {
        "fixture": "tampered", "expected_phase2": "invalid_signature", **common,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest updated: {manifest_path}")


if __name__ == "__main__":
    main()
