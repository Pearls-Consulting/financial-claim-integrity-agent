"""استقطاع lines: an invoice line billing negative work (advance-payment
recovery, retention, credit) is a payment adjustment, not a BoQ item.
Fielded on VRM-900005: invoice row "5" (استقطاع 10% من الدفعة المقدمة,
qty -1 @ 88,643.40) collided with BoQ item 5 (دليل المنتجات المبتكرة @
221,960.00) and was reported as a unit-price mismatch."""

from app.domain.models import ClaimDocuments, is_deduction_line
from app.services.datasource import get_source
from app.services.extraction import reconcile as rc
from app.services.rules.engine import CHECKS


def _docs() -> ClaimDocuments:
    return ClaimDocuments.model_validate({
        "invoice": {"invoice_no": "2326000000070", "lines": [
            {"item_code": "1", "description_ar": "تقرير الوضع الراهن لمستفيدي التدريب المتخصص 2023", "unit_price": 443920.0, "quantity": 1, "amount": 510508.0},
            {"item_code": "5", "description_ar": "استقطاع 10% من الدفعة المقدمة", "unit_price": 88643.4, "quantity": -1, "amount": -101939.91},
        ]},
        "boq": [
            {"item_code": "1", "description_ar": "تقرير الوضع الراهن لمستفيدي التدريب المتخصص لعام 2023", "unit_price": 443920.0, "quantity": 1},
            {"item_code": "5", "description_ar": "دليل المنتجات المبتكرة - توفير دليل للمنتجات المبتكرة وعددها 100 منتج", "unit_price": 221960.0, "quantity": 1},
        ],
    })


def test_negative_quantity_or_amount_is_a_deduction():
    docs = _docs()
    assert not is_deduction_line(docs.invoice.lines[0])
    assert is_deduction_line(docs.invoice.lines[1])


def test_deduction_line_is_never_remapped_and_never_needs_the_model():
    docs = _docs()
    assert rc.align_codes(docs) == []  # the coincidental code 5 is left alone
    assert docs.invoice.lines[1].item_code == "5"
    assert rc.needs(docs, {}) == (False, False)  # and triggers no reconcile call


def test_boq_lines_match_reports_the_deduction_instead_of_a_false_mismatch():
    claim = get_source().get_claim("VRM-002401").model_copy(deep=True)
    docs = _docs()
    claim.documents.invoice = docs.invoice
    claim.documents.boq = docs.boq
    out = CHECKS["boq_lines_match"](claim, {})
    assert out.ok is True  # no unit-price finding against BoQ item 5
    assert out.evidence["deductions"] == [
        {"item": "5", "description": "استقطاع 10% من الدفعة المقدمة", "amount": -101939.91}
    ]
    assert "deduction" in out.detail_en and "استقطاع" in out.detail_ar
