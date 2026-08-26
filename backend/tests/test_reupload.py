"""Re-uploading a document on a wizard step must replace the previous one —
record and staged file — so the next run reads only the new document."""

import shutil
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import submissions


def _files(claim: dict, doc_type: str) -> list[str]:
    return [f["path"] for f in claim["source_files"] if f["doc_type"] == doc_type]


def test_reupload_replaces_slot_and_stale_file(monkeypatch):
    # Staged paths are stored relative to PROJECT_ROOT, so the throwaway
    # upload dir must live under it.
    upload_dir = submissions.PROJECT_ROOT / "backend" / ".test_uploads" / uuid.uuid4().hex
    monkeypatch.setattr(submissions, "UPLOAD_DIR", upload_dir)
    client = TestClient(app)

    created = client.post(
        "/api/submissions",
        data={"invoice_no": "INV-1"},
        files={"invoice": ("inv_v1.pdf", b"%PDF-1", "application/pdf")},
    ).json()
    cid = created["id"]
    old_invoice = submissions.PROJECT_ROOT / _files(created, "invoice")[0]
    assert old_invoice.is_file()

    # Step 1 re-upload (different file name): one invoice slot, old file gone.
    updated = client.post(
        f"/api/submissions/{cid}",
        files={"invoice": ("inv_v2.pdf", b"%PDF-2", "application/pdf")},
    ).json()
    assert [p.rsplit("/", 1)[-1] for p in _files(updated, "invoice")] == ["inv_v2.pdf"]
    assert not old_invoice.exists()

    # Steps 2/3: BoQ replaces BoQ; a delivery note supersedes a COC.
    client.post(f"/api/submissions/{cid}", files={"contract_boq": ("boq_a.pdf", b"a", "application/pdf")})
    client.post(f"/api/submissions/{cid}", files={"coc": ("coc.pdf", b"c", "application/pdf")})
    updated = client.post(
        f"/api/submissions/{cid}",
        files={
            "contract_boq": ("boq_b.pdf", b"b", "application/pdf"),
            "delivery_note": ("dn.pdf", b"d", "application/pdf"),
        },
    ).json()
    assert len(_files(updated, "contract_boq")) == 1
    assert _files(updated, "contract_boq")[0].endswith("boq_b.pdf")
    assert _files(updated, "coc") == []
    assert len(_files(updated, "delivery_note")) == 1

    # Step 5: "other" uploads replace, not accumulate.
    client.post(f"/api/submissions/{cid}", files=[("other", ("o1.pdf", b"1", "application/pdf"))])
    updated = client.post(
        f"/api/submissions/{cid}", files=[("other", ("o2.pdf", b"2", "application/pdf"))]
    ).json()
    assert len(_files(updated, "other")) == 1

    # Only the live files remain on disk.
    names = sorted(p.name for p in (upload_dir / cid).iterdir())
    assert names == ["boq_b.pdf", "dn.pdf", "inv_v2.pdf", "o2.pdf"]
    shutil.rmtree(upload_dir.parent, ignore_errors=True)


def test_export_zips_matching_documents_only(monkeypatch):
    import io
    import zipfile

    upload_dir = submissions.PROJECT_ROOT / "backend" / ".test_uploads" / uuid.uuid4().hex
    monkeypatch.setattr(submissions, "UPLOAD_DIR", upload_dir)
    client = TestClient(app)
    pdf = "application/pdf"
    cid = client.post(
        "/api/submissions",
        files=[
            ("invoice", ("INV-0342.pdf", b"i", pdf)),
            ("contract_boq", ("BoQ-RFQ26-042.pdf", b"b", pdf)),
            ("coc", ("COC-00342.pdf", b"c", pdf)),
            ("other", ("notes.pdf", b"o", pdf)),
        ],
    ).json()["id"]
    client.post(
        f"/api/submissions/{cid}",
        data={"detected_attachments": '[{"file_name":"cr.pdf","doc_key":"cr"}]'},
        files=[("attachment_docs", ("cr.pdf", b"cr", pdf))],
    )

    res = client.get(f"/api/claims/{cid}/export")
    assert res.status_code == 200
    assert res.headers["content-disposition"].endswith(f'"{cid}_matching_documents.zip"')
    names = sorted(zipfile.ZipFile(io.BytesIO(res.content)).namelist())
    assert names == sorted([f"{cid}_Invoice_INV-0342.pdf", "BoQ-RFQ26-042.pdf", f"{cid}_COC_COC-00342.pdf"])
    shutil.rmtree(upload_dir.parent, ignore_errors=True)
