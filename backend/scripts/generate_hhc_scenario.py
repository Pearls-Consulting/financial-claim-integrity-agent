"""Generate the `hhc-fitout` scenario — the COMPLEX-CONTRACT showcase.

Unlike demo-vendor (fully synthetic), this scenario is built around a REAL
76-page scanned works contract (HHC00050 — تأهيل المقر الرئيسي لشركة الصحة
القابضة): handwritten-signed, stamped, with the penalty clauses on p.37
(delay penalty ≤10% of the BoQ line value, total penalties capped at 20% of
the contract value), a 4-milestone payment schedule on p.36, and a priced
BoQ appendix on rotated landscape pages. The synthetic documents around it
(invoice, COC, work-commencement minutes) bill REAL BoQ lines at the real
unit prices, so every gate exercises the actual contract content:

  Contract-HHC00050-Fitout.pdf            the real contract, copied verbatim
  Invoice-AlBait-INV-2026-0518_real.pdf   مستخلص ٢ — 8 real BoQ lines, QR signed
  Invoice-AlBait-INV-2026-0518_overpriced.pdf
                                          line 9.10 billed 200.00 vs BoQ 180.00
                                          (boq.lines_match fail)
  COC-HHC-00518_ontime.pdf                dated within the contract period
  COC-HHC-00518_late.pdf                  dated 20 days AFTER the period ends,
                                          delay declared -> the final check
                                          demands a penalty per contract p.37
  WorkCommencement-HHC00050.pdf           محضر بدء المشروع 2026-02-16 — the
                                          start the 5-month duration runs from

Timeline: site handover 2026-02-16, contract duration 5 months (clause 1.5)
-> contractual end 2026-07-16. The _late COC (2026-08-05) is 20 days over.

Dev tool (segno + pymupdf — AGPL pymupdf never ships in the product).
Run:  .venv/Scripts/python scripts/generate_hhc_scenario.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pymupdf
import segno
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = PROJECT_ROOT / "supporting_docs" / "test_scenarios"
OUT_DIR = OUT_ROOT / "hhc-fitout"
CONTRACT_SRC = (
    PROJECT_ROOT
    / "supporting_docs"
    / "contracts"
    / "hhc"
    / "_عقد تأهيل المقر الرئيسي لشركة الصحة القابضة من الطابق الأول وحتى الطابق التاسع (1).pdf"
)
CONTRACT_DEST = "Contract-HHC00050-Fitout.pdf"

# Fixed scalar -> reproducible keypair (test material only, never a real key).
_KEY = ec.derive_private_key(0xA17A7A0FF1CE5CA1E, ec.SECP256K1())

VENDOR = {
    # The contract's real second party; the identity numbers are synthetic.
    "name_ar": "شركة البيت الإنشائي للمقاولات المحدودة",
    "name_en": "Al-Bait Construction Co. Ltd.",
    "vat": "310123456700003",
    "cr": "1010201140",
}
CONTRACT_NO = "HHC00050"
INVOICE_NO = "INV-2026-0518"
COC_NO = "COC-HHC-00518"
DATE, TS = "2026-07-10", "2026-07-10T09:00:00Z"
START_DATE = "2026-02-16"  # محضر بدء المشروع (site handover)
END_DATE = "2026-07-16"  # + 5 months (contract clause 1.5)
COC_DATE_ONTIME = "2026-07-12"
COC_DATE_LATE = "2026-08-05"  # 20 days after the contractual end

CONTRACT_BASE = 20_100_000.00  # excl. VAT (contract clause 1.4: 23,115,000 incl.)
CONTRACT_TOTAL = 23_115_000.00
CUMULATIVE_PRIOR = 5_025_000.00  # payment 1 (25% — "بعد بداية الأعمال", p.36)

# REAL BoQ lines from the contract's جدول الكميات appendix (rotated pages):
# (item_code, description, unit, unit_price, billed_qty, contract_qty)
ITEMS = [
    ("6.10", "بلاط بورسلين للأرضيات قياس 120*120 + 120*60", "Sq.M", 330.00, 200.00, 449.57),
    ("6.20", "بلاط بورسلين للأرضيات قياس 120*280 لمنطقة المصاعد واستقبال الدور الأول", "Sq.M", 420.00, 150.00, 308.60),
    ("7.10", "موكيت للأرضيات مربعات 50*50 سم شامل الغراء اللاصق والتثبيت والتركيب", "Sq.M", 250.00, 2000.00, 5026.00),
    ("8.10", "توريد وتركيب نعلات ألومنيوم أسود", "LM", 90.00, 500.00, 1104.00),
    ("9.10", "جبس بورد سماكة 150 مم مع عزل الصوف الصخري طبقتين للوجهين", "Sq.M", 180.00, 1500.00, 2933.40),
    ("9.30", "توريد وتركيب بلوك أسمنتي", "Sq.M", 100.00, 800.00, 1496.00),
    ("9.40", "لياسة جدران المبنى", "Sq.M", 45.00, 1200.00, 2193.00),
    ("10.10", "طبقة دهان أساس وطبقتين دهان معتمد للجدران", "Sq.M", 45.00, 3000.00, 5430.00),
]
BASE = round(sum(p * q for _, _, _, p, q, _ in ITEMS), 2)  # 1,213,000.00
VAT = round(BASE * 0.15, 2)  # 181,950.00
TOTAL = round(BASE + VAT, 2)  # 1,394,950.00

_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1a1a1a; }
h1 { font-size: 14pt; margin: 0 0 4pt 0; text-align: center; }
h2 { font-size: 10pt; margin: 10pt 0 2pt 0; }
table { width: 100%; border-collapse: collapse; margin-top: 6pt; }
th, td { border: 0.5pt solid #444; padding: 4pt 6pt; text-align: right; }
th { background-color: #1b6ea8; color: #ffffff; font-weight: bold; }
.kv td { border: none; padding: 1.5pt 4pt; }
.q { background-color: #eef4f8; }
.note { color: #555; font-size: 7pt; margin-top: 10pt; text-align: center; }
.sig td { border: none; padding-top: 18pt; text-align: center; }
"""

DISCLAIMER = (
    '<p class="note">مستند اختباري مولّد آليا لأغراض تطوير وكيل سلامة المطالبات — الأرقام والهويات تجريبية.<br/>'
    "Synthetic test document generated for the Claim Integrity Agent — demo figures and identities.</p>"
)


# ------------------------------------------------------------------ QR build
def tlv(tag: int, value: bytes | str) -> bytes:
    b = value.encode("utf-8") if isinstance(value, str) else value
    return bytes([tag, len(b)]) + b


def payload_valid() -> str:
    phase1 = (
        tlv(1, VENDOR["name_ar"])
        + tlv(2, VENDOR["vat"])
        + tlv(3, TS)
        + tlv(4, f"{TOTAL:.2f}")
        + tlv(5, f"{VAT:.2f}")
    )
    digest = hashlib.sha256(
        json.dumps([i[:2] for i in ITEMS], ensure_ascii=False).encode() + INVOICE_NO.encode()
    ).digest()
    signature = _KEY.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    public = _KEY.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(
        phase1 + tlv(6, base64.b64encode(digest).decode()) + tlv(7, signature) + tlv(8, public)
    ).decode()


# ------------------------------------------------------------------ renderers
def render(path: Path, html: str | list[str], qr_payload: str | None = None) -> None:
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


def invoice_html(price_910: float) -> str:
    rows = "".join(
        f'<tr><td><span dir="ltr">{c}</span></td><td>{d}</td><td>{u}</td><td>{q:,.2f}</td>'
        f"<td>{(price_910 if c == '9.10' else p):,.2f}</td>"
        f"<td>{(price_910 if c == '9.10' else p) * q:,.2f}</td></tr>"
        for c, d, u, p, q, _ in ITEMS
    )
    base = round(sum((price_910 if c == "9.10" else p) * q for c, _, _, p, q, _ in ITEMS), 2)
    vat = round(base * 0.15, 2)
    return f"""
<body dir="rtl">
<h1>فاتورة ضريبية — مستخلص أعمال — TAX INVOICE</h1>
<p style="text-align:center"><b>{VENDOR["name_ar"]}</b> — {VENDOR["name_en"]}<br/>
السجل التجاري: <span dir="ltr">{VENDOR["cr"]}</span> &nbsp;|&nbsp; الرقم الضريبي: <span dir="ltr">{VENDOR["vat"]}</span></p>
<table class="kv">
<tr><td><b>رقم الفاتورة:</b> <span dir="ltr">{INVOICE_NO}</span></td>
    <td><b>تاريخ الفاتورة:</b> <span dir="ltr">{DATE}</span></td></tr>
<tr><td><b>رقم العقد:</b> <span dir="ltr">{CONTRACT_NO}</span></td>
    <td><b>المشروع:</b> تأهيل المقر الرئيسي لشركة الصحة القابضة — الطابق الأول حتى التاسع</td></tr>
<tr><td colspan="2"><b>نوع المستخلص:</b> دوري — المستخلص رقم ٢ (عن الأعمال المنفذة والمقاسة حسب جدول الكميات)</td></tr>
</table>
<table>
<tr><th>البند</th><th>الوصف</th><th>الوحدة</th><th>الكمية المنفذة</th><th>سعر الوحدة (ر.س)</th><th>المجموع (ر.س)</th></tr>
{rows}
</table>
<table class="kv">
<tr><td><b>الإجمالي قبل الضريبة:</b> {base:,.2f} ر.س</td>
    <td><b>ضريبة القيمة المضافة (15٪):</b> {vat:,.2f} ر.س</td>
    <td><b>الإجمالي شامل الضريبة:</b> <b>{round(base + vat, 2):,.2f} ر.س</b></td></tr>
</table>
<p>الكميات أعلاه مقاسة هندسياً بالصافي وفق أسلوب القياس المتفق عليه في العقد (البند ٢.٤.١٥)،
وبأسعار الوحدات المعتمدة في جدول الكميات والأسعار (البند ٣.٥ من الشروط المالية).</p>
{DISCLAIMER}
</body>"""


def coc_html(coc_date: str, late: bool) -> str:
    delay_days = 20 if late else 0
    yes, no = "نعم ☑", "لا ☐"
    return f"""
<body dir="rtl">
<h1>محضر الإنجاز — شهادة إنجاز أعمال — Certificate of Completion</h1>
<table class="kv">
<tr><td><b>التاريخ:</b> <span dir="ltr">{coc_date}</span></td>
    <td><b>اسم المؤسسة / الشركة:</b> {VENDOR["name_ar"]}</td></tr>
<tr><td><b>رقم المطالبة المالية:</b> <span dir="ltr">{INVOICE_NO}</span></td>
    <td><b>رقم محضر الإنجاز:</b> <span dir="ltr">{COC_NO}</span></td></tr>
<tr><td><b>نوع المستخلص:</b> دوري — المستخلص رقم ٢</td>
    <td><b>قيمة العقد (شامل الضريبة):</b> {CONTRACT_TOTAL:,.2f}</td></tr>
</table>

<table>
<tr><th>رقم العقد</th><th>تاريخ محضر بدء المشروع (استلام الموقع)</th><th>تاريخ نهاية العقد (مدة خمسة أشهر)</th><th>قيمة العقد (شامل الضريبة)</th></tr>
<tr><td><span dir="ltr">{CONTRACT_NO}</span></td><td><span dir="ltr">{START_DATE}</span></td><td><span dir="ltr">{END_DATE}</span></td><td>{CONTRACT_TOTAL:,.2f}</td></tr>
<tr><th>ترتيب المستخلص الحالي</th><th>قيمة المطالبة الحالية</th><th>مبلغ الضريبة</th><th>إجمالي المطالبة</th></tr>
<tr><td>المستخلص الثاني</td><td>{BASE:,.2f}</td><td>{VAT:,.2f}</td><td>{TOTAL:,.2f}</td></tr>
</table>

<table>
<tr class="q"><th colspan="2">هل يوجد إيقاف و إعادة استئناف للمشروع؟</th><th colspan="2">هل هناك تأخير في تنفيذ الأعمال المطلوبة؟</th></tr>
<tr><td>نعم ☐</td><td>لا ☑</td><td>{yes if late else "نعم ☐"} &nbsp; (عدد الأيام: {delay_days})</td><td>{no if late else "لا ☑"}</td></tr>
<tr class="q"><th colspan="4">هل هناك ملاحظات على التنفيذ؟</th></tr>
<tr><td colspan="2">{"نعم ☑" if late else "نعم ☐"}</td><td colspan="2">{"لا ☐" if late else "لا ☑"}</td></tr>
</table>

<p>{
        "تمت معاينة الأعمال محل المستخلص الثاني واستلامها، مع إثبات تأخر إنجاز الأعمال عن نهاية مدة العقد "
        f"({END_DATE}) بواقع {delay_days} يوماً؛ ويُحال احتساب غرامة التأخير وفق البند ٣.٣ من الشروط المالية للعقد."
        if late
        else "تمت معاينة الأعمال محل المستخلص الثاني بمشاركة المقاول واستلامها مطابقة للمواصفات وجدول الكميات، ضمن مدة العقد ودون أي تأخير أو إيقاف."
    }</p>

<table class="sig">
<tr><td><b>ممثل شركة الصحة القابضة</b><br/>الاسم: م. خالد سعد العمري<br/>التاريخ: <span dir="ltr">{coc_date}</span></td>
    <td><b>ممثل المقاول</b><br/>الاسم: سهيل صلاح الدين كيالي<br/>التاريخ: <span dir="ltr">{coc_date}</span></td></tr>
</table>
{DISCLAIMER}
</body>"""


def work_commencement_html() -> str:
    return f"""
<body dir="rtl">
<h1>محضر بدء المشروع — استلام الموقع — Work Commencement Minutes</h1>
<table class="kv">
<tr><td><b>رقم المحضر:</b> <span dir="ltr">WC-{CONTRACT_NO}</span></td>
    <td><b>التاريخ:</b> <span dir="ltr">{START_DATE}</span></td></tr>
<tr><td><b>العقد:</b> <span dir="ltr">{CONTRACT_NO}</span> — تأهيل المقر الرئيسي لشركة الصحة القابضة</td>
    <td><b>المقاول:</b> {VENDOR["name_ar"]}</td></tr>
<tr><td colspan="2"><b>الموقع:</b> المقر الرئيسي — الطابق الأول حتى الطابق التاسع — الرياض</td></tr>
</table>
<p>بتاريخه تم تسليم الموقع للمقاول المذكور أعلاه ومباشرته أعمال التأهيل محل العقد،
ويُعد هذا التاريخ بداية احتساب مدة التنفيذ البالغة خمسة أشهر وفق البند (١.٥) من العقد،
فتكون نهاية مدة العقد بتاريخ <span dir="ltr">{END_DATE}</span>.</p>
<table class="sig">
<tr><td><b>ممثل شركة الصحة القابضة</b><br/>الاسم: م. خالد سعد العمري</td>
    <td><b>ممثل المقاول</b><br/>الاسم: سهيل صلاح الدين كيالي</td></tr>
</table>
{DISCLAIMER}
</body>"""


README = f"""# hhc-fitout — the complex-contract showcase

A claim package built around a REAL 76-page scanned works contract
({CONTRACT_NO} — تأهيل المقر الرئيسي لشركة الصحة القابضة, signed & stamped).
The synthetic documents bill REAL BoQ lines at the real unit prices, so every
gate runs against actual contract content. Generated by
backend/scripts/generate_hhc_scenario.py (which also copies the contract from
supporting_docs/contracts/hhc/).

## Why this scenario exists
"Even complex contracts can be handled": scanned Arabic pages, rotated BoQ
tables, penalty clauses buried on p.37 — the agent reads them (Azure CU
layout OCR + GPT structuring), cites them, and SHOWS the evidence in the
embedded PDF reader (OCR-polygon highlight — scanned pages have no text
layer).

## The contract's own facts (what extraction should find)
- Clause 1.4: total value 23,115,000.00 SAR incl. VAT (base {CONTRACT_BASE:,.0f})
- Clause 1.5: duration FIVE MONTHS from محضر بدء المشروع (site handover)
- Financial terms p.36: 25% advance; 4-milestone payment schedule; 5%
  retention per payment (out of scope for now); payment within 90 days of an
  approved مستخلص + signed completion certificate
- Penalties p.37 (الشروط المالية ٣.٣): delay penalty up to 10% of the BoQ
  line value; TOTAL penalties capped at 20% of the contract value; plus a
  compliance-penalty table (1,000 SAR/day items)
- BoQ appendix (جدول الكميات والأسعار): priced lines on ROTATED landscape
  pages — the ones this scenario's invoice bills

## Timeline
- Site handover (محضر بدء المشروع): {START_DATE}
- Contractual end (+5 months):      {END_DATE}
- Invoice (مستخلص ٢):               {DATE} — base {BASE:,.2f}, VAT {VAT:,.2f}, total {TOTAL:,.2f}
- COC on-time variant:              {COC_DATE_ONTIME} (4 days to spare)
- COC late variant:                 {COC_DATE_LATE} (20 days over)

## Form values for the guided review
- Project / contract no:  {CONTRACT_NO}
- Contract kind:          WORKS -> step 3 asks for the COC
- Contract value (base):  {CONTRACT_BASE:,.0f}  (or leave empty — uploading the
                          contract suggests the BoQ total; correct it to this)
- Contract end date:      {END_DATE}  (auto-suggested when the extractor derives
                          it from the 5-month duration + the commencement date
                          printed on the COC; type it if the field stays empty)
- Claim type:             Periodic — payment no. 2
- Prior payments:         1 payment, cumulative {CUMULATIVE_PRIOR:,.0f}
  (payment 1 = 25% milestone per the contract's payment schedule, p.36)

## Files
- {CONTRACT_DEST}  — the real contract (step 2 upload; first
  OCR pass over 76 pages takes a few minutes and is disk-cached — pre-warm
  before the demo by uploading it once)
- Invoice-AlBait-{INVOICE_NO}_real.pdf — 8 real BoQ lines, quantities within
  contract quantities, prices matching the BoQ
- Invoice-AlBait-{INVOICE_NO}_overpriced.pdf — line 9.10 billed at 200.00 vs
  the BoQ's 180.00 (the contract & BoQ gate fails with the exact line cited)
- {COC_NO}_ontime.pdf — accepted within the contract period
- {COC_NO}_late.pdf — dated {COC_DATE_LATE}, 20 days late, delay declared
- WorkCommencement-{CONTRACT_NO}.pdf — site-handover minutes (the date the
  5-month duration runs from; upload at step 5 as a vendor file)

## Scripted demo beats
1. Step 2: upload the REAL contract. The agent reads the scanned pages: BoQ
   lines from rotated tables, the contract value, and the PENALTY CLAUSES
   (p.37). Contract value suggestion fills the form.
2. Step 2 results: open a BoQ evidence chip -> the embedded reader lands on
   the rotated BoQ page and draws the OCR polygon highlight over the line.
3. Swap in the _overpriced invoice -> the gate fails citing line 9.10:
   billed 200.00 vs contracted 180.00. Swap back.
4. Step 3 (works): upload the on-time COC -> three-way match passes.
5. Step 4: the "Penalty terms read from the contract" card shows the 10% /
   20%-cap clauses — click one -> the reader opens the contract AT the
   penalty page and highlights the clause. No delay, no penalties -> passes.
6. Re-run step 3 with the LATE COC -> step 4 now infers 20 days of delay,
   and final.penalties_vs_contract FAILS: the contract demands a delay
   penalty (clause ٣.٣.١, up to 10% of the line value, cap 20%).
7. Add the penalty (e.g. غرامة تأخير المستخلص الثاني — {13500:,.2f} = 10% of
   line 10.10's billed value) -> re-run -> consistent; try an absurd amount
   above {0.2 * CONTRACT_BASE:,.0f} (20% of the contract value) -> the CAP
   check fails it.
8. Export the organized zip — invoice, contract, COC, traceably named.
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not CONTRACT_SRC.exists():
        raise SystemExit(f"real contract not found: {CONTRACT_SRC}")
    dest = OUT_DIR / CONTRACT_DEST
    if not dest.exists():
        shutil.copyfile(CONTRACT_SRC, dest)
        print(f"copied {CONTRACT_DEST} ({dest.stat().st_size / 1e6:.1f} MB)")
    qr = payload_valid()
    render(OUT_DIR / f"Invoice-AlBait-{INVOICE_NO}_real.pdf", invoice_html(price_910=180.00), qr)
    render(OUT_DIR / f"Invoice-AlBait-{INVOICE_NO}_overpriced.pdf", invoice_html(price_910=200.00), qr)
    render(OUT_DIR / f"{COC_NO}_ontime.pdf", coc_html(COC_DATE_ONTIME, late=False))
    render(OUT_DIR / f"{COC_NO}_late.pdf", coc_html(COC_DATE_LATE, late=True))
    render(OUT_DIR / f"WorkCommencement-{CONTRACT_NO}.pdf", work_commencement_html())
    (OUT_DIR / "README.md").write_text(README, encoding="utf-8")
    print(f"README + {len(list(OUT_DIR.glob('*.pdf')))} PDFs in {OUT_DIR.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
