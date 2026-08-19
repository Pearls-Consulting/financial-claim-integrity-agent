"""Generate the mock COC (محضر الإنجاز) and BoQ (جدول الكميات) PDFs for the
defective seed claim VRM-002402.

Dev tool (pymupdf — AGPL, never ships in the product). Neutral letterhead on
purpose: we do not fabricate documents carrying the client's real branding.

The two documents embed the claim's planted defects exactly:
- COC: answers "no delay / no stoppage / no observations" while the ERP
  penalty record (seeded on the claim) shows a 25,000 SAR delay fine — the
  client's real-world contradiction case.
- BoQ: CIV-014 unit price 55,000 vs the invoice's 63,333.33 — the line
  deviation the boq.lines_match rule must catch from real files.

Run:  .venv/Scripts/python scripts/generate_claim_docs.py
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT_DIR = (
    Path(__file__).resolve().parents[2] / "supporting_docs" / "test_scenarios" / "modern-construction"
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


def render(path: Path, html: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(36, 36, 559, 806), html, css=_CSS)
    doc.save(path)
    doc.close()
    print(f"wrote {path.name}")


def coc_html() -> str:
    return f"""
<body dir="rtl">
<h1>محضر الإنجاز — Certificate of Completion</h1>
<table class="kv">
<tr><td><b>التاريخ:</b> <span dir="ltr">2026-07-05</span></td>
    <td><b>اسم المؤسسة / الشركة:</b> شركة البناء الحديث للمقاولات</td></tr>
<tr><td><b>رقم المطالبة المالية:</b> <span dir="ltr">INV-2026-0117</span></td>
    <td><b>عدد دفعات التعميد / العقد:</b> حسب الإنجاز</td></tr>
<tr><td><b>نوع المستخلص:</b> دوري</td>
    <td><b>رقم محضر الإنجاز:</b> <span dir="ltr">COC-000000242</span></td></tr>
<tr><td><b>قيمة طلب التغيير:</b> 0.00</td>
    <td><b>قيمة العقد النهائية:</b> 4,200,000.00</td></tr>
</table>

<table>
<tr><th>رقم التعميد / العقد</th><th>تاريخ التعميد / العقد</th><th>تاريخ نهاية التعميد / العقد</th><th>قيمة التعميد / العقد (شامل الضريبة)</th></tr>
<tr><td><span dir="ltr">RFQ25/118</span></td><td><span dir="ltr">2025-10-01</span></td><td><span dir="ltr">2026-12-31</span></td><td>4,200,000.00</td></tr>
<tr><th>ترتيب الدفعة الحالية</th><th>قيمة المطالبة الحالية</th><th>مبلغ الضريبة</th><th>إجمالي المطالبة</th></tr>
<tr><td>الدفعة الخامسة</td><td>380,000.00</td><td>57,000.00</td><td>437,000.00</td></tr>
<tr><th colspan="2">رقم خطاب الترسية</th><th colspan="2">تاريخ محضر تسليم الموقع للمقاول / بداية الخدمة</th></tr>
<tr><td colspan="2"><span dir="ltr">25401188</span></td><td colspan="2"><span dir="ltr">2025-10-15</span></td></tr>
</table>

<table>
<tr class="q"><th colspan="2">هل يوجد إيقاف و إعادة استئناف للمشروع؟</th><th colspan="2">هل هناك تأخير في تنفيذ الأعمال المطلوبة؟</th></tr>
<tr><td>نعم ☐</td><td>لا ☑</td><td>نعم ☐ &nbsp; (عدد الأيام: 0)</td><td>لا ☑</td></tr>
<tr class="q"><th colspan="4">هل هناك ملاحظات على التنفيذ؟</th></tr>
<tr><td colspan="2">نعم ☐</td><td colspan="2">لا ☑</td></tr>
</table>

<p>إشارة إلى المشروع الموضح بياناته أعلاه، نفيدكم بأن الأعمال المذكورة بالفاتورة تم
إنجازها بالكامل وفقاً للمواصفات الفنية المتفق عليها والجدول الزمني المتفق عليه، دون أي
تأخير. لذا نأمل صرف كامل قيمة الفاتورة للمورد.</p>

<table class="sig">
<tr><td><b>مدير المشروع</b><br/>الاسم: ماجد عبدالله السالم<br/>التاريخ: <span dir="ltr">2026-07-05</span></td>
    <td><b>المدير التنفيذي للإدارة المشرفة</b><br/>الاسم: نورة سعد العتيق<br/>التاريخ: <span dir="ltr">2026-07-05</span></td></tr>
</table>
{DISCLAIMER}
</body>"""


def boq_html() -> str:
    items = [
        ("CIV-010", "أعمال ترميم واجهات", "مبنى", 8, 95000.0),
        ("CIV-011", "أعمال عزل أسطح", "مبنى", 8, 30000.0),
        ("CIV-012", "أعمال كهرباء وإنارة", "مبنى", 8, 42000.0),
        ("CIV-013", "أعمال سباكة وصرف", "مبنى", 8, 28000.0),
        ("CIV-014", "أعمال تكييف وتهوية", "مبنى", 8, 55000.0),  # invoice claims 63,333.33
        ("CIV-015", "أعمال تشطيبات داخلية", "مبنى", 8, 68000.0),
    ]
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{q}</td>'
        f"<td>{p:,.2f}</td><td>{p * q:,.2f}</td></tr>"
        for c, d, u, q, p in items
    )
    subtotal = sum(p * q for *_, q, p in [(c, d, u, q, p) for c, d, u, q, p in items])
    return f"""
<body dir="rtl">
<h1>جدول الكميات — Bill of Quantities</h1>
<table class="kv">
<tr><td><b>المشروع:</b> مشروع تأهيل وصيانة مباني الفروع</td>
    <td><b>رقم العقد:</b> <span dir="ltr">RFQ25/118</span></td></tr>
<tr><td><b>المقاول:</b> شركة البناء الحديث للمقاولات</td>
    <td><b>أمر الشراء:</b> <span dir="ltr">PO25-00139</span></td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الوحدة</th><th>الكمية</th><th>سعر الوحدة (ر.س)</th><th>الإجمالي (ر.س)</th></tr>
{rows}
<tr><td colspan="5"><b>المجموع قبل الضريبة</b></td><td><b>{subtotal:,.2f}</b></td></tr>
</table>
<p>ملاحظة: تصرف المستخلصات الدورية حسب نسب الإنجاز الفعلية وبأسعار الوحدات المعتمدة أعلاه،
ولا يجوز تجاوز أسعار الوحدات المتعاقد عليها.</p>
{DISCLAIMER}
</body>"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render(OUT_DIR / "COC-000000242.pdf", coc_html())
    render(OUT_DIR / "BoQ-RFQ25-118.pdf", boq_html())


if __name__ == "__main__":
    main()
