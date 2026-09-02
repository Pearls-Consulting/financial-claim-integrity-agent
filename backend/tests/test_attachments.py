"""Attachment identification — the deterministic safety net.

The GPT classification path needs network; these tests pin the behavior that
must hold WITHOUT it: every upload comes back classified (filename heuristic),
so the pre-finance flow never dies in a demo.
"""

from app.services.extraction.attachments import classify_attachments, entries_from_raw, heuristic_doc_key


def test_heuristic_identifies_demo_vendor_files():
    cases = {
        "CR-Alwaha-1010777045.pdf": "commercial registration",
        # real vendor-file namings: bare CR token, any separator
        "CR Safari 1010034600.pdf": "commercial registration",
        "Att 1_ILF - CR - Valid until 08.06.2025 - Arabic.pdf": "commercial registration",
        "Company CR Contracting-Arabic.pdf": "commercial registration",  # 'Contracting' must not win
        "شهادة_السجل_التجاري_شركة_بزار_نجد[1].pdf": "commercial registration",
        "سجل-تجاري-شركة-تكثير-المحدودة.pdf": "commercial registration",
        "crystal facilities profile.pdf": "other",  # 'cr' inside a word never trips
        "Zakat-Alwaha-Certificate.pdf": "zakat certificate",
        "GOSI-Alwaha-Certificate.pdf": "gosi certificate",
        "AwardLetter-Alwaha-26400871.pdf": "award letter",
        "WorkCommencement-Alwaha-RFQ26-042.pdf": "work commencement",
        "random-scan-001.pdf": "other",
    }
    for name, expected in cases.items():
        assert heuristic_doc_key(name) == expected, name


def test_unreadable_uploads_still_classified_by_filename():
    """OCR produced nothing readable -> pure heuristic fallback, no model call."""
    detected = classify_attachments([("Zakat-Alwaha-Certificate.pdf", ""), ("mystery.pdf", "")])
    assert [d.doc_key for d in detected] == ["zakat certificate", "other"]
    assert all(d.fields == {} for d in detected)


def test_bundle_scan_yields_one_detection_per_document():
    """One 'شهادات' bundle PDF -> zakat + CR + ..., each with its own page.
    Client-observed 2026-09-01: a 7-page bundle (zakat p1, chamber p2, CR p3,
    saudization p4 ...) must not collapse into a single 'zakat' detection."""
    raw = {"documents": [
        {"doc_key": "zakat certificate", "page": 1, "fields": {"reference_no": "1027070528"}},
        {"doc_key": "commercial registration", "page": 3, "fields": {"cr_number": "7030999093"}},
        {"doc_key": "zakat certificate", "page": 5, "fields": {}},  # duplicate type: first wins
        {"doc_key": "iban letter", "page": 7, "fields": {}},  # unknown key: dropped
    ]}
    dets = entries_from_raw(raw, "شهادات شركة  صناع.pdf")
    assert [(d.doc_key, d.page) for d in dets] == [("zakat certificate", 1), ("commercial registration", 3)]
    assert dets[1].fields["cr_number"] == "7030999093"
    assert all(d.file_name == "شهادات شركة  صناع.pdf" for d in dets)


def test_legacy_single_object_output_still_parses():
    dets = entries_from_raw({"doc_key": "gosi certificate", "page": 1, "fields": {"reference_no": "115206555"}}, "gosi.pdf")
    assert [(d.doc_key, d.page) for d in dets] == [("gosi certificate", 1)]


def test_nothing_recognized_parses_to_empty():
    assert entries_from_raw({"documents": [{"doc_key": "other", "page": 1}]}, "x.pdf") == []
    assert entries_from_raw({"doc_key": "other"}, "x.pdf") == []
    assert entries_from_raw("garbage", "x.pdf") == []
