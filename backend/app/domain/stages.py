"""The review gates, in order. Role-agnostic on purpose (see models.py).

Each gate maps to a rulepack file under services/rules/rulepacks/. Adding a
gate = adding an entry here + a rulepack; the pipeline discovers nothing else.
"""

from pydantic import BaseModel


class Stage(BaseModel):
    id: str
    order: int
    title_en: str
    title_ar: str
    desc_en: str
    desc_ar: str
    rulepack: str


STAGES: list[Stage] = [
    Stage(
        id="intake",
        order=1,
        title_en="Intake & authenticity",
        title_ar="الاستلام والتحقق من الصحة",
        desc_en="Claim metadata complete, tax invoice genuine (ZATCA QR), VAT math correct.",
        desc_ar="اكتمال بيانات المطالبة، صحة الفاتورة الضريبية (رمز الاستجابة)، صحة احتساب الضريبة.",
        rulepack="intake.yaml",
    ),
    Stage(
        id="boq_match",
        order=2,
        title_en="BoQ / contract match",
        title_ar="المطابقة مع جدول الكميات والعقد",
        desc_en="Invoice lines match the Bill of Quantities; amounts within contract and payment schedule.",
        desc_ar="مطابقة بنود الفاتورة مع جدول الكميات؛ المبالغ ضمن قيمة العقد وتسلسل الدفعات.",
        rulepack="boq_match.yaml",
    ),
    Stage(
        id="coc_consistency",
        order=3,
        title_en="COC consistency",
        title_ar="اتساق محضر الإنجاز",
        desc_en="Certificate of Completion consistent with penalties, amounts and project events.",
        desc_ar="اتساق محضر الإنجاز مع الغرامات والمبالغ ووقائع المشروع.",
        rulepack="coc_consistency.yaml",
    ),
    Stage(
        id="three_way_match",
        order=4,
        title_en="Three-way match",
        title_ar="المطابقة الثلاثية",
        desc_en="Contract/BoQ ↔ ERP product receipt ↔ invoice: billed only what was received, within contracted quantities.",
        desc_ar="مطابقة ثلاثية بين العقد وإيصال الاستلام والفاتورة: لا فوترة إلا لما تم استلامه وضمن الكميات التعاقدية.",
        rulepack="three_way.yaml",
    ),
    Stage(
        id="prefinance",
        order=5,
        title_en="Pre-finance package",
        title_ar="اكتمال الملف قبل المالية",
        desc_en="Full package check before referral to Finance: attachments, VAT treatment, document set.",
        desc_ar="فحص الملف كاملاً قبل الإحالة للإدارة المالية: المرفقات ومعالجة الضريبة واكتمال المستندات.",
        rulepack="prefinance.yaml",
    ),
]
