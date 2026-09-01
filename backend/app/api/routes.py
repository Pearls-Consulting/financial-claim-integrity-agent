import io
import json
import mimetypes
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import require_session
from app.core.config import get_settings
from pydantic import BaseModel

from app.domain.models import BoqLine, Claim, ClaimType, ContractDoc, ContractKind, DetectedAttachment, InvoiceDoc, Penalty, RunResult, Verdict
from app.services import store
from app.domain.stages import STAGES, Stage
from app.services import pipeline, submissions
from app.services.datasource import get_source

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Where a claim's staged file may legitimately resolve to. On the server the
# release's backend/uploads is a SYMLINK into the persistent data dir, so a
# resolved path is no longer under the release root — checking only
# PROJECT_ROOT rejected every uploaded document (viewer + locate returned
# "File not found" in production while working locally).
_ALLOWED_ROOTS = (PROJECT_ROOT, submissions.UPLOAD_DIR.resolve())


def _staged_path(claim: Claim, index: int) -> Path:
    """The on-disk file for claim.source_files[index], or 404."""
    if index < 0 or index >= len(claim.source_files):
        raise HTTPException(status_code=404, detail=f"Claim {claim.id} has no file #{index}")
    path = (PROJECT_ROOT / claim.source_files[index].path).resolve()
    if not any(path.is_relative_to(root) for root in _ALLOWED_ROOTS) or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path

# Every claim/document endpoint requires a signed-in session (httpOnly cookie,
# see app/api/deps.py). /health and /api/auth/* live outside this router.
router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def _find_claim(claim_id: str) -> Claim | None:
    return submissions.get(claim_id) or get_source().get_claim(claim_id)


def _annotate(claim: Claim, steps: dict[str, int] | None = None, verdicts: dict[str, str] | None = None) -> Claim:
    """Fill the guided-review annotations from the progress/runs store."""
    steps = store.progress_map() if steps is None else steps
    verdicts = store.verdict_map() if verdicts is None else verdicts
    claim.review_step = steps.get(claim.id, 0)
    verdict = verdicts.get(claim.id)
    claim.latest_verdict = Verdict(verdict) if verdict else None
    return claim


@router.get("/stages", response_model=list[Stage])
def list_stages() -> list[Stage]:
    return STAGES


@router.get("/claims", response_model=list[Claim])
def list_claims() -> list[Claim]:
    steps, verdicts = store.progress_map(), store.verdict_map()
    # newest submissions first, then the static ERP claims
    return [_annotate(c, steps, verdicts) for c in submissions.list_claims()[::-1] + get_source().list_claims()]


@router.get("/claims/{claim_id}", response_model=Claim)
def get_claim(claim_id: str) -> Claim:
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    return _annotate(claim)


@router.post("/claims/{claim_id}/progress")
def set_progress(claim_id: str, step: int = Form(...)) -> dict:
    """Persist where the guided review stands so a closed tab resumes in place."""
    if _find_claim(claim_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    if not 0 <= step <= 6:
        raise HTTPException(status_code=422, detail=f"step must be 0-6, got {step}")
    store.set_progress(claim_id, step)
    return {"claim_id": claim_id, "step": step}


@router.post("/claims/{claim_id}/run", response_model=RunResult)
def run_claim(claim_id: str, gates: str | None = None) -> RunResult:
    """Run every gate, or `?gates=intake,boq_match` for the guided flow's
    cumulative step-by-step runs."""
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    gate_ids: set[str] | None = None
    if gates:
        gate_ids = {g.strip() for g in gates.split(",") if g.strip()}
        unknown = gate_ids - {s.id for s in STAGES}
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown gates: {', '.join(sorted(unknown))}")
    try:
        return pipeline.run_claim(claim, gate_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # The reader could not produce anything (Azure outage, throttling,
        # an unreadable file): tell the reviewer, do not dump a stack trace.
        raise HTTPException(status_code=502, detail=f"Document reading failed — {exc}. Try again in a moment.") from exc


@router.get("/claims/{claim_id}/run", response_model=RunResult | None)
def latest_run(claim_id: str) -> RunResult | None:
    return pipeline.latest_run(claim_id)


# Documents the matching gates read, in pack order. Pre-finance attachments
# (CR, GOSI, Zakat, bank letter...) are compliance proofs, not matching inputs,
# and stay out of the export.
_EXPORT_DOC_TYPES = ("invoice", "contract_boq", "coc", "delivery_note")
_EXPORT_LABELS = {"invoice": "Invoice", "coc": "COC", "delivery_note": "DeliveryNote"}


def export_entries(claim: Claim) -> list[tuple[str, Path]]:
    """(name in zip, path on disk) for every matching document.

    Claim-bound documents (invoice, acceptance) are prefixed with the claim id
    so the pack stays traceable once unzipped; the contract/BoQ is a contract
    document shared across claims and keeps its own file name.
    """
    entries: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for doc_type in _EXPORT_DOC_TYPES:
        for f in claim.source_files:
            if f.doc_type != doc_type:
                continue
            path = (PROJECT_ROOT / f.path).resolve()
            if not any(path.is_relative_to(root) for root in _ALLOWED_ROOTS) or not path.is_file():
                continue
            name = path.name if doc_type == "contract_boq" else f"{claim.id}_{_EXPORT_LABELS[doc_type]}_{path.name}"
            if name in seen:  # two files with the same name — keep both
                name = f"{path.stem}_{len(seen)}{path.suffix}"
            seen.add(name)
            entries.append((name, path))
    return entries


@router.get("/claims/{claim_id}/export")
def export_claim(claim_id: str) -> StreamingResponse:
    """Zip of the documents that took part in the matching, for hand-off to
    finance / audit: invoice, contract/BoQ, acceptance document."""
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    entries = export_entries(claim)
    if not entries:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} has no matching documents staged")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in entries:
            zf.write(path, arcname=name)
    buf.seek(0)
    filename = f"{claim.id}_matching_documents.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/claims/{claim_id}/files/{index}")
def claim_file(claim_id: str, index: int) -> FileResponse:
    """Stream one of the claim's staged source files inline (for the viewer)."""
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    path = _staged_path(claim, index)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        content_disposition_type="inline",
        filename=path.name,
    )


class LocateRequest(BaseModel):
    page: int = 1
    values: list[str]  # candidate renderings of the same value, tried in order
    also: list[str] = []  # row context (item code, unit price): cited page only, never picks a page
    anchors: list[str] = []  # the verbatim clause / excerpt: disambiguates, then last resort


class LocateResponse(BaseModel):
    found: bool
    polygons: list[list[dict[str, float]]]  # [[{x,y} × 4], …], x,y ∈ [0,1]
    page: int | None = None  # where it was ACTUALLY found


@router.post("/claims/{claim_id}/files/{index}/locate", response_model=LocateResponse)
def locate_in_file(claim_id: str, index: int, req: LocateRequest) -> LocateResponse:
    """OCR-locate a value on a staged document page — the viewer's fallback
    when the PDF text layer can't be matched (scanned contracts). Returns
    word polygons as page fractions; cached per (content, page) forever."""
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    path = _staged_path(claim, index)
    # CU is the viewer's fallback on every real engine (gpt | azure) — the
    # only place it is still called, one rendered page at a time.
    s = get_settings()
    if s.extractor_engine == "mock" or not (s.azure_cu_endpoint and s.azure_cu_key):
        return LocateResponse(found=False, polygons=[])
    from app.services.extraction.locate import locate_value_in_document

    try:
        result = locate_value_in_document(path, max(req.page, 1), req.values, also=req.also, anchors=req.anchors)
    except Exception:
        return LocateResponse(found=False, polygons=[])
    return LocateResponse(**result)


class InvoiceExtract(BaseModel):
    """What the step-1 invoice read yields. ``is_invoice`` is False when the
    reader ran fine but found no invoice in the pages — ``looks_like`` then
    names what the document resembled (contract | coc | receipt | unknown)
    so the wizard can tell the reviewer explicitly."""

    invoice: InvoiceDoc | None = None
    is_invoice: bool = True
    looks_like: str = ""


@router.post("/extract/invoice", response_model=InvoiceExtract | None)
def extract_invoice(invoice: UploadFile = File(...)) -> InvoiceExtract | None:
    """Read one invoice on its own — powers the intake form's autofill.

    Null means "no reader available" (mock engine) and the form stays manual.
    The read is disk-cached by content hash, so the later full-claim run
    re-reads this same file for free.

    A read that finishes but finds no invoice in the pages comes back with
    ``is_invoice=False`` — the wizard shows that verdict explicitly instead
    of leaving the reviewer to infer it from an empty form.
    """
    docs = _read_single(invoice, "invoice")
    if docs is None:
        return None
    inv = docs.invoice
    if inv is not None and (inv.invoice_no or inv.total_with_vat or inv.lines):
        return InvoiceExtract(invoice=inv)
    # The read finished but the pages hold no invoice — say what they DO hold.
    looks_like = (
        "contract"
        if (docs.contract or docs.boq)
        else "coc" if docs.coc else "receipt" if docs.receipt else "unknown"
    )
    return InvoiceExtract(invoice=None, is_invoice=False, looks_like=looks_like)


def _read_single(upload: UploadFile, doc_type: str):
    """One uploaded file → ClaimDocuments on the configured engine; None on
    the mock engine (no reader)."""
    engine = get_settings().extractor_engine
    if engine not in ("gpt", "azure"):
        return None
    staged = submissions.stage_file(f"_prefill/{uuid.uuid4().hex[:8]}", upload, doc_type)
    path = PROJECT_ROOT / staged.path
    try:
        if engine == "gpt":
            from app.services.extraction.gpt_vision import read_file, to_documents

            try:
                return to_documents(read_file(path, doc_type))
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Read failed: {exc}") from exc
        from app.services.extraction.cu_client import analyze_layout
        from app.services.extraction.structuring import structure_documents

        result = analyze_layout(path)
        if not result.ok:
            raise HTTPException(status_code=422, detail=f"OCR failed: {result.error}")
        return structure_documents([(path.name, doc_type, result.markdown)])
    finally:
        _discard_prefill(path)


def _discard_prefill(path: Path) -> None:
    """A prefill copy is read once; the wizard uploads the file again with
    the submission. Leaving it behind leaks 20 MB per contract pick on a
    24 GB shared box. Also sweeps batches older than an hour left by
    earlier builds / interrupted requests."""
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()  # the per-pick batch dir, if now empty
    except OSError:
        pass
    prefill_root = submissions.UPLOAD_DIR / "_prefill"
    cutoff = time.time() - 3600
    try:
        for batch in prefill_root.iterdir():
            if batch.is_dir() and batch.stat().st_mtime < cutoff:
                shutil.rmtree(batch, ignore_errors=True)
    except OSError:
        pass


class ContractExtract(BaseModel):
    """What one contract/BoQ read yields for the step-2 suggestions."""

    boq: list[BoqLine] = []
    contract: ContractDoc | None = None


@router.post("/extract/boq", response_model=ContractExtract | None)
def extract_boq(contract_boq: list[UploadFile] = File(...)) -> ContractExtract | None:
    """Read the contract / BoQ file(s) on their own — powers the step-2
    suggestions (contract value, end date), reviewer-confirmed, never
    silently trusted. One combined document or several files (the
    contract, the BoQ, appendices) — they are read in parallel and fused.

    Null means "no reader available" (mock engine).
    """
    docs = _read_many([u for u in contract_boq if u.filename], "contract_boq")
    return ContractExtract(boq=docs.boq, contract=docs.contract) if docs else None


def _read_many(uploads: list[UploadFile], doc_type: str):
    """Several uploaded files of one document type → ONE ClaimDocuments
    (headers: first read wins, lines concatenate, BoQ codes de-duplicated);
    None on the mock engine."""
    engine = get_settings().extractor_engine
    if engine not in ("gpt", "azure") or not uploads:
        return None
    batch = f"_prefill/{uuid.uuid4().hex[:8]}"
    paths = [PROJECT_ROOT / submissions.stage_file(batch, u, doc_type).path for u in uploads]
    try:
        if engine == "gpt":
            from app.services.extraction.gpt_vision import merge_reads, read_files, to_documents

            reads = [r for r in read_files([(p, doc_type) for p in paths]) if r]
            if not reads:
                raise HTTPException(status_code=422, detail="Read failed: none of the files could be read")
            return to_documents(merge_reads(reads))
        from app.services.extraction.cu_client import analyze_layout
        from app.services.extraction.structuring import structure_documents

        ocr = []
        for p in paths:
            result = analyze_layout(p)
            if result.ok:
                ocr.append((p.name, doc_type, result.markdown))
        if not ocr:
            raise HTTPException(status_code=422, detail="OCR failed on every file")
        return structure_documents(ocr)
    finally:
        for p in paths:
            _discard_prefill(p)


@router.post("/extract/attachments", response_model=list[DetectedAttachment])
def extract_attachments(files: list[UploadFile] = File(...)) -> list[DetectedAttachment]:
    """Identify the uploaded vendor-file documents for the pre-finance gate —
    which is the CR, the zakat certificate, the award letter... — and lift
    their printed identity fields (CR number, VAT number, reference no.).

    gpt engine: one GPT vision read per upload (first pages only), all in
    parallel, cached by content. Azure engine: CU OCR (disk-cached) + one GPT
    classification call. Both keep a filename heuristic as the safety net.
    Mock engine: heuristic only, no fields — the demo flow still works, just
    without extracted values.
    """
    from app.services.extraction.attachments import (
        classify_attachments,
        classify_attachments_vision,
        heuristic_doc_key,
    )

    engine = get_settings().extractor_engine
    if engine not in ("gpt", "azure"):
        return [
            DetectedAttachment(file_name=f.filename or "file", doc_key=heuristic_doc_key(f.filename or ""))
            for f in files
        ]
    batch = f"_prefill/{uuid.uuid4().hex[:8]}"
    staged = [(submissions.stage_file(batch, upload, "attachment"), upload.filename) for upload in files]
    try:
        if engine == "gpt":
            return classify_attachments_vision(
                [PROJECT_ROOT / f.path for f, _ in staged],
                [name or Path(f.path).name for f, name in staged],
            )
        from app.services.extraction.cu_client import analyze_layout

        markdown_by_file: list[tuple[str, str]] = []
        for f, name in staged:
            result = analyze_layout(PROJECT_ROOT / f.path)
            markdown_by_file.append((name or Path(f.path).name, result.markdown if result.ok else ""))
        return classify_attachments(markdown_by_file)
    finally:
        for f, _ in staged:
            _discard_prefill(PROJECT_ROOT / f.path)


@router.post("/submissions", response_model=Claim)
def create_submission(
    # Header fields mirroring the D365 استلام المطالبات overview form.
    po_no: str = Form(""),
    project_no: str = Form(""),
    project_name_ar: str = Form(""),
    project_name_en: str = Form(""),
    vendor_account: str = Form(""),
    vendor_name_ar: str = Form(""),
    vendor_name_en: str = Form(""),
    contract_value: float = Form(0.0),
    contract_kind: ContractKind = Form(ContractKind.works),
    contract_end_date: str = Form(""),
    claim_amount_base: float = Form(0.0),
    vat_amount: float = Form(0.0),
    claim_amount_total: float = Form(0.0),
    invoice_no: str = Form(""),
    payment_no: int = Form(0),
    claim_type: ClaimType = Form(ClaimType.periodic),
    claim_date: str = Form(""),
    cumulative_prior: float = Form(0.0),
    prior_payment_count: int = Form(0),
    # ERP-owned context the demo has no other source for.
    penalties: str = Form("[]"),  # JSON list of {reason_ar, amount, date}
    attachments: str = Form("[]"),  # JSON list of attachment names as filed
    # Document files, one slot per doc_type the extractor understands. The
    # contract/BoQ slot takes SEVERAL files (contract, BoQ, appendices) that
    # the extractor fuses into one contract view.
    invoice: UploadFile | None = File(None),
    contract_boq: list[UploadFile] = File(default=[]),
    coc: UploadFile | None = File(None),
    delivery_note: UploadFile | None = File(None),
    other: list[UploadFile] = File(default=[]),
) -> Claim:
    claim_id = submissions.next_claim_id()

    source_files = [
        submissions.stage_file(claim_id, upload, doc_type)
        for upload, doc_type in (
            (invoice, "invoice"),
            (coc, "coc"),
            (delivery_note, "delivery_note"),
        )
        if upload is not None and upload.filename
    ]
    source_files += [
        submissions.stage_file(claim_id, upload, "contract_boq") for upload in contract_boq if upload.filename
    ]
    source_files += [
        submissions.stage_file(claim_id, upload, "other") for upload in other if upload.filename
    ]

    try:
        penalty_list = [Penalty.model_validate(p) for p in json.loads(penalties)]
        attachment_list = [str(a) for a in json.loads(attachments)]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Bad penalties/attachments payload: {exc}") from exc

    claim = Claim(
        id=claim_id,
        po_no=po_no,
        project_no=project_no,
        project_name_ar=project_name_ar,
        project_name_en=project_name_en,
        vendor_account=vendor_account,
        vendor_name_ar=vendor_name_ar,
        vendor_name_en=vendor_name_en,
        contract_value=contract_value,
        contract_kind=contract_kind,
        contract_end_date=contract_end_date,
        claim_amount_base=claim_amount_base,
        vat_amount=vat_amount,
        claim_amount_total=claim_amount_total,
        invoice_no=invoice_no,
        payment_no=payment_no,
        claim_type=claim_type,
        claim_date=claim_date,
        cumulative_prior=cumulative_prior,
        prior_payment_count=prior_payment_count,
        source_files=source_files,
        origin="submitted",
    )
    claim.documents.penalties = penalty_list
    claim.documents.attachments = attachment_list
    submissions.add(claim)
    return claim


@router.post("/submissions/{claim_id}", response_model=Claim)
def update_submission(
    claim_id: str,
    # Any field the guided flow edits after creation; None = leave unchanged.
    po_no: str | None = Form(None),
    project_no: str | None = Form(None),
    project_name_ar: str | None = Form(None),
    project_name_en: str | None = Form(None),
    vendor_account: str | None = Form(None),
    vendor_name_ar: str | None = Form(None),
    vendor_name_en: str | None = Form(None),
    contract_value: float | None = Form(None),
    contract_kind: ContractKind | None = Form(None),
    contract_end_date: str | None = Form(None),
    claim_amount_base: float | None = Form(None),
    vat_amount: float | None = Form(None),
    claim_amount_total: float | None = Form(None),
    invoice_no: str | None = Form(None),
    payment_no: int | None = Form(None),
    claim_type: ClaimType | None = Form(None),
    claim_date: str | None = Form(None),
    cumulative_prior: float | None = Form(None),
    prior_payment_count: int | None = Form(None),
    penalties: str | None = Form(None),
    attachments: str | None = Form(None),
    # Vendor-file documents for the pre-finance gate: the files plus the
    # detections returned by /extract/attachments (aligned by file name).
    detected_attachments: str | None = Form(None),
    attachment_docs: list[UploadFile] = File(default=[]),
    # Documents attached at their gate's step; re-uploading replaces the slot.
    # contract_boq is a SET of files (contract, BoQ, appendices): a new set
    # replaces the whole previous set.
    invoice: UploadFile | None = File(None),
    contract_boq: list[UploadFile] = File(default=[]),
    coc: UploadFile | None = File(None),
    delivery_note: UploadFile | None = File(None),
    other: list[UploadFile] = File(default=[]),
) -> Claim:
    """Attach documents / ERP context to an ad-hoc submission between gate runs.

    Only submitted claims are mutable — ERP-sourced claims are read-only.
    """
    claim = submissions.get(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown submission {claim_id}")

    # One file per slot: a re-upload REPLACES the previous document (record and
    # staged file) so the next run re-reads only the new one. The acceptance
    # document is one slot too — a delivery note supersedes a COC and vice
    # versa, since the contract kind decides which the gate should read.
    slots = (
        (invoice, "invoice", ("invoice",)),
        (coc, "coc", ("coc", "delivery_note")),
        (delivery_note, "delivery_note", ("coc", "delivery_note")),
    )
    for upload, doc_type, replaces in slots:
        if upload is not None and upload.filename:
            submissions.drop_files(claim, lambda f, r=replaces: f.doc_type in r)
            claim.source_files.append(submissions.stage_file(claim_id, upload, doc_type))
    real_cb = [u for u in contract_boq if u.filename]
    if real_cb:
        submissions.drop_files(claim, lambda f: f.doc_type == "contract_boq")
        claim.source_files += [submissions.stage_file(claim_id, upload, "contract_boq") for upload in real_cb]
    real_other = [u for u in other if u.filename]
    if real_other:
        submissions.drop_files(claim, lambda f: f.doc_type == "other")
        claim.source_files += [submissions.stage_file(claim_id, upload, "other") for upload in real_other]

    updates = {
        "po_no": po_no, "project_no": project_no, "project_name_ar": project_name_ar,
        "project_name_en": project_name_en, "vendor_account": vendor_account,
        "vendor_name_ar": vendor_name_ar, "vendor_name_en": vendor_name_en,
        "contract_value": contract_value, "contract_kind": contract_kind,
        "contract_end_date": contract_end_date, "claim_amount_base": claim_amount_base,
        "vat_amount": vat_amount, "claim_amount_total": claim_amount_total,
        "invoice_no": invoice_no, "payment_no": payment_no, "claim_type": claim_type,
        "claim_date": claim_date, "cumulative_prior": cumulative_prior,
        "prior_payment_count": prior_payment_count,
    }
    for field_name, value in updates.items():
        if value is not None:
            setattr(claim, field_name, value)
    try:
        if penalties is not None:
            claim.documents.penalties = [Penalty.model_validate(p) for p in json.loads(penalties)]
        if attachments is not None:
            claim.documents.attachments = [str(a) for a in json.loads(attachments)]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Bad penalties/attachments payload: {exc}") from exc

    # Vendor-file documents: stage each upload under its detected type and
    # derive the ERP attachment list from the detections — the completeness
    # gate then validates what the agent actually SAW, not a checkbox.
    real_docs = [u for u in attachment_docs if u.filename]
    if real_docs:
        from app.services.extraction.attachments import heuristic_doc_key

        try:
            detected = (
                [DetectedAttachment.model_validate(d) for d in json.loads(detected_attachments)]
                if detected_attachments
                else []
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Bad detected_attachments payload: {exc}") from exc
        by_name = {d.file_name: d for d in detected}

        submissions.drop_files(claim, lambda f: f.doc_type.startswith("attachment"))
        final: list[DetectedAttachment] = []
        for upload in real_docs:
            det = by_name.get(upload.filename or "") or DetectedAttachment(
                file_name=upload.filename or "file", doc_key=heuristic_doc_key(upload.filename or "")
            )
            claim.source_files.append(submissions.stage_file(claim_id, upload, f"attachment:{det.doc_key}"))
            final.append(det)
        claim.documents.detected_attachments = final
        keys = {d.doc_key for d in final if d.doc_key != "other"}
        if any(f.doc_type == "contract_boq" for f in claim.source_files):
            keys |= {"contract", "boq"}  # the combined step-2 document covers both
        claim.documents.attachments = sorted(keys)

    submissions.add(claim)
    return claim
