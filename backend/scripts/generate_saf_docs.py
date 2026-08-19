"""Generate BoQ (جدول الكميات) and COC (محضر الإنجاز) PDFs for the
`unknown_vendor` scenario built around the real ZATCA invoice `saf.pdf`
(seller: مكتب التوزيع الذهبي للخدمات التجارية، INV/2026/00115, phase-2 valid).

Invoice ground truth (decoded from the printed QR + face):
- line: [800] مبرد 800 — 300 units @ 10.20 = 3,060.00
- VAT 459.00, total incl. VAT 3,519.00, date 2026-01-03

Two variants per document:
- *_real.pdf      — consistent with the invoice; the claim should pass.
- *_tampered.pdf  — BoQ: item 800 priced 9.20 (invoice overbills by 1.00/unit);
                    COC: total 3,219.00 (does not match the claim's 3,519.00).

Dev tool (pymupdf — AGPL, never ships in the product). Neutral letterhead on
purpose: we do not fabricate documents carrying the client's real branding.

Run:  .venv/Scripts/python scripts/generate_saf_docs.py
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT_DIR = (
    Path(__file__).resolve().parents[2] / "supporting_docs" / "test_scenarios" / "unknown_vendor"
)

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
    '<p class="note">مستند اختباري مولّد آليا لأغراض تطوير وكيل سلامة المطالبات — ليس مستنداً حقيقياً.<br/>'
    "Synthetic test document generated for the Claim Integrity Agent — not a real document.</p>"
)

VENDOR = "مكتب التوزيع الذهبي للخدمات التجارية (RUH)"
CONTRACT_NO = "S228579"
INVOICE_NO = "INV/2026/00115"


def render(path: Path, html: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(36, 36, 559, 806), html, css=_CSS)
    doc.save(path)
    doc.close()
    print(f"wrote {path.name}")


def boq_html(price_800: float) -> str:
    # Item 800 is the invoiced line; the rest pad the schedule realistically.
    items = [
        ("800", "مبرد 800 — مبرد مياه سعة كبيرة", "قطعة", 300, price_800),
        ("810", "مبرد 400 — مبرد مياه سعة متوسطة", "قطعة", 150, 8.40),
        ("820", "قطع غيار وصيانة مبردات", "مجموعة", 20, 45.00),
    ]
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{q}</td>'
        f"<td>{p:,.2f}</td><td>{p * q:,.2f}</td></tr>"
        for c, d, u, q, p in items
    )
    line_total = 300 * price_800
    vat = round(line_total * 0.15, 2)
    return f"""
<body dir="rtl">
<h1>جدول الكميات — Bill of Quantities</h1>
<table class="kv">
<tr><td><b>المشروع:</b> توريد مبردات مياه لمواقع الجهة</td>
    <td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td></tr>
<tr><td><b>المورد:</b> {VENDOR}</td>
    <td><b>تاريخ العقد:</b> <span dir="ltr">2025-12-15</span></td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الوحدة</th><th>الكمية</th><th>سعر الوحدة (ر.س)</th><th>الإجمالي (ر.س)</th></tr>
{rows}
</table>
<h2>نطاق أمر التوريد الحالي (البند 800)</h2>
<table class="kv">
<tr><td><b>قيمة أمر التوريد قبل الضريبة:</b> {line_total:,.2f}</td>
    <td><b>ضريبة القيمة المضافة (15٪):</b> {vat:,.2f}</td>
    <td><b>الإجمالي شامل الضريبة:</b> {line_total + vat:,.2f}</td></tr>
</table>
<p>ملاحظة: تصرف المستخلصات حسب الكميات الموردة فعلياً وبأسعار الوحدات المعتمدة أعلاه،
ولا يجوز تجاوز أسعار الوحدات المتعاقد عليها.</p>
{DISCLAIMER}
</body>"""


def coc_html(base: float, vat: float, total: float) -> str:
    return f"""
<body dir="rtl">
<h1>محضر الإنجاز — Certificate of Completion</h1>
<table class="kv">
<tr><td><b>التاريخ:</b> <span dir="ltr">2026-01-05</span></td>
    <td><b>اسم المؤسسة / الشركة:</b> {VENDOR}</td></tr>
<tr><td><b>رقم المطالبة المالية:</b> <span dir="ltr">{INVOICE_NO}</span></td>
    <td><b>عدد دفعات التعميد / العقد:</b> دفعة واحدة</td></tr>
<tr><td><b>نوع المستخلص:</b> نهائي</td>
    <td><b>رقم محضر الإنجاز:</b> <span dir="ltr">COC-000000318</span></td></tr>
<tr><td><b>قيمة طلب التغيير:</b> 0.00</td>
    <td><b>قيمة العقد النهائية (شامل الضريبة):</b> 3,519.00</td></tr>
</table>

<table>
<tr><th>رقم التعميد / العقد</th><th>تاريخ التعميد / العقد</th><th>تاريخ نهاية التعميد / العقد</th><th>قيمة التعميد / العقد (شامل الضريبة)</th></tr>
<tr><td><span dir="ltr">{CONTRACT_NO}</span></td><td><span dir="ltr">2025-12-15</span></td><td><span dir="ltr">2026-01-31</span></td><td>3,519.00</td></tr>
<tr><th>ترتيب الدفعة الحالية</th><th>قيمة المطالبة الحالية</th><th>مبلغ الضريبة</th><th>إجمالي المطالبة</th></tr>
<tr><td>الدفعة الأولى والأخيرة</td><td>{base:,.2f}</td><td>{vat:,.2f}</td><td>{total:,.2f}</td></tr>
<tr><th colspan="2">رقم خطاب الترسية</th><th colspan="2">تاريخ محضر تسليم الموقع للمورد / بداية الخدمة</th></tr>
<tr><td colspan="2"><span dir="ltr">25401203</span></td><td colspan="2"><span dir="ltr">2025-12-20</span></td></tr>
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
<tr><td><b>مدير المشروع</b><br/>الاسم: ماجد عبدالله السالم<br/>التاريخ: <span dir="ltr">2026-01-05</span></td>
    <td><b>المدير التنفيذي للإدارة المشرفة</b><br/>الاسم: نورة سعد العتيق<br/>التاريخ: <span dir="ltr">2026-01-05</span></td></tr>
</table>
{DISCLAIMER}
</body>"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Real: consistent with the invoice (300 × 10.20 + 15% VAT = 3,519.00).
    render(OUT_DIR / "BoQ-SAF-S228579_real.pdf", boq_html(price_800=10.20))
    render(OUT_DIR / "COC-SAF-00115_real.pdf", coc_html(base=3060.00, vat=459.00, total=3519.00))
    # Tampered: BoQ contracted price 9.20 (invoice overbills 1.00/unit);
    # COC totals that do not match the claim's 3,519.00.
    render(OUT_DIR / "BoQ-SAF-S228579_tampered.pdf", boq_html(price_800=9.20))
    render(OUT_DIR / "COC-SAF-00115_tampered.pdf", coc_html(base=2800.00, vat=419.00, total=3219.00))


if __name__ == "__main__":
    main()
