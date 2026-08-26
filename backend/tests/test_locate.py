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
