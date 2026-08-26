"""Generate the fully-synthetic `demo-vendor` scenario — an imaginary vendor
(Al-Waha Office Supplies) with a complete claim package, so demos never show
real company data:

  Invoice-Alwaha-INV-2026-0342_real.pdf      QR tags 1-5 + genuinely signed
                                             6-8 (secp256k1) -> phase-2 "valid"
  Invoice-Alwaha-INV-2026-0342_tampered.pdf  signature over a different hash
                                             -> "invalid_signature" (intake fail)
  Contract-BoQ-Alwaha-RFQ26-042_real.pdf     contract (page 1) + 12-line BoQ
                                             (page 2); unit prices match the invoice
  Contract-BoQ-Alwaha-RFQ26-042_tampered.pdf OF-205 priced 290.00 vs invoiced
                                             320.00 (boq.lines_match fail)
  COC-Alwaha-00342_real.pdf                  totals match the claim (230,000.00)
  COC-Alwaha-00342_tampered.pdf              totals 220,000.00 (coc.amount fail)
  Delivery-Alwaha-DN-26-0342_real.pdf        delivered = billed quantities
                                             (three-way match passes)
  Delivery-Alwaha-DN-26-0342_short.pdf       OF-205 delivered 80 vs billed 120
                                             (three_way.billed_vs_received fail)

The claim is a PERIODIC progress payment: the invoice bills 7 of the 12
contracted BoQ lines (200,000.00 of the 620,000.00 contract base) — partial
billing is normal for periodic claims, and typing the claim "final" makes
boq.claim_type_consistent fail with "change the type to periodic" (a scripted
demo beat).

Demo form values: contract value 620,000 (base, excl. VAT), claim type
periodic, payment no. 1, prior payments 0 — see the README next to the PDFs.

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
# invoice_qty 0 = contracted but NOT billed in this periodic claim.
ITEMS = [
    ("OF-101", "مكتب إداري خشبي مع أدراج", "قطعة", 1850.00, 40, 80),
    ("OF-102", "مكتب موظف زاوية مع وحدة جانبية", "قطعة", 1250.00, 0, 60),
    ("OF-205", "كرسي اجتماعات جلد", "قطعة", 320.00, 120, 240),
    ("OF-210", "كرسي مكتبي دوّار شبكي", "قطعة", 500.00, 50, 100),
    ("OF-310", "خزانة ملفات معدنية", "قطعة", 704.00, 25, 50),
    ("OF-315", "خزانة أرشيف متحركة على سكك", "وحدة", 2050.00, 0, 12),
    ("OF-401", "طاولة اجتماعات ١٢ شخصاً مع وحدة توصيلات", "قطعة", 6000.00, 2, 6),
    ("OF-402", "طاولة جانبية للاستقبال", "قطعة", 260.00, 0, 40),
    ("OF-501", "وحدة أدراج متنقلة بثلاثة أدراج", "قطعة", 375.00, 60, 120),
    ("OF-502", "فاصل مكتبي زجاجي مع إطار ألمنيوم", "متر طولي", 400.00, 0, 200),
    ("OF-601", "لوحة كتابة زجاجية ١٢٠×٩٠", "قطعة", 525.00, 20, 40),
    ("OF-702", "أعمال النقل والتركيب والتجميع لكامل الفروع", "مقطوعية", 18000.00, 0, 1),
]
BILLED = [i for i in ITEMS if i[4] > 0]  # the 7 lines this periodic claim bills
BASE = round(sum(p * q for _, _, _, p, q, _ in BILLED), 2)  # 200,000.00
VAT = round(BASE * 0.15, 2)  # 30,000.00
TOTAL = round(BASE + VAT, 2)  # 230,000.00
CONTRACT_BASE = round(sum(p * cq for _, _, _, p, _, cq in ITEMS), 2)  # 620,000.00
CONTRACT_VAT = round(CONTRACT_BASE * 0.15, 2)  # 93,000.00
CONTRACT_TOTAL = round(CONTRACT_BASE + CONTRACT_VAT, 2)  # 713,000.00 incl. VAT

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
.clause { margin: 6pt 0 0 0; text-align: justify; }
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
def render(path: Path, html: str | list[str], qr_payload: str | None = None) -> None:
    """One A4 page per html string; the QR (if any) sits on the first page."""
    doc = pymupdf.open()
    for page_no, page_html in enumerate([html] if isinstance(html, str) else html):
        page = doc.new_page(width=595, height=842)
        top = 130 if qr_payload and page_no == 0 else 36
        page.insert_htmlbox(pymupdf.Rect(36, top, 559, 806), page_html, css=_CSS)
        if qr_payload and page_no == 0:
            qr_png = segno.make(qr_payload, error="m").png_data_uri(scale=4)
            page.insert_image(pymupdf.Rect(36, 30, 126, 120), stream=base64.b64decode(qr_png.split(",", 1)[1]))
    doc.save(path)
    doc.close()
    print(f"wrote {path.relative_to(OUT_ROOT)}")


def invoice_html() -> str:
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{q}</td><td>{p:,.2f}</td><td>{p * q:,.2f}</td></tr>'
        for c, d, _, p, q, _ in BILLED
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
<tr><td colspan="2"><b>نوع المستخلص:</b> دوري — الدفعة رقم ١ (مستخلص جزئي عن الكميات الموردة حتى تاريخه)</td></tr>
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


def contract_page_html() -> str:
    """Page 1 of the combined document: the supply contract itself."""
    return f"""
<body dir="rtl">
<h1>عقد توريد وتركيب أثاث مكتبي — Supply &amp; Installation Contract</h1>
<table class="kv">
<tr><td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td>
    <td><b>تاريخ العقد:</b> <span dir="ltr">2026-05-01</span></td></tr>
<tr><td><b>أمر الشراء:</b> <span dir="ltr">{PO_NO}</span></td>
    <td><b>مدة العقد:</b> حتى <span dir="ltr">2026-11-30</span></td></tr>
<tr><td><b>الطرف الأول (الجهة المشترية):</b> البنك — إدارة المشتريات والعقود</td>
    <td><b>موقع التنفيذ:</b> الفروع — الرياض</td></tr>
<tr><td colspan="2"><b>الطرف الثاني (المورد):</b> {VENDOR["name_ar"]} — سجل تجاري
    <span dir="ltr">{VENDOR["cr"]}</span> — الرقم الضريبي <span dir="ltr">{VENDOR["vat"]}</span></td></tr>
</table>

<h2>المادة الأولى — موضوع العقد</h2>
<p class="clause">توريد وتركيب أثاث مكتبي لفروع الجهة المشترية وفق المواصفات والكميات
الواردة في جدول الكميات (الملحق أ — الصفحة الثانية من هذا المستند)، ويُعد الملحق
جزءاً لا يتجزأ من هذا العقد.</p>

<h2>المادة الثانية — قيمة العقد</h2>
<table>
<tr><th>قيمة العقد قبل الضريبة</th><th>ضريبة القيمة المضافة (15٪)</th><th>قيمة العقد شاملة الضريبة</th></tr>
<tr><td>{CONTRACT_BASE:,.2f} ر.س</td><td>{CONTRACT_VAT:,.2f} ر.س</td><td><b>{CONTRACT_TOTAL:,.2f} ر.س</b></td></tr>
</table>

<h2>المادة الثالثة — المستخلصات والدفعات</h2>
<p class="clause">تُصرف قيمة العقد على مستخلصات دورية وفق الكميات الموردة والمستلمة
فعلياً وبأسعار الوحدة المعتمدة في جدول الكميات، ولا يجوز تجاوز أسعار الوحدات
المتعاقد عليها ولا الكميات التعاقدية. يُقفل العقد بمستخلص نهائي يغطي ما تبقى من
قيمته عند اكتمال التوريد والتركيب، ولا تُقبل مطالبة عن دفعة سبق صرفها.</p>

<h2>المادة الرابعة — الغرامات</h2>
<p class="clause">يخضع التأخير في التوريد أو التركيب لغرامة تأخير حسب النظام، مع
إثبات أي تأخير أو إيقاف أو ملاحظات تنفيذ في محضر الإنجاز الخاص بكل مستخلص.</p>

<table class="sig">
<tr><td><b>الطرف الأول</b><br/>إدارة المشتريات والعقود</td>
    <td><b>الطرف الثاني</b><br/>{VENDOR["name_ar"]}</td></tr>
</table>
{DISCLAIMER}
</body>"""


def boq_page_html(price_of205: float) -> str:
    """Page 2 of the combined document: the Bill of Quantities annex."""
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{cq}</td>'
        f"<td>{(price_of205 if c == 'OF-205' else p):,.2f}</td>"
        f"<td>{(price_of205 if c == 'OF-205' else p) * cq:,.2f}</td></tr>"
        for c, d, u, p, _, cq in ITEMS
    )
    return f"""
<body dir="rtl">
<h1>الملحق (أ) — جدول الكميات — Bill of Quantities</h1>
<table class="kv">
<tr><td><b>المشروع:</b> توريد وتركيب أثاث مكتبي للفروع</td>
    <td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td></tr>
<tr><td><b>المورد:</b> {VENDOR["name_ar"]}</td>
    <td><b>أمر الشراء:</b> <span dir="ltr">{PO_NO}</span></td></tr>
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


def delivery_html(delivered: dict[str, int]) -> str:
    """إشعار تسليم — the buyer-side record of what was actually received.

    The wizard reads this as the product receipt (production pulls the D365
    receipt instead); quantities here are what the three-way match compares
    billed quantities against. Only the lines delivered for THIS claim period
    appear — the unbilled BoQ lines have not been delivered yet."""
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{delivered[c]}</td></tr>'
        for c, d, u, _, _, _ in BILLED
    )
    return f"""
<body dir="rtl">
<h1>إشعار تسليم — Delivery Note</h1>
<table class="kv">
<tr><td><b>رقم الإشعار:</b> <span dir="ltr">DN-26-0342</span></td>
    <td><b>تاريخ التسليم:</b> <span dir="ltr">2026-06-14</span></td></tr>
<tr><td><b>المورد:</b> {VENDOR["name_ar"]}</td>
    <td><b>أمر الشراء:</b> <span dir="ltr">{PO_NO}</span></td></tr>
<tr><td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td>
    <td><b>موقع التسليم:</b> مستودع الفروع — الرياض</td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الوحدة</th><th>الكمية المستلمة</th></tr>
{rows}
</table>
<p>تم استلام الكميات الموضحة أعلاه وفحصها بحالة جيدة ومطابقة للمواصفات، ويُعد هذا
الإشعار سنداً لتنفيذ إيصال استلام المنتجات في نظام تخطيط الموارد.</p>
<table class="sig">
<tr><td><b>مسؤول الاستلام بالمستودع</b><br/>الاسم: فهد ناصر الدوسري<br/>التاريخ: <span dir="ltr">2026-06-14</span></td>
    <td><b>مندوب المورد</b><br/>الاسم: سامي محمد الحربي<br/>التاريخ: <span dir="ltr">2026-06-14</span></td></tr>
</table>
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
    <td><b>عدد دفعات التعميد / العقد:</b> ثلاث دفعات</td></tr>
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


def _cert_kv(rows: list[tuple[str, str]]) -> str:
    return "<table class='kv'>" + "".join(
        f"<tr><td><b>{k}:</b> {v}</td></tr>" for k, v in rows
    ) + "</table>"


def cr_html() -> str:
    return f"""
<body dir="rtl">
<h1>السجل التجاري — Commercial Registration</h1>
<p style="text-align:center">وزارة التجارة — المملكة العربية السعودية</p>
{_cert_kv([
    ("رقم السجل التجاري", f'<span dir="ltr">{VENDOR["cr"]}</span>'),
    ("الاسم التجاري", VENDOR["name_ar"] + " — " + VENDOR["name_en"]),
    ("نوع الكيان", "شركة ذات مسؤولية محدودة"),
    ("النشاط", "بيع وتوريد وتركيب الأثاث والتجهيزات المكتبية"),
    ("مدينة السجل", "الرياض"),
    ("تاريخ الإصدار", '<span dir="ltr">2019-03-12</span>'),
    ("تاريخ الانتهاء", '<span dir="ltr">2027-03-11</span>'),
])}
<p>سجل تجاري ساري المفعول، ويخوّل المنشأة مزاولة النشاط الموضح أعلاه.</p>
{DISCLAIMER}
</body>"""


def zakat_html() -> str:
    return f"""
<body dir="rtl">
<h1>شهادة الزكاة والدخل — Zakat Certificate</h1>
<p style="text-align:center">هيئة الزكاة والضريبة والجمارك</p>
{_cert_kv([
    ("رقم الشهادة", '<span dir="ltr">ZC-26-118455</span>'),
    ("اسم المكلف", VENDOR["name_ar"]),
    ("رقم السجل التجاري", f'<span dir="ltr">{VENDOR["cr"]}</span>'),
    ("الرقم المميز / الرقم الضريبي", f'<span dir="ltr">{VENDOR["vat"]}</span>'),
    ("تاريخ الإصدار", '<span dir="ltr">2026-05-01</span>'),
    ("صالحة حتى", '<span dir="ltr">2027-04-30</span>'),
])}
<p>تشهد الهيئة بأن المكلف المذكور أعلاه قدّم إقراراته وسدّد المستحق عليه، وتُمنح هذه
الشهادة لتمكينه من التعامل مع الجهات الحكومية وصرف مستحقاته النهائية.</p>
{DISCLAIMER}
</body>"""


def gosi_html() -> str:
    return f"""
<body dir="rtl">
<h1>شهادة التأمينات الاجتماعية — GOSI Certificate</h1>
<p style="text-align:center">المؤسسة العامة للتأمينات الاجتماعية</p>
{_cert_kv([
    ("رقم الشهادة", '<span dir="ltr">GOSI-26-774210</span>'),
    ("اسم المنشأة", VENDOR["name_ar"]),
    ("رقم السجل التجاري", f'<span dir="ltr">{VENDOR["cr"]}</span>'),
    ("رقم المنشأة في التأمينات", '<span dir="ltr">504192837</span>'),
    ("تاريخ الإصدار", '<span dir="ltr">2026-06-01</span>'),
    ("صالحة حتى", '<span dir="ltr">2026-12-31</span>'),
])}
<p>تشهد المؤسسة بأن المنشأة المذكورة أعلاه ملتزمة بأحكام نظام التأمينات الاجتماعية
وسددت الاشتراكات المستحقة عليها حتى تاريخه.</p>
{DISCLAIMER}
</body>"""


def award_letter_html() -> str:
    return f"""
<body dir="rtl">
<h1>خطاب الترسية — Award Letter</h1>
<p style="text-align:center">البنك — إدارة المشتريات والعقود</p>
{_cert_kv([
    ("رقم خطاب الترسية", '<span dir="ltr">26400871</span>'),
    ("التاريخ", '<span dir="ltr">2026-04-20</span>'),
    ("المنافسة", f'توريد وتركيب أثاث مكتبي للفروع — <span dir="ltr">{CONTRACT_NO}</span>'),
    ("المورد المرسى عليه", f'{VENDOR["name_ar"]} — سجل تجاري <span dir="ltr">{VENDOR["cr"]}</span>'),
    ("قيمة الترسية (شامل الضريبة)", f"{CONTRACT_TOTAL:,.2f} ر.س"),
])}
<p>نفيدكم بأنه تمت الموافقة على ترسية المنافسة الموضحة أعلاه عليكم وفق نظام
المنافسات والمشتريات الحكومية ولائحته التنفيذية. نأمل مراجعة إدارة المشتريات
والعقود لاستكمال إجراءات توقيع العقد خلال المدة النظامية.</p>
<table class="sig">
<tr><td><b>إدارة المشتريات والعقود</b><br/>التاريخ: <span dir="ltr">2026-04-20</span></td></tr>
</table>
{DISCLAIMER}
</body>"""


def work_commencement_html() -> str:
    return f"""
<body dir="rtl">
<h1>محضر البدء بالأعمال — Work Commencement Minutes</h1>
{_cert_kv([
    ("رقم المحضر", '<span dir="ltr">WC-26-0342</span>'),
    ("التاريخ", '<span dir="ltr">2026-05-10</span>'),
    ("العقد", f'<span dir="ltr">{CONTRACT_NO}</span> — توريد وتركيب أثاث مكتبي للفروع'),
    ("المورد", VENDOR["name_ar"]),
    ("الموقع", "الفروع — الرياض"),
])}
<p>بتاريخه تم تسليم الموقع للمورد المذكور أعلاه ومباشرته الأعمال محل العقد، ويُعد
هذا التاريخ بداية احتساب مدة التنفيذ.</p>
<table class="sig">
<tr><td><b>ممثل الجهة</b><br/>الاسم: ماجد عبدالله السالم</td>
    <td><b>ممثل المورد</b><br/>الاسم: سامي محمد الحربي</td></tr>
</table>
{DISCLAIMER}
</body>"""


README = f"""# demo-vendor — fully synthetic claim scenario

Imaginary vendor ({VENDOR["name_en"]} / {VENDOR["name_ar"]}) — safe for demos,
no real company data. Generated by backend/scripts/generate_demo_scenario.py.

A PERIODIC progress claim: the invoice bills 7 of the 12 contracted BoQ lines
({BASE:,.2f} of the {CONTRACT_BASE:,.2f} contract base). Partial billing is
expected for periodic claims — the unbilled lines simply aren't due yet.

## Files
- Invoice-Alwaha-{INVOICE_NO}_real.pdf      — QR phase-2 signature VERIFIES (intake passes)
- Invoice-Alwaha-{INVOICE_NO}_tampered.pdf  — QR signature does NOT verify (intake fails)
- Contract-BoQ-Alwaha-RFQ26-042_real.pdf    — contract (p.1) + 12-line BoQ (p.2);
                                              prices match the invoice
- Contract-BoQ-Alwaha-RFQ26-042_tampered.pdf— OF-205 priced 290.00 vs invoiced 320.00
- COC-Alwaha-00342_real.pdf                 — totals match ({TOTAL:,.2f})
- COC-Alwaha-00342_tampered.pdf             — totals 220,000.00 (mismatch)
- Delivery-Alwaha-DN-26-0342_real.pdf       — delivered = billed quantities (three-way passes)
- Delivery-Alwaha-DN-26-0342_short.pdf      — OF-205 delivered 80 vs billed 120
                                              (three-way fails: over-billing + claimed
                                              {BASE:,.2f} vs 187,200.00 of received work)

Vendor-file documents for the pre-finance gate (step 5 uploads — the agent
identifies each one and lifts its identity fields; all numbers cross-tie to
the rest of the chain):
- CR-Alwaha-{VENDOR["cr"]}.pdf              — commercial registration (CR {VENDOR["cr"]})
- Zakat-Alwaha-Certificate.pdf              — zakat certificate (VAT {VENDOR["vat"]}, valid to 2027-04-30)
- GOSI-Alwaha-Certificate.pdf               — GOSI certificate (valid to 2026-12-31)
- AwardLetter-Alwaha-26400871.pdf           — award letter no. 26400871 (same no. printed on the COC)
- WorkCommencement-Alwaha-RFQ26-042.pdf     — site handover 2026-05-10 (same date on the COC)
(contract + BoQ are covered by the combined step-2 document)

## Form values for the guided review
(the invoice upload autofills vendor / invoice no. / date / amounts)

- Purchase order:        {PO_NO}
- Project / contract no: {CONTRACT_NO}
- Contract value (base): {CONTRACT_BASE:,.0f}        (excl. VAT, the BoQ line total — or leave
                         empty: uploading the contract/BoQ suggests it into the field)
- Claim type:            Periodic — payment no. 1, prior payments 0
- Contract kind:         GOODS (supply) — so step 3 asks for the goods receipt /
                         delivery note as the acceptance document (the COC files
                         are kept for a works-kind variant of the same chain)
- Contract end date:     2026-11-30 (suggested from the contract page; feeds the
                         step-4 delay inference — delivery 2026-06-14 is on time)
- Step 3:                upload the delivery note (acceptance) -> three-way match
- Step 4:                penalties (none) -> final check infers delay from dates
- Expected result:       _real set -> Recommend approve; any _tampered/_short
                         file flips its gate to Fail; omitting the delivery note
                         -> Review (acceptance not evidenced).

## Scripted demo beats
1. Upload the TAMPERED invoice at step 1 -> the QR's phase-2 signature fails
   ECDSA verification -> intake FAILS ("you cannot photoshop this").
2. Re-upload the REAL invoice -> intake passes.
3. At step 1 or 2, flip the claim type to FINAL -> the contract & BoQ gate
   FAILS: {round(CONTRACT_BASE - BASE, 2):,.2f} of the contract value would remain
   unclaimed, "change the claim type to periodic" (checks the disbursement
   record like the client's own reviewers do).
4. Set it back to PERIODIC -> everything passes; the line-item table shows the
   7 billed lines matching the BoQ and the 5 unbilled lines as "not billed
   this period" (normal for periodic claims).
5. Step 3: delivery note (goods receipt) -> three-way match passes; step 4:
   no penalties, delivery 2026-06-14 vs contract end 2026-11-30 -> on time.
6. Step 5: upload the five vendor-file documents (CR, zakat, GOSI, award
   letter, work commencement) -> the agent identifies each one and reads its
   identity fields (CR number, VAT number, document no., validity); contract
   + BoQ are auto-covered by the step-2 document; the completeness gate
   verifies what the agent SAW, not a checkbox. Withhold one file to show the
   gate fail on a genuinely missing document.
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # The combined contract+BoQ replaces the old BoQ-only files.
    for stale in OUT_DIR.glob("BoQ-Alwaha-*.pdf"):
        stale.unlink()
        print(f"removed stale {stale.name}")
    render(OUT_DIR / f"Invoice-Alwaha-{INVOICE_NO}_real.pdf", invoice_html(), payload_valid())
    render(OUT_DIR / f"Invoice-Alwaha-{INVOICE_NO}_tampered.pdf", invoice_html(), payload_tampered())
    render(OUT_DIR / "Contract-BoQ-Alwaha-RFQ26-042_real.pdf", [contract_page_html(), boq_page_html(price_of205=320.00)])
    render(OUT_DIR / "Contract-BoQ-Alwaha-RFQ26-042_tampered.pdf", [contract_page_html(), boq_page_html(price_of205=290.00)])
    render(OUT_DIR / "COC-Alwaha-00342_real.pdf", coc_html(BASE, VAT, TOTAL))
    render(OUT_DIR / "COC-Alwaha-00342_tampered.pdf", coc_html(190000.00, 30000.00, 220000.00))
    render(
        OUT_DIR / "Delivery-Alwaha-DN-26-0342_real.pdf",
        delivery_html({c: q for c, _, _, _, q, _ in BILLED}),
    )
    render(
        OUT_DIR / "Delivery-Alwaha-DN-26-0342_short.pdf",
        delivery_html({c: (80 if c == "OF-205" else q) for c, _, _, _, q, _ in BILLED}),
    )
    render(OUT_DIR / f"CR-Alwaha-{VENDOR['cr']}.pdf", cr_html())
    render(OUT_DIR / "Zakat-Alwaha-Certificate.pdf", zakat_html())
    render(OUT_DIR / "GOSI-Alwaha-Certificate.pdf", gosi_html())
    render(OUT_DIR / "AwardLetter-Alwaha-26400871.pdf", award_letter_html())
    render(OUT_DIR / "WorkCommencement-Alwaha-RFQ26-042.pdf", work_commencement_html())
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
