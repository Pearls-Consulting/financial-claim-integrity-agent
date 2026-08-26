"""Seed display-only claims into the store so the استلام المطالبات list looks
like a live D365 queue for the demo — varied vendors, statuses and verdicts.

These rows are set dressing: origin="erp" (read-only if clicked) with no
documents staged, in the ERP's VRM-002xxx range so they sit alongside the two
real seeded claims and never collide with wizard submissions (VRM-9xxxxx).

Run AFTER wiping backend/data/claims.db:
    .venv/Scripts/python scripts/seed_demo_claims.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.models import Claim, ClaimType, RunResult, Verdict  # noqa: E402
from app.services import store  # noqa: E402

# (id, vendor_ar, vendor_en, project_ar, project_en, po, contract_value,
#  base, type, payment_no, claim_date, step, verdict)
ROWS = [
    ("VRM-002395", "شركة المدار لتقنية المعلومات", "Almadar IT Co.",
     "تطوير بوابة الخدمات الرقمية", "Digital services portal development",
     "PO26-00188", 1_840_000.00, 460_000.00, ClaimType.periodic, 2, "2026-07-19", 6, Verdict.approve),
    ("VRM-002396", "مؤسسة الريادة للصيانة والتشغيل", "Al-Riyadah O&M Est.",
     "صيانة وتشغيل مباني الفروع", "Branch buildings O&M",
     "PO26-00121", 2_600_000.00, 216_666.67, ClaimType.periodic, 7, "2026-07-22", 6, Verdict.approve),
    ("VRM-002397", "شركة الأمان لأنظمة السلامة", "Alaman Safety Systems Co.",
     "توريد وتركيب أنظمة إنذار الحريق", "Fire alarm systems supply & installation",
     "PO26-00167", 935_000.00, 187_000.00, ClaimType.first, 1, "2026-07-28", 6, Verdict.reject),
    ("VRM-002398", "شركة النخبة للاستشارات الهندسية", "Elite Engineering Consultants",
     "الإشراف على ترميم المستودعات", "Warehouse renovation supervision",
     "PO26-00203", 720_000.00, 120_000.00, ClaimType.periodic, 3, "2026-08-02", 4, None),
    ("VRM-002399", "مصنع الجودة للأثاث المكتبي", "Aljawda Office Furniture Factory",
     "تأثيث مركز خدمة العملاء", "Customer service center furnishing",
     "PO26-00229", 1_150_000.00, 1_150_000.00, ClaimType.final, 4, "2026-08-09", 2, None),
    ("VRM-002403", "شركة الاتقان للنقل والخدمات اللوجستية", "Al-Itqan Logistics Co.",
     "خدمات النقل والتخزين السنوية", "Annual transport & warehousing services",
     "PO26-00241", 480_000.00, 40_000.00, ClaimType.periodic, 5, "2026-08-14", 0, None),
    ("VRM-002404", "مؤسسة البيان للدعاية والإعلان", "Albayan Advertising Est.",
     "حملة التوعية بمنتجات التمويل", "Financing products awareness campaign",
     "PO26-00256", 300_000.00, 75_000.00, ClaimType.first, 1, "2026-08-18", 0, None),
    ("VRM-002405", "شركة الرواد للمقاولات العامة", "Alrowad General Contracting Co.",
     "إنشاء فرع حي النسيم بالرياض", "Al-Naseem branch construction, Riyadh",
     "PO26-00097", 6_750_000.00, 843_750.00, ClaimType.periodic, 4, "2026-08-19", 6, Verdict.needs_human),
    ("VRM-002406", "شركة التقنية المتقدمة للحلول الرقمية", "Advanced Digital Solutions Co.",
     "ترخيص وصيانة نظام إدارة الوثائق", "Document management system licence & support",
     "PO26-00173", 980_000.00, 245_000.00, ClaimType.periodic, 2, "2026-08-19", 6, Verdict.approve),
    ("VRM-002407", "مؤسسة الواحة للتوريدات المكتبية", "Alwaha Office Supplies Est.",
     "توريد القرطاسية والمستلزمات المكتبية", "Stationery & office consumables supply",
     "PO26-00262", 240_000.00, 60_000.00, ClaimType.periodic, 1, "2026-08-20", 6, Verdict.reject),
    ("VRM-002408", "شركة الخليج لخدمات النظافة", "Gulf Cleaning Services Co.",
     "خدمات النظافة للمقر الرئيسي والفروع", "Cleaning services — HQ and branches",
     "PO26-00114", 1_920_000.00, 160_000.00, ClaimType.periodic, 8, "2026-08-20", 6, Verdict.approve),
    ("VRM-002409", "شركة المعمار للتصميم الهندسي", "Almemar Engineering Design Co.",
     "إعداد التصاميم التنفيذية لمركز البيانات", "Data centre detailed design",
     "PO26-00219", 1_350_000.00, 405_000.00, ClaimType.periodic, 2, "2026-08-21", 5, None),
    ("VRM-002410", "مؤسسة الأفق لتقنية المعلومات", "Alufuq IT Est.",
     "توريد وتركيب أجهزة الشبكات للفروع", "Branch network equipment supply & installation",
     "PO26-00231", 2_100_000.00, 630_000.00, ClaimType.first, 1, "2026-08-21", 3, None),
    ("VRM-002411", "شركة الحماية للحراسات الأمنية", "Alhimaya Security Guarding Co.",
     "خدمات الحراسة الأمنية للفروع", "Security guarding services for branches",
     "PO26-00108", 3_360_000.00, 280_000.00, ClaimType.periodic, 9, "2026-08-22", 1, None),
    ("VRM-002412", "شركة الديار للتكييف والتبريد", "Aldiyar HVAC Co.",
     "استبدال أنظمة التكييف بالمقر الرئيسي", "HQ air-conditioning replacement",
     "PO26-00185", 1_480_000.00, 1_480_000.00, ClaimType.final, 3, "2026-08-22", 6, Verdict.needs_human),
    ("VRM-002413", "شركة المستقبل للتدريب والتطوير", "Almustaqbal Training & Development Co.",
     "برنامج تطوير القيادات الإدارية", "Leadership development programme",
     "PO26-00247", 560_000.00, 140_000.00, ClaimType.periodic, 2, "2026-08-23", 0, None),
    ("VRM-002414", "مؤسسة الصفوة للطباعة والنشر", "Alsafwa Printing & Publishing Est.",
     "طباعة التقرير السنوي والمطبوعات التعريفية", "Annual report & corporate print",
     "PO26-00271", 180_000.00, 180_000.00, ClaimType.final, 1, "2026-08-23", 0, None),
]


def main() -> None:
    for (cid, v_ar, v_en, p_ar, p_en, po, contract, base, ctype, pay_no, date, step, verdict) in ROWS:
        vat = round(base * 0.15, 2)
        claim = Claim(
            id=cid,
            po_no=po,
            project_no=f"PRJ{cid[-4:]}0",
            project_name_ar=p_ar,
            project_name_en=p_en,
            vendor_account=f"Vend00{cid[-3:]}",
            vendor_name_ar=v_ar,
            vendor_name_en=v_en,
            contract_value=contract,
            claim_amount_base=base,
            vat_amount=vat,
            claim_amount_total=round(base + vat, 2),
            invoice_no=f"INV/2026/{cid[-4:]}",
            payment_no=pay_no,
            claim_type=ctype,
            claim_date=date,
            cumulative_prior=round(base * max(pay_no - 1, 0), 2),
            prior_payment_count=pay_no - 1,
            status_ar="تم الانتهاء" if step >= 6 else ("تحت الاجراء" if step else "تم الانتهاء"),
            origin="erp",  # read-only if someone clicks it by accident
        )
        store.save_submission(claim)
        if step:
            store.set_progress(cid, step)
        if verdict is not None:
            store.save_run(RunResult(claim_id=cid, verdict=verdict))
        print(f"seeded {cid}  {v_en:38s} step={step} verdict={verdict.value if verdict else '-'}")
    print("done — the claims list now shows a live-looking D365 queue.")


if __name__ == "__main__":
    main()
