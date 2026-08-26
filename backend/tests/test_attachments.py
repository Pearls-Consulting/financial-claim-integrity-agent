"""Attachment identification — the deterministic safety net.

The GPT classification path needs network; these tests pin the behavior that
must hold WITHOUT it: every upload comes back classified (filename heuristic),
so the pre-finance flow never dies in a demo.
"""

from app.services.extraction.attachments import classify_attachments, heuristic_doc_key


def test_heuristic_identifies_demo_vendor_files():
    cases = {
        "CR-Alwaha-1010777045.pdf": "commercial registration",
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
