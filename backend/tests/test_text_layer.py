"""The PDF text layer as a digit witness for the vision read
(extraction/text_layer.py): exact digits from a born-digital PDF settle the
model's transposed / miscounted digit strings, silently, and never touch a
value the text confirms or cannot confirm."""

from pathlib import Path

import pytest

from app.services.extraction import text_layer as tl

FIXTURES = Path(__file__).resolve().parents[2] / "supporting_docs"
ALWAHA = FIXTURES / "test_scenarios" / "demo-vendor" / "Invoice-Alwaha-INV-2026-0342_real.pdf"

# What PDFium hands back for a synthetic Arabic invoice: reversed words,
# mojibake letters ("اǄ"), exact digits, comma thousands, Arabic-Indic digits
# in the body text.
GARBLED = (
    "TAX INVOICE — ةيبيرض ةروتاف\n.Al-Waha Office Supplies Co — اǄبتاكم Ǆتازيهجت اǄةحاو ةكرش\n"
    "310987654300003 : اǄيبيرض اǄمقر\n 1010777045 : اǄيراجت اǄلجس\n"
    "INV-2026-0342 : اǄةروتاف مقر 15-06-2026 : اǄةروتاف خيرات\nPO26-00214 : اǄءارش رمأ\n"
    "OF-101 جاردأ عم يبشخ يرادإ بتكم 40 1,850.00 74,000.00\n"
    "OF-401 اصخش ١٢ تاعامتجا واطǄة 2 6,000.00 12,000.00\n"
    "س.ر 200,000.00 : اǄةبيرض لبق اƾامجǄي س.ر 30,000.00) : ٪15 ( اǄةفاضم اǄةميق ةبيرض "
    "س.ر 230,000.00 : اǄةبيرض لماش اƾامجǄي\n"
)


def _invoice(**over):
    inv = {
        "invoice_no": "INV-2026-0342",
        "seller_vat_number": "310987654300003",
        "total_with_vat": 230000.0,
        "vat_amount": 30000.0,
        "lines": [
            {"item_code": "OF-101", "description_ar": "x", "unit_price": 1850.0, "quantity": 40.0, "amount": 74000.0},
            {"item_code": "OF-401", "description_ar": "y", "unit_price": 6000.0, "quantity": 2.0, "amount": 12000.0},
            # a third line so the lines sum to the net (200,000)
            {"item_code": "OF-999", "description_ar": "z", "unit_price": 1.0, "quantity": 114000.0, "amount": 114000.0},
        ],
    }
    inv.update(over)
    return inv


def test_normalize_keeps_digit_order_and_drops_only_thousands_separators():
    assert tl.normalize("١٬٨٥٠٫٥٠ س.ر") == "1850.50 س.ر"
    assert tl.normalize("Sq.M 200.00 330.00 66,000.00") == "sq.m 200.00 330.00 66000.00"  # cells never merge
    assert tl.normalize("2,50%") == "2,50%"  # a decimal comma is not a thousands separator


def test_usable_is_about_digit_runs_not_readable_prose():
    assert tl.usable(GARBLED)  # mojibake Arabic, exact digits: usable
    assert not tl.usable("فاتورة ضريبية رقم ١٢")  # too few digit runs — a scan's stray OCR
    assert not tl.usable("")


def test_contains_identifier_and_amount_in_normalised_garbled_text():
    norm = tl.normalize(GARBLED)
    assert tl.contains_identifier(norm, "INV-2026-0342")
    assert tl.contains_identifier(norm, "310987654300003")
    assert tl.contains_identifier(norm, "INV 2026 0342")  # digit runs in sequence, punctuation aside
    assert not tl.contains_identifier(norm, "310987654300004")
    assert tl.contains_amount(norm, 230000.0)
    assert tl.contains_amount(norm, 1850.0)
    assert not tl.contains_amount(norm, 30000.5)


def test_near_is_one_vision_slip():
    assert tl.near("301987654300003", "310987654300003")  # adjacent transposition
    assert tl.near("310987654380003", "310987654300003")  # one digit misread
    assert tl.near("31098765430003", "310987654300003")  # one zero lost
    assert tl.near("1000000", "100000")  # zero-run count
    assert not tl.near("310987654300003", "310987654300003")  # identical = a match, not "near"
    assert not tl.near("310987654300003", "310987654399003")  # two independent errors
    assert not tl.near("123456", "654321")


def test_reconcile_fixes_a_transposed_vat_number_and_a_zero_miscount():
    docs = {"invoice": _invoice(seller_vat_number="301987654300003")}
    fixed = tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["seller_vat_number"] == "310987654300003"
    assert any("seller_vat_number" in f for f in fixed)
    docs = {"invoice": _invoice(seller_vat_number="3109876543000003")}  # one zero too many
    tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["seller_vat_number"] == "310987654300003"
    docs = {"invoice": _invoice(invoice_no="INV-2026-0324")}  # transposed invoice number keeps its casing
    tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["invoice_no"] == "INV-2026-0342"


def test_reconcile_keeps_a_value_the_text_confirms_or_cannot_place():
    docs = {"invoice": _invoice()}
    assert tl.reconcile(docs, GARBLED) == []
    assert docs["invoice"]["seller_vat_number"] == "310987654300003"
    # a wholly different number (not one slip away) is left to the reader
    docs = {"invoice": _invoice(seller_vat_number="300000000000001")}
    assert tl.reconcile(docs, GARBLED) == []
    assert docs["invoice"]["seller_vat_number"] == "300000000000001"
    # an ambiguous near-variant (two candidates) is left alone
    text = GARBLED + "\n310987654300004 : x\n"  # now two tokens one slip from the misread
    docs = {"invoice": _invoice(seller_vat_number="310987654300005")}
    assert tl.reconcile(docs, text) == []


def test_reconcile_amounts_only_when_the_arithmetic_says_so():
    # header total miscounted (2,300,000 for 230,000): lines + VAT no longer add up; the text's 230000 does
    docs = {"invoice": _invoice(total_with_vat=2300000.0)}
    fixed = tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["total_with_vat"] == 230000.0 and fixed
    # a line's unit price transposed (1,580 for 1,850): qty × price no longer equals the amount
    docs = {"invoice": _invoice()}
    docs["invoice"]["lines"][0]["unit_price"] = 1580.0
    tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["lines"][0]["unit_price"] == 1850.0
    # a consistent invoice is never "corrected"
    docs = {"invoice": _invoice()}
    assert tl.reconcile(docs, GARBLED) == []
    # an inconsistent one whose near-variant does not repair it stays as read
    docs = {"invoice": _invoice(total_with_vat=2300000.0, vat_amount=300000.0)}
    tl.reconcile(docs, GARBLED)
    assert docs["invoice"]["total_with_vat"] == 2300000.0


def test_reconcile_is_a_no_op_without_a_text_layer():
    docs = {"invoice": _invoice(seller_vat_number="301987654300003")}
    assert tl.reconcile(docs, "") == []
    assert tl.reconcile(docs, "scan noise ١٢") == []
    assert docs["invoice"]["seller_vat_number"] == "301987654300003"


def test_reconcile_adds_nothing_the_reviewer_would_see():
    """Corrections live in the returned log (server log only) — the document
    gains no flag, no note, nothing but the corrected value."""
    docs = {"invoice": _invoice(seller_vat_number="301987654300003")}
    before = set(docs["invoice"])
    tl.reconcile(docs, GARBLED)
    assert set(docs["invoice"]) == before


@pytest.mark.skipif(not ALWAHA.exists(), reason="fixture invoice absent")
def test_real_fixture_text_layer_confirms_every_header_value():
    pytest.importorskip("pypdfium2")
    text = tl.pages_text(ALWAHA, 0, 1)
    assert tl.usable(text)
    norm = tl.normalize(text)
    assert tl.contains_identifier(norm, "INV-2026-0342")
    assert tl.contains_identifier(norm, "310987654300003")
    assert tl.contains_amount(norm, 230000.0) and tl.contains_amount(norm, 30000.0)
    assert not tl.pages_text(ALWAHA.with_suffix(".png"), 0, 1)  # not a PDF → no witness


def test_verify_read_gets_the_hint_only_when_usable(monkeypatch):
    from app.services.extraction import gpt_vision as gv

    seen = {}

    def fake_call_json(client, *, system, content, **kw):
        seen["content"] = content
        return {"invoice": {"invoice_no": "INV-1"}}

    monkeypatch.setattr(gv, "call_json", fake_call_json)
    monkeypatch.setattr(gv, "unit_blocks", lambda unit: [{"type": "input_image", "image_url": "data:..."}])
    unit = gv.Unit(path=Path("x.pdf"), doc_type="invoice")
    gv.read_key_fields(unit, client=object(), text=GARBLED)
    assert any("TEXT LAYER" in b.get("text", "") for b in seen["content"])
    gv.read_key_fields(unit, client=object(), text="noise")
    assert not any("TEXT LAYER" in b.get("text", "") for b in seen["content"])


COC_TEXT = (
    "Certificate of Completion — اƾزاجن رضحم\n18-06-2026 : اǄخيرات\nINV-2026-0342 : اǄامǄةي اǄاطمǄةب مقر\n"
    "COC-000000355 : اƾزاجن رضحم مقر\nRFQ26/042 2026-05-01 2026-11-30 713,000.00\n"
    "اƾ	وǄى اǄةعفد 200,000.00 30,000.00 230,000.00\n26400871 2026-05-10\n"
)


def _coc(**over):
    coc = {"coc_no": "COC-000000355", "coc_date": "2026-06-18", "claim_amount": 230000.0, "claim_net": 200000.0, "vat_amount": 30000.0,
           "invoice_ref": "INV-2026-0342", "contract_no": "RFQ26/042", "award_letter_no": "26400871", "contract_value_with_vat": 713000.0}
    coc.update(over)
    return coc


def test_coc_identifiers_and_amounts_get_the_invoice_treatment():
    # transposed COC number, transposed award letter number, a zero too many on the total
    docs = {"coc": _coc(coc_no="COC-000000535", award_letter_no="26408071", claim_amount=2300000.0)}
    fixed = tl.reconcile(docs, COC_TEXT)
    assert docs["coc"]["coc_no"] == "COC-000000355"
    assert docs["coc"]["award_letter_no"] == "26400871"
    assert docs["coc"]["claim_amount"] == 230000.0
    assert len(fixed) == 3
    # a COC that adds up is never touched; one that prints only the total has no gate
    docs = {"coc": _coc()}
    assert tl.reconcile(docs, COC_TEXT) == []
    docs = {"coc": _coc(claim_net=0.0, vat_amount=0.0, claim_amount=2300000.0)}
    assert tl.reconcile(docs, COC_TEXT) == [] and docs["coc"]["claim_amount"] == 2300000.0


def test_verify_read_parses_the_coc_net_and_vat(monkeypatch):
    from app.services.extraction import gpt_vision as gv

    monkeypatch.setattr(gv, "call_json", lambda *a, **k: {"coc": {"coc_no": "COC-1", "claim_amount": "363,816.30", "claim_net": 316362.0,
                                                                   "vat_amount": 47454.3, "contract_end_date": "10/04/2026", "invoice_ref": "INV/2026/00070"}})
    monkeypatch.setattr(gv, "unit_blocks", lambda unit: [])
    out = gv.read_key_fields(gv.Unit(path=Path("x.pdf"), doc_type="coc"), client=object())
    assert out["coc"]["claim_net"] == 316362.0 and out["coc"]["vat_amount"] == 47454.3
    assert out["coc"]["contract_end_date"] == "2026-04-10" and out["coc"]["invoice_ref"] == "INV/2026/00070"


CONTRACT_TEXT = (
    "قيمة العقد\n"
    "أولاً: القيمة الإجمالية للعقد هي مبلغ قدره ( 14,950,000 ) أربعة عشر مليون وتسعمائة وخمسون ألف ريال سعودي فقط لا غير\n"
    "وتشمل كذلك كافة الرسوم والضرائب بما في ذلك ضريبة القيمة المضافة.\n"
    "رقم العقد: RFQ25/053 تاريخ 2025-09-10\n"
)


def test_reconcile_contract_value_recovers_a_dropped_leading_digit():
    # VRM-900001: page 6 prints (14,950,000) once, VAT-inclusive; the read kept
    # it in value_base but emitted value_with_vat as 4,950,000 — dropped ١.
    docs = {"contract": {"contract_no": "RFQ25/053", "value_base": 14950000.0, "value_with_vat": 4950000.0}}
    fixed = tl.reconcile(docs, CONTRACT_TEXT)
    assert any("value_with_vat" in f for f in fixed)
    assert docs["contract"]["value_with_vat"] == 14950000.0
    assert docs["contract"]["value_base"] == 13000000.0  # one printed figure, incl-VAT; base derived


def test_reconcile_contract_leaves_consistent_or_unwitnessed_values_alone():
    # base 13,000,000 is not printed, but nothing near it is either: untouched.
    docs = {"contract": {"contract_no": "RFQ25/053", "value_base": 13000000.0, "value_with_vat": 14950000.0}}
    assert tl.reconcile(docs, CONTRACT_TEXT) == []
    # a lone value with no near-variant on the page: untouched
    docs = {"contract": {"contract_no": "RFQ25/053", "value_with_vat": 8000000.0}}
    assert tl.reconcile(docs, CONTRACT_TEXT) == []
