import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.domain.models import Claim, ClaimType, InvoiceDoc, Penalty, RunResult, Verdict
from app.services import store
from app.domain.stages import STAGES, Stage
from app.services import pipeline, submissions
from app.services.datasource import get_source

PROJECT_ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api")


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
    return [_annotate(c, steps, verdicts) for c in get_source().list_claims() + submissions.list_claims()]


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
    return pipeline.run_claim(claim, gate_ids)


@router.get("/claims/{claim_id}/run", response_model=RunResult | None)
def latest_run(claim_id: str) -> RunResult | None:
    return pipeline.latest_run(claim_id)


@router.get("/claims/{claim_id}/files/{index}")
def claim_file(claim_id: str, index: int) -> FileResponse:
    """Stream one of the claim's staged source files inline (for the viewer)."""
    claim = _find_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Unknown claim {claim_id}")
    if index < 0 or index >= len(claim.source_files):
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} has no file #{index}")
    path = (PROJECT_ROOT / claim.source_files[index].path).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        content_disposition_type="inline",
        filename=path.name,
    )


@router.post("/extract/invoice", response_model=InvoiceDoc | None)
def extract_invoice(invoice: UploadFile = File(...)) -> InvoiceDoc | None:
    """Read one invoice on its own — powers the intake form's autofill.

    Null means "no reader available" (mock engine) and the form stays manual.
    OCR is disk-cached by content hash, so the later full-claim run re-reads
    this same file for free.
    """
    if get_settings().extractor_engine != "azure":
        return None
    from app.services.extraction.cu_client import analyze_layout
    from app.services.extraction.structuring import structure_documents

    staged = submissions.stage_file(f"_prefill/{uuid.uuid4().hex[:8]}", invoice, "invoice")
    path = PROJECT_ROOT / staged.path
    result = analyze_layout(path)
    if not result.ok:
        raise HTTPException(status_code=422, detail=f"OCR failed: {result.error}")
    docs = structure_documents([(path.name, "invoice", result.markdown)])
    return docs.invoice


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
    # Document files, one slot per doc_type the extractor understands.
    invoice: UploadFile | None = File(None),
    contract_boq: UploadFile | None = File(None),
    coc: UploadFile | None = File(None),
    delivery_note: UploadFile | None = File(None),
    other: list[UploadFile] = File(default=[]),
) -> Claim:
    claim_id = submissions.next_claim_id()

    source_files = [
        submissions.stage_file(claim_id, upload, doc_type)
        for upload, doc_type in (
            (invoice, "invoice"),
            (contract_boq, "contract_boq"),
            (coc, "coc"),
            (delivery_note, "delivery_note"),
        )
        if upload is not None and upload.filename
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
    # Documents attached at their gate's step; re-uploading replaces the slot.
    contract_boq: UploadFile | None = File(None),
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

    for upload, doc_type in ((contract_boq, "contract_boq"), (coc, "coc"), (delivery_note, "delivery_note")):
        if upload is not None and upload.filename:
            staged = submissions.stage_file(claim_id, upload, doc_type)
            claim.source_files = [f for f in claim.source_files if f.doc_type != doc_type] + [staged]
    claim.source_files += [
        submissions.stage_file(claim_id, upload, "other") for upload in other if upload.filename
    ]

    updates = {
        "po_no": po_no, "project_no": project_no, "project_name_ar": project_name_ar,
        "project_name_en": project_name_en, "vendor_account": vendor_account,
        "vendor_name_ar": vendor_name_ar, "vendor_name_en": vendor_name_en,
        "contract_value": contract_value, "claim_amount_base": claim_amount_base,
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

    submissions.add(claim)
    return claim
