"""The OCR-locate matching ladder (pure parts — no Azure call) and the
page-marker annotation the structuring prompt relies on."""

from app.services.extraction.locate import _match, _match_number, _norm, _rtl_percent, _value_digits
from app.services.extraction.structuring import annotate_pages


def _items(*words):
    return [{"content": w, "poly": [(i, 0.0), (i + 1, 0.0), (i + 1, 1.0), (i, 1.0)]} for i, w in enumerate(words)]


def test_norm_folds_arabic_digits_and_separators():
    assert _value_digits("23,115,000.00") == "23115000"
    assert _norm("٢٣٬١١٥٬٠٠٠") == _norm("23,115,000") == "23115000"
    assert _norm("شرکة") == _norm("شركة")


def test_match_finds_value_split_across_words():
    items = _items("قيمة", "العقد", "23,115,000.00", "ريال")
    assert _match(items, _norm("23,115,000.00"))
    assert not _match(items, _norm("999"))


def test_short_needle_requires_word_boundary():
    # "10" must not match inside "2100"; it must match the standalone word.
    assert not _match(_items("2100"), "10")
    assert _match(_items("غرامة", "10", "%"), "10")


def test_rtl_percent_swaps_form():
    assert _rtl_percent("10%") == "%10"
    assert _rtl_percent("قيمة 10%") is None


def test_number_match_on_integer_digit_run():
    items = _items("الإجمالي", "٢٣٬١١٥٬٠٠٠")
    assert _match_number(items, _value_digits("23,115,000.00"))
    # digit-boundary guard: 650000 must not hit inside 3650000
    assert not _match_number(_items("3650000"), "650000")


def test_annotate_pages_numbers_cu_page_breaks():
    md = "first page\n<!-- PageBreak -->\nsecond page\n<!-- PageBreak -->\nthird"
    out = annotate_pages(md)
    assert out.startswith("[[PAGE 1]]")
    assert "[[PAGE 2]]" in out and "[[PAGE 3]]" in out
    assert "PageBreak" not in out


# ── the number first (live-demo blunder: an item code / clause won the highlight) ──

from app.services.extraction.locate import _find_value, _match_all, _needles


def _layout(*words):
    return {"w": 100.0, "h": 100.0, "words": _items(*words), "lines": []}


def test_numeric_needle_must_be_the_whole_printed_number():
    # "89.9" is not inside "189.9" / "89.95"; item "2.14" is not inside clause "3.2.14" / "2.14.1"
    assert not _match_all(_items("189.9"), "89.9")
    assert not _match_all(_items("89.95"), "89.9")
    assert not _match_all(_items("3.2.14"), "2.14")
    assert not _match_all(_items("2.14.1"), "2.14")
    assert _match_all(_items("الكمية", "89.9", "م2"), "89.9")
    assert _match_all(_items("(2.14)"), "2.14")
    # neighbouring WORDS never veto: the flattened run "950.00|1,553.00|2.1.1" still holds 1553.00
    assert _match_all(_items("950.00", "1,553.00", "2.1.1"), _norm("1553.00"))
    assert _match_all(_items("2,100.00", "89.79", "2.2.2"), "89.79")
    assert _match_number(_items("950.00", "1,553.00"), "1553")


def test_decimal_comma_survives_normalisation_and_is_tried_as_a_variant():
    assert _norm("2,50") == "2,50" and _norm("23,115,000.00") == "23115000.00" and _norm("1,553.00") == "1553.00"
    assert _norm("٢٫٥٠") == "2.50" and _norm("٢٣٬١١٥") == "23115"
    assert "2,50%" in _needles(["2.50%"]) and "%2,50" in _needles(["2.50%"])
    assert _needles(["1.500"]) == ["1.500"]  # a 3-digit fraction is not turned into a thousand
    assert _find_value({"w": 1, "h": 1, "words": _items("(", "%", "2,50", ")", "من"), "lines": []}, ["2.5%", "2.50%"], anchor=None)


def test_number_is_never_highlighted_as_a_whole_line():
    layout = {"w": 100.0, "h": 100.0, "words": _items("قيمة", "الأعمال"),
              "lines": [{"content": "غرامة 10% من قيمة الأعمال", "poly": [(0, 0), (9, 0), (9, 1), (0, 1)]}]}
    assert _find_value(layout, ["10%"], anchor=None) == []  # not in words → no line-level fallback for numbers
    assert _find_value(layout, ["غرامة 10% من قيمة الأعمال"], anchor=None)  # text may still match a line


def test_anchor_picks_the_nearest_occurrence_but_never_replaces_the_value():
    # two "10%" on the page; the clause anchor sits next to the second one
    words = _items("10%", "x", "x", "x", "x", "x", "x", "x", "x", "x", "إذا", "تأخر", "المقاول", "10%")
    layout = {"w": 100.0, "h": 100.0, "words": words, "lines": []}
    from app.services.extraction.locate import _find_anchor

    anchor = _find_anchor(layout, ["إذا تأخر المقاول"])
    assert anchor
    hit = _find_value(layout, ["10%"], anchor=anchor)
    assert hit == [words[13]["poly"]]  # the 10% next to the clause, not the whole clause
    assert _find_value(layout, ["10%"], anchor=None) == [words[0]["poly"]]


def test_document_search_prefers_value_then_also_on_cited_page_only(monkeypatch):
    """Server-side ordering: value on cited page > value on neighbours > item
    code on the cited page; the item code must never pick another page."""
    import app.services.extraction.locate as loc

    pages = {
        1: _layout("البند", "3.2.14", "غرامة"),  # clause numbering that contains the item code
        5: _layout("2.14", "حوائط", "89.79"),  # the BoQ row
        6: _layout("2.14", "متابعة"),  # recap row: item code, no quantity
    }
    monkeypatch.setattr(loc, "_get_page_layout", lambda path, page: pages.get(page) or _layout())
    monkeypatch.setattr(loc, "_page_count", lambda path: 6)
    from pathlib import Path

    doc = Path("contract.pdf")
    # cited page right: the number on that page
    assert loc.locate_value_in_document(doc, 5, ["89.79"], also=["2.14"], max_scan_pages=3)["page"] == 5
    # cited page off by one: the number on the neighbour, not the item code on the cited page
    r = loc.locate_value_in_document(doc, 6, ["89.79"], also=["2.14"], max_scan_pages=3)
    assert r["found"] and r["page"] == 5
    # number nowhere near: fall back to the item code on the CITED page — never page 1's clause
    r = loc.locate_value_in_document(doc, 6, ["55.5"], also=["2.14"], max_scan_pages=3)
    assert r["found"] and r["page"] == 6
    r = loc.locate_value_in_document(doc, 3, ["55.5"], also=["2.14"], max_scan_pages=3)
    assert not r["found"] and r["page"] == 3
