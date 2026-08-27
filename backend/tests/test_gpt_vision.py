"""The `gpt` extractor's deterministic plumbing — no network: page rebasing,
chunk/file merging, validation, the reconcile patch, and the bounded locate
scan. The model calls themselves are exercised by scripts, not tests."""

from pathlib import Path

from app.domain.models import ClaimDocuments
from app.services.extraction import gpt_vision as gv
from app.services.extraction import reconcile as rc
from app.services.extraction.locate import scan_order

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "supporting_docs/test_scenarios/demo-vendor/Contract-BoQ-Alwaha-RFQ26-042_real.pdf"


def _read(**docs):
    return gv.validate_read({"anchors": {}, **docs})


def test_validate_read_rebases_pages_onto_the_file():
    out = gv.validate_read(
        {
            "contract": {"contract_no": "C-1", "page": 2, "penalty_terms": [{"rate_percent": 10.0, "page": 17}]},
            "boq": [{"item_code": "1", "description_ar": "x", "unit_price": 1.0, "quantity": 2.0, "page": None}],
            "anchors": {"commencement_date": "2026-01-05", "signing_date": None},
        },
        page_offset=20,
    )
    assert out["docs"]["contract"]["page"] == 22
    assert out["docs"]["contract"]["penalty_terms"][0]["page"] == 37
    assert out["docs"]["boq"][0]["page"] == 0  # unknown stays unknown, never offset
    assert out["anchors"] == {"commencement_date": "2026-01-05", "site_handover_date": "", "signing_date": ""}


def test_validate_read_drops_boq_header_subtotal_and_recap_rows():
    out = gv.validate_read({
        "boq": [
            {"item_code": "1", "description_ar": "أعمال الإزالة", "unit_price": 0, "quantity": 0},  # section header
            {"item_code": "1.10", "description_ar": "إزالة", "unit": "LS", "unit_price": 15037.95, "quantity": 9},
            {"item_code": "", "description_ar": "", "unit_price": 12339989.2, "quantity": 0},  # subtotal, no item
            {"item_code": "11.10", "description_ar": "بلاط", "unit_price": 0.0, "quantity": 0.0, "page": 75},  # recap
            {"item_code": "2.15", "description_ar": "حوائط", "unit_price": 0, "quantity": 3},  # qty only: kept
        ]
    })
    assert [(r["item_code"]) for r in out["docs"]["boq"]] == ["1.10", "2.15"]


def test_penalty_per_follows_the_clause_wording_not_the_reader():
    out = gv.validate_read({"contract": {"contract_no": "C", "penalty_terms": [
        {"kind": "delay", "rate_percent": 10.0, "per": "day", "text_ar": "تفرض عليه غرامة تأخير لا تتجاوز (10%) من قيمة البند حسب جدول الكميات"},
        {"kind": "delay", "rate_percent": 2.5, "per": "", "text_ar": "عدم الالتزام بجدول تنفيذ الأعمال — (2,50%) من قيمة الأعمال المتأخرة عن كل 7 أيام"},
        {"kind": "other", "rate_percent": 0.0, "per": "", "text_ar": "عدم تقديم جدول زمني — 1000 ريال / يوم"},
        {"kind": "delay", "rate_percent": 0.5, "per": "week", "text_ar": "0.5% per week of delay"},
    ]}})
    assert [t["per"] for t in out["docs"]["contract"]["penalty_terms"]] == ["", "week", "day", "week"]  # "عن كل 7 أيام" = weekly
    assert gv.clause_period("غرامة 1000 ريال عن كل 30 يوم تأخير") == ""  # a 30-day period is no unit the checks compute with
    assert gv.clause_period("عن كل يوم تأخير") == "day" and gv.clause_period("لكل أسبوع") == "week"


def test_validate_read_drops_erp_owned_keys():
    out = gv.validate_read({"penalties": [{"reason_ar": "x", "amount": 1}], "attachments": ["a"], "invoice": None})
    assert out["docs"]["penalties"] == [] and out["docs"]["attachments"] == []


def test_merge_reads_first_header_wins_gaps_fill_lists_concatenate():
    a = _read(contract={"contract_no": "C-1", "start_date": "", "value_base": 0.0, "page": 2, "penalty_terms": []},
              boq=[{"item_code": "1", "description_ar": "a", "unit_price": 1.0, "quantity": 1.0, "page": 5}])
    b = _read(contract={"contract_no": "OTHER", "start_date": "2026-01-01", "value_base": 100.0, "page": 30,
                        "penalty_terms": [{"rate_percent": 10.0, "page": 37}]},
              boq=[{"item_code": "1", "description_ar": "a", "unit_price": 1.0, "quantity": 1.0, "page": 10},  # boundary repeat
                   {"item_code": "2", "description_ar": "b", "unit_price": 2.0, "quantity": 1.0, "page": 11}])
    m = gv.merge_reads([a, b])
    c = m["docs"]["contract"]
    assert c["contract_no"] == "C-1" and c["page"] == 2  # first read's header wins
    assert c["start_date"] == "2026-01-01" and c["value_base"] == 100.0  # gaps filled
    assert [t["page"] for t in c["penalty_terms"]] == [37]
    assert [l["item_code"] for l in m["docs"]["boq"]] == ["1", "2"]  # repeat dropped, page ignored
    assert ClaimDocuments.model_validate(m["docs"]).contract.contract_no == "C-1"


def test_merge_reads_null_document_does_not_erase_a_read_one():
    a = _read(invoice={"invoice_no": "INV-1", "page": 1, "lines": []})
    b = _read(invoice=None)
    assert gv.merge_reads([b, a])["docs"]["invoice"]["invoice_no"] == "INV-1"
    assert gv.merge_reads([a, b])["docs"]["invoice"]["invoice_no"] == "INV-1"


def test_build_units_chunks_by_pages_and_caps():
    whole = gv.build_units(CONTRACT, "contract_boq", chunk_pages=10)
    assert len(whole) == 1 and whole[0].page_count == 2 and whole[0].chunk_total == 1
    chunks = gv.build_units(CONTRACT, "contract_boq", chunk_pages=1)
    assert [(u.page_offset, u.page_count, u.chunk_index, u.chunk_total) for u in chunks] == [(0, 1, 0, 2), (1, 1, 1, 2)]
    first_only = gv.build_units(CONTRACT, "attachment", chunk_pages=3, max_pages=1)
    assert len(first_only) == 1 and first_only[0].page_count == 1 and gv.pdf_page_count(CONTRACT) == 2


def test_unit_blocks_are_page_images_in_order():
    """Pages travel as rendered images (never a PDF input_file — its garbled
    Arabic text layer misleads the model), one block per page, high detail."""
    blocks = gv.unit_blocks(gv.build_units(CONTRACT, "contract_boq", chunk_pages=10)[0])
    assert len(blocks) == 2
    assert all(b["type"] == "input_image" and b["detail"] == "high" for b in blocks)
    assert all(b["image_url"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,")) for b in blocks)
    pages = gv.render_pages(CONTRACT, 0, 2, dpi=72)
    assert [m for _, m in pages] == ["image/png", "image/png"] and all(d[:8] == b"\x89PNG\r\n\x1a\n" for d, _ in pages)


def test_reconcile_patch_only_remaps_onto_real_boq_codes_and_duration_end_dates():
    docs = ClaimDocuments.model_validate(
        {
            "invoice": {"invoice_no": "I", "lines": [
                {"item_code": "A", "description_ar": "x", "unit_price": 1, "quantity": 1, "amount": 1},
                {"item_code": "B", "description_ar": "y", "unit_price": 1, "quantity": 1, "amount": 1},
            ]},
            "boq": [{"item_code": "1", "description_ar": "x", "unit_price": 1, "quantity": 1}],
            "contract": {"contract_no": "C", "end_date": "خمسة أشهر من تاريخ محضر بدء المشروع"},
        }
    )
    need_codes, need_date = rc.needs(docs, {"commencement_date": "2026-01-05"})
    assert need_codes and need_date
    out = rc.apply_patch(docs, {"invoice_item_codes": {"A": "1", "B": "99"}, "contract_end_date": "2026-06-05"})
    assert [l.item_code for l in out.invoice.lines] == ["1", "B"]  # "99" is not a BoQ code
    assert out.contract.end_date == "2026-06-05"
    # an ISO end date already read from the page is never overwritten
    out.contract.end_date = "2026-07-01"
    assert rc.apply_patch(out, {"contract_end_date": "2026-06-05"}).contract.end_date == "2026-07-01"
    assert rc.needs(out, {}) == (True, False)  # "B" is still unaligned; no anchors -> no date work


def test_vote_majority_beats_a_single_digit_slip_and_a_missed_document():
    good = _read(invoice={"invoice_no": "INV-1", "seller_vat_number": "310123456700003", "total_with_vat": 100.0, "page": 1, "lines": []})
    slip = _read(invoice={"invoice_no": "INV-1", "seller_vat_number": "301123456700003", "total_with_vat": 100.0, "page": 1, "lines": []})
    assert gv.vote([good, slip])["docs"]["invoice"]["seller_vat_number"] == "310123456700003"  # 2 reads: field-wise, first wins
    assert gv.vote([slip, good, good])["docs"]["invoice"]["seller_vat_number"] == "310123456700003"  # 3 reads: majority
    missed = _read(invoice=None)
    assert gv.vote([missed, good])["docs"]["invoice"]["invoice_no"] == "INV-1"  # null never outvotes a read
    assert gv.vote([good, good])["docs"]["invoice"]["page"] == 1  # pages survive the vote
    # a field-wise vote when every read differs somewhere: each field settles on its own majority
    a = _read(coc={"coc_no": "C-1", "claim_amount": 5.0, "delay_days": 0})
    b = _read(coc={"coc_no": "C-1", "claim_amount": 50.0, "delay_days": 0})
    c = _read(coc={"coc_no": "C-7", "claim_amount": 50.0, "delay_days": 0})
    v = gv.vote([a, b, c])["docs"]["coc"]
    assert (v["coc_no"], v["claim_amount"]) == ("C-1", 50.0)


def test_reads_agree_when_only_wording_or_date_format_differs():
    """Two passes that print the same values differently must NOT trigger a
    tie-break read: digit-free text and page numbers are not compared, digit
    strings compare as digit bags (date order / separators / Arabic digits)."""
    a = _read(coc={"coc_no": "COC-HHC-00518", "coc_date": "2026-07-12", "claim_amount": 1394950.0, "page": 1},
              contract={"contract_no": "HHC00050", "end_date": "خمسة أشهر من تاريخ محضر بدء المشروع", "value_base": 20100000, "penalty_terms": [
                  {"rate_percent": 10.0, "text_ar": "إذا تأخر المقاول ...", "ref": "٣.٣.١", "page": 37}]})
    b = _read(coc={"coc_no": "COC-HHC-00518", "coc_date": "12-07-2026", "claim_amount": 1394950.00, "page": 1},
              contract={"contract_no": "HHC00050", "end_date": "خمسة اشهر ابتداءً من تاريخ محضر بدء المشروع", "value_base": 20100000.0, "penalty_terms": [
                  {"rate_percent": 10.0, "text_ar": "إذا تأخر المقاول في تنفيذ", "ref": "1.3.3", "page": 37}]})
    assert gv._same(a, b)
    c = _read(coc={"coc_no": "COC-HHC-00518", "coc_date": "2026-07-12", "claim_amount": 1349950.0, "page": 1})
    assert not gv._same(a, c)  # a transposed amount is a real disagreement
    # anchors are merged, never voted: a pass that saw the commencement date fills in for one that did not
    x = gv.validate_read({"coc": {"coc_no": "C"}, "anchors": {"site_handover_date": "2026-05-10"}})
    y = gv.validate_read({"coc": {"coc_no": "C"}, "anchors": {"commencement_date": "2026-05-10", "site_handover_date": "2026-05-10"}})
    assert gv._same(x["docs"], y["docs"])
    assert gv.vote([x, y])["anchors"] == {"site_handover_date": "2026-05-10", "commencement_date": "2026-05-10"}


def test_key_fields_override_header_values_but_never_invent_a_document():
    full = _read(invoice={"invoice_no": "INV-2026-0518", "seller_vat_number": "301123456700003", "total_with_vat": 1394950.0,
                          "page": 1, "lines": [{"item_code": "6.10", "description_ar": "x", "unit_price": 330, "quantity": 200, "amount": 66000}]},
                 coc=None)
    key = {"invoice": {"seller_vat_number": "310123456700003", "total_with_vat": 1394950.0}, "coc": {"coc_no": "COC-1"}}
    out = gv.apply_key_fields(full, key)
    inv = out["docs"]["invoice"]
    assert inv["seller_vat_number"] == "310123456700003"  # the focused read wins
    assert inv["invoice_no"] == "INV-2026-0518" and inv["page"] == 1 and len(inv["lines"]) == 1  # everything else untouched
    assert out["docs"]["coc"] is None  # a document the full read found absent stays absent
    # same digits in another format is agreement: the full read's ISO date stays
    full = _read(coc={"coc_no": "COC-1", "coc_date": "2026-07-12", "claim_amount": 1394950.0})
    out = gv.apply_key_fields(full, {"coc": {"coc_date": "12-07-2026", "claim_amount": 1394950}})
    assert out["docs"]["coc"]["coc_date"] == "2026-07-12" and out["docs"]["coc"]["claim_amount"] == 1394950.0


def test_key_field_disagreement_alone_needs_no_tiebreak():
    """A VAT-number slip between two passes is settled by the verify read, so
    it must not count as a disagreement; a line-level slip still does."""
    a = _read(invoice={"invoice_no": "I", "seller_vat_number": "310123456700003", "lines": [{"item_code": "1", "description_ar": "x", "unit_price": 1, "quantity": 2, "amount": 2}]})
    b = _read(invoice={"invoice_no": "I", "seller_vat_number": "301123456700003", "lines": [{"item_code": "1", "description_ar": "x", "unit_price": 1, "quantity": 2, "amount": 2}]})
    assert not gv._same(a["docs"], b["docs"])
    assert gv._same(gv._without_key_fields(a["docs"]), gv._without_key_fields(b["docs"]))
    c = _read(invoice={"invoice_no": "I", "seller_vat_number": "310123456700003", "lines": [{"item_code": "1", "description_ar": "x", "unit_price": 1, "quantity": 3, "amount": 3}]})
    assert not gv._same(gv._without_key_fields(a["docs"]), gv._without_key_fields(c["docs"]))


def test_extraction_cache_is_off_by_default(tmp_path, monkeypatch):
    from app.core.config import get_settings

    assert get_settings().extraction_cache is False
    monkeypatch.setattr(gv, "_CACHE_DIR", tmp_path)
    cache = tmp_path / "x.json"
    gv._store_cache(cache, {"docs": {}, "anchors": {}})
    assert not cache.exists()
    cache.write_text('{"docs": {}, "anchors": {}}', encoding="utf-8")
    assert gv._load_cache(cache) is None


def test_numeric_item_codes_are_coerced_and_recap_duplicates_resolved():
    out = gv.validate_read({"boq": [
        {"item_code": 6.1, "description_ar": "كسوة", "unit_price": 330, "quantity": 499.57, "page": 60},
        {"item_code": 21, "description_ar": "x", "unit_price": 1, "quantity": 1},
    ]})
    assert [r["item_code"] for r in out["docs"]["boq"]] == ["6.1", "21"]
    a = _read(boq=[{"item_code": "9.10", "description_ar": "جبس بورد", "unit_price": 180, "quantity": 2933.4, "page": 60}])
    b = _read(boq=[{"item_code": "9.10", "description_ar": "", "unit_price": 23, "quantity": 96, "page": 73},  # recap page
                   {"item_code": "9.30", "description_ar": "", "unit_price": 100, "quantity": 1496, "page": 73},
                   {"item_code": "9.30", "description_ar": "بلوك", "unit_price": 100, "quantity": 1496, "page": 61}])
    m = gv.merge_reads([a, b])["docs"]["boq"]
    assert [(r["item_code"], r["unit_price"], r["page"]) for r in m] == [("9.10", 180, 60), ("9.30", 100, 61)]


def test_canonical_code_alignment_only_when_unambiguous():
    docs = ClaimDocuments.model_validate({
        "invoice": {"invoice_no": "I", "lines": [
            {"item_code": "9.10", "description_ar": "x", "unit_price": 180, "quantity": 1, "amount": 180},
            {"item_code": "6.10", "description_ar": "y", "unit_price": 330, "quantity": 1, "amount": 330},
            {"item_code": "7.10", "description_ar": "z", "unit_price": 250, "quantity": 1, "amount": 250},
        ]},
        "boq": [{"item_code": "9.1", "description_ar": "x", "unit_price": 180, "quantity": 5},
                {"item_code": "6.1", "description_ar": "y", "unit_price": 330, "quantity": 5},
                {"item_code": "6.10", "description_ar": "y2", "unit_price": 1, "quantity": 5},  # both forms exist: ambiguous, leave
                {"item_code": "7.1", "description_ar": "z", "unit_price": 250, "quantity": 5},
                {"item_code": "7.01", "description_ar": "z", "unit_price": 250, "quantity": 5}],
    })
    assert rc.canonical_code("10.10") == "10.1" and rc.canonical_code("OF-101") == "of-101" and rc.canonical_code("2.1.1") == "2.1.1"
    assert rc.align_codes(docs) == ["9.10 -> 9.1", "7.10 -> 7.1"]
    assert [l.item_code for l in docs.invoice.lines] == ["9.1", "6.10", "7.1"]


def test_printed_day_month_year_dates_become_iso():
    assert gv.normalize_date("12-07-2026") == "2026-07-12"
    assert gv.normalize_date("2026-7-2") == "2026-07-02"
    assert gv.normalize_date("١٢/٠٧/٢٠٢٦") == "2026-07-12"
    assert gv.normalize_date("خمسة أشهر من تاريخ محضر بدء المشروع") == "خمسة أشهر من تاريخ محضر بدء المشروع"
    assert gv.normalize_date("1447/01/15") == "1447-01-15"  # Hijri-looking ISO passes through untouched
    out = gv.validate_read({"coc": {"coc_no": "C", "coc_date": "12-07-2026"}, "invoice": {"invoice_no": "I", "invoice_date": "10/07/2026", "lines": []},
                            "contract": {"contract_no": "K", "start_date": "2024-12-25", "end_date": "خمسة أشهر"}})
    assert out["docs"]["coc"]["coc_date"] == "2026-07-12" and out["docs"]["invoice"]["invoice_date"] == "2026-07-10"
    assert out["docs"]["contract"]["end_date"] == "خمسة أشهر"


def test_contract_value_incl_vat_never_becomes_the_base():
    out = gv.validate_read({"contract": {"contract_no": "K", "value_base": 23115000.0, "value_with_vat": 23115000.0}})
    assert out["docs"]["contract"]["value_base"] == 20100000.0
    out = gv.validate_read({"contract": {"contract_no": "K", "value_base": 20100000.0, "value_with_vat": 23115000.0}})
    assert out["docs"]["contract"]["value_base"] == 20100000.0  # consistent pair untouched
    out = gv.validate_read({"contract": {"contract_no": "K", "value_base": 620000.0, "value_with_vat": 0}})
    assert out["docs"]["contract"]["value_base"] == 620000.0  # nothing to compare against


def test_fused_files_keep_their_source_file_on_every_value():
    """Contract in one file, BoQ in another: after the merge each header,
    line and clause still names the file (and page) it was read from."""
    contract = gv.stamp_source(_read(contract={"contract_no": "K", "page": 5, "penalty_terms": [{"rate_percent": 10.0, "page": 37}]})["docs"], "Contract.pdf")
    boq = gv.stamp_source(_read(boq=[{"item_code": "9.10", "description_ar": "x", "unit_price": 180, "quantity": 1, "page": 3}])["docs"], "BoQ.pdf")
    m = gv.merge_reads([{"docs": contract, "anchors": {}}, {"docs": boq, "anchors": {}}])["docs"]
    assert m["contract"]["source_file"] == "Contract.pdf" and m["contract"]["penalty_terms"][0]["source_file"] == "Contract.pdf"
    assert m["boq"][0] == {**m["boq"][0], "source_file": "BoQ.pdf", "page": 3}
    docs = ClaimDocuments.model_validate(m)
    assert docs.boq[0].source_file == "BoQ.pdf" and docs.contract.penalty_terms[0].source_file == "Contract.pdf"
    # the same row read from two files (a BoQ appendix repeated) is still one row
    again = gv.stamp_source(_read(boq=[{"item_code": "9.10", "description_ar": "x", "unit_price": 180, "quantity": 1, "page": 9}])["docs"], "Appendix.pdf")
    assert len(gv.merge_reads([{"docs": boq, "anchors": {}}, {"docs": again, "anchors": {}}])["docs"]["boq"]) == 1


def test_scan_order_stays_near_the_cited_page():
    assert scan_order(37, 76, 3) == [37, 36, 38]
    assert scan_order(1, 76, 3) == [1, 2, 3]
    assert scan_order(76, 76, 3) == [76, 75, 74]
    assert scan_order(4, 10, 1) == [4]
    assert scan_order(9, 3, 5) == [3, 2, 1]  # out-of-range cite clamps to the last page
