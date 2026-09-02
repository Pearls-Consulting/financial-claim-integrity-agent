"""Pre-finance attachment identification: which government/vendor-file
document is each upload, and what identity fields does it carry?

Two readers, one contract:
  * `gpt` engine — GPT vision reads the first pages of every upload directly
    (identity documents carry everything on page 1-2), all uploads in
    parallel, each result cached by file content; classify + lift the
    printed fields + cite the page in ONE call per file.
  * `azure` engine — CU OCR reads, GPT organizes the markdown (the original
    path, kept as fallback).
A deterministic filename heuristic backs both up so a demo upload is never
left unclassified — a wrong-but-visible detection beats a silent "other".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.core.config import get_settings
from app.domain.models import DetectedAttachment

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "gpt_attachments"

logger = logging.getLogger(__name__)

# key -> (label_en, label_ar, filename hints). Keys MUST match the prefinance
# rulepack's `required` list — detection feeds attachments_complete directly.
ATTACHMENT_TYPES: dict[str, tuple[str, str, list[str]]] = {
    "contract": ("Contract", "العقد", ["contract", "عقد"]),
    "boq": ("Bill of Quantities", "جدول الكميات", ["boq", "quantities", "كميات"]),
    "award letter": ("Award letter", "خطاب الترسية", ["award", "ترسية", "trseya"]),
    "work commencement": ("Work commencement minutes", "محضر البدء بالأعمال", ["commencement", "بدء", "مباشرة"]),
    "commercial registration": ("Commercial registration", "السجل التجاري", ["commercial", "سجل", "registration"]),
    "zakat certificate": ("Zakat certificate", "شهادة الزكاة", ["zakat", "زكاة", "زكاه"]),
    "gosi certificate": ("GOSI certificate", "شهادة التأمينات الاجتماعية", ["gosi", "تأمينات", "insurance"]),
}

FIELD_KEYS = ["vendor_name_ar", "cr_number", "vat_number", "reference_no", "issue_date", "expiry_date"]

# Formats are NOT stable: Saudi certificates are redesigned almost every
# year, and vendors file decades-old ones alongside current ones — so both
# prompts teach identification by issuer + content, never by layout.
_GUIDANCE = """Guidance — identify by ISSUER and CONTENT, never by layout or era: these
documents are redesigned almost every year, and vendors file decades-old
certificates, blurry scans, and photographed wall certificates. An obsolete
or unclear format is still the document it says it is.
- commercial registration = ANY Ministry of Commerce (وزارة التجارة) company
  or establishment registration certificate, whatever its title: السجل
  التجاري, شهادة السجل التجاري, شهادة تسجيل شركة مساهمة / شركة ذات مسؤولية
  محدودة / مؤسسة فردية, Company Registration Certificate. It carries a
  10-digit CR number (رقم السجل التجاري / رقم المنشأة).
  NOT a chamber of commerce (الغرفة التجارية) membership, a VAT registration,
  or a municipal licence — another certificate printing a CR number does not
  become the CR.
- العقد = contract; جدول الكميات = boq; خطاب الترسية / إشعار الترسية = award
  letter; محضر البدء بالأعمال / محضر تسليم الموقع = work commencement; شهادة
  الزكاة والدخل = zakat certificate; شهادة التأمينات الاجتماعية (GOSI) = gosi
  certificate."""

_SYSTEM = f"""\
You are identifying already-OCR'd Saudi vendor-file documents attached to a
payment claim. The input is MARKDOWN, one section per file, annotated like
[[FILE <name>]]. Treat the characters as GROUND TRUTH: never change, correct,
or re-guess any digit, date, name, or number. Organize only.

For EVERY file, decide which document it is and lift the identity fields it
prints. doc_key must be exactly one of:
{json.dumps(list(ATTACHMENT_TYPES), ensure_ascii=False)}
or "other" if none applies.

{_GUIDANCE}

OUTPUT — return ONLY this JSON (no fences, no commentary), input-file order:
[{{"file_name": "", "doc_key": "", "fields": {{
  "vendor_name_ar": "", "cr_number": "", "vat_number": "",
  "reference_no": "", "issue_date": "", "expiry_date": ""}}}}]

RULES
- A file that bundles SEVERAL certificates (zakat + CR + GOSI + ...) appears
  once PER document found — same file_name repeated, one entry each.
- fields: only what THAT document PRINTS; use "" for anything absent.
- reference_no = the document's own number (award letter no., certificate
  no., minutes no. ...). Dates as printed (YYYY-MM-DD if derivable).
- Every input file must appear at least once; a file matching nothing
  appears once with doc_key "other".
"""


def heuristic_doc_key(file_name: str) -> str:
    """Filename-based fallback classification — deterministic, never fails."""
    name = file_name.lower()
    # Vendors most often name the CR by its bare initialism — "CR Safari
    # 1010034600.pdf", "Att 1_ILF - CR - Valid until 08.06.2025.pdf" — and
    # their trade descriptor ("... Contracting ...") collides with the
    # contract hint, so the whole-token CR is judged first. A token, not a
    # substring: "crystal" or "concrete" must never trip it.
    if re.search(r"(?<![a-z0-9])c\.?r(?![a-z0-9])", name):
        return "commercial registration"
    for key, (_, _, hints) in ATTACHMENT_TYPES.items():
        if any(h in name for h in hints):
            return key
    return "other"


def _parse_json_list(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array in model output")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("model output is not a list")
    return data


def _fallback(file_names: list[str]) -> list[DetectedAttachment]:
    return [DetectedAttachment(file_name=n, doc_key=heuristic_doc_key(n)) for n in file_names]


def witness_identity_fields(fields: dict[str, str], raw_text: str) -> dict[str, str]:
    """The invoice reader's text-layer witness, applied to the identity
    fields: a born-digital certificate carries its digits EXACTLY in the PDF
    text layer, while the vision read can transpose or drop one. When a read
    identifier is absent from a usable text layer and exactly ONE token sits
    a single slip away, the printed token replaces the read — silently: the
    reader getting the digits right, not a finding (see text_layer.py). A
    falsely attributed CR number is worse than a missing one."""
    from app.services.extraction.text_layer import contains_identifier, near_identifier, normalize, usable

    if not raw_text or not usable(raw_text):
        return fields
    norm = normalize(raw_text)
    out = dict(fields)
    for key in ("cr_number", "vat_number", "reference_no"):
        value = out.get(key) or ""
        if not value or contains_identifier(norm, value):
            continue
        alt = near_identifier(norm, value)
        if alt:
            logger.info("attachment witness: %s %r -> %r (text layer)", key, value, alt)
            out[key] = alt
    return out



_VISION_SYSTEM = f"""\
You are identifying the Saudi vendor-file document(s) inside ONE uploaded
attachment, reading its pages visually. Vendors often scan SEVERAL
certificates into a single bundle PDF — find them all. Never change, correct, or re-guess any
digit, date, name, or number — report what is printed, Arabic-Indic digits
٠١٢٣٤٥٦٧٨٩ read one by one.

Decide which document it is. doc_key must be exactly one of:
{json.dumps(list(ATTACHMENT_TYPES), ensure_ascii=False)}
or "other" if none applies.

{_GUIDANCE}

OUTPUT — return ONLY this JSON (no fences, no commentary):
{{"documents": [{{"doc_key": "", "page": 1, "fields": {{
  "vendor_name_ar": "", "cr_number": "", "vat_number": "",
  "reference_no": "", "issue_date": "", "expiry_date": ""}}}}]}}

RULES
- ONE entry PER DOCUMENT found: a bundle scan (zakat + CR + GOSI + ...) gets
  one entry for each, each with its own page and fields. A single-document
  file gets exactly one entry.
- Pages whose document matches NO listed doc_key (IBAN letter, chamber of
  commerce membership, saudization certificate ...) get no entry — but if
  NOTHING in the file matches, return one {{"doc_key": "other"}} entry.
- fields: only what THAT document PRINTS; use "" for anything absent.
- reference_no = the document's own number (award letter no., certificate
  no., minutes no. ...). Dates as printed (YYYY-MM-DD if derivable).
- page = the 1-based page (within the pages given) where that document's
  identity fields / title are printed.
"""


def entries_from_raw(raw: Any, file_name: str) -> list[DetectedAttachment]:
    """Model output -> detections. Accepts the documents-array shape and the
    legacy single-object shape. Only known doc_keys survive, one entry per
    key (the first — lowest page — wins): a bundle yields several detections,
    a single certificate yields one, junk yields none."""
    docs = raw.get("documents") if isinstance(raw, dict) else None
    if not isinstance(docs, list):
        docs = [raw] if isinstance(raw, dict) else []
    out: list[DetectedAttachment] = []
    seen: set[str] = set()
    for d in docs:
        if not isinstance(d, dict):
            continue
        key = str(d.get("doc_key") or "")
        if key not in ATTACHMENT_TYPES or key in seen:
            continue
        fields = {k: str(v) for k, v in (d.get("fields") or {}).items() if k in FIELD_KEYS and v}
        try:
            page = int(d.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        out.append(DetectedAttachment(file_name=file_name, doc_key=key, fields=fields, page=max(page, 0)))
        seen.add(key)
    return out


def _identify_vision(path: Path, client: Any) -> list[DetectedAttachment]:
    """GPT vision read of ONE upload (first pages only), cached by content.
    Returns every document identified in the file — one for a plain
    certificate, several for a bundle scan."""
    from app.services.extraction.gpt_vision import build_units, call_json, unit_blocks, vision_model

    s = get_settings()
    data = path.read_bytes()
    stamp = f"{_VISION_SYSTEM}\x00{vision_model()}\x00{s.gpt_vision_attachment_max_pages}\x00img{s.gpt_vision_render_dpi}\x00w2"
    cache = _CACHE_DIR / f"{hashlib.sha256(data + stamp.encode('utf-8')).hexdigest()}.json"
    if s.extraction_cache and cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            return [DetectedAttachment.model_validate(e) for e in (cached if isinstance(cached, list) else [cached])]
        except Exception:
            pass
    units = build_units(path, "attachment", chunk_pages=max(1, s.gpt_vision_attachment_max_pages), max_pages=s.gpt_vision_attachment_max_pages)
    unit = units[0]  # identity documents & bundles: the first pages carry everything
    prompt_tail = {"type": "input_text", "text": f"File: {path.name}\nReturn ONLY the JSON object."}
    raw = call_json(
        client,
        system=_VISION_SYSTEM,
        content=[*unit_blocks(unit), prompt_tail],
        model=vision_model(),
        effort=s.gpt_vision_effort,
        max_tokens=4000,
    )
    dets = entries_from_raw(raw, path.name)
    if not dets:
        # An unclear scan or an obsolete template often reads as "other" at
        # low effort. One second look, thinking harder, before conceding.
        try:
            retry = call_json(
                client,
                system=_VISION_SYSTEM,
                content=[*unit_blocks(unit), prompt_tail],
                model=vision_model(),
                effort="high",
                max_tokens=4000,
            )
            dets = entries_from_raw(retry, path.name)
        except Exception:
            logger.debug("high-effort second look failed for %s", path.name, exc_info=True)
    if not dets:  # still unknown — let the filename try
        dets = [DetectedAttachment(file_name=path.name, doc_key=heuristic_doc_key(path.name))]
    try:
        from app.services.extraction.text_layer import unit_text

        text = unit_text(unit)
        for det in dets:
            det.fields = witness_identity_fields(det.fields, text)
    except Exception:
        logger.debug("text-layer witness unavailable for %s", path.name, exc_info=True)
    if s.extraction_cache:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps([d.model_dump() for d in dets], ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return dets


def classify_attachments_vision(paths: list[Path], display_names: list[str] | None = None) -> list[DetectedAttachment]:
    """`gpt` engine: identify every upload with one parallel vision read each.
    A file whose read fails still comes back, classified by its filename."""
    from app.services.extraction.gpt_vision import make_client

    names = display_names or [p.name for p in paths]
    if not paths:
        return []
    try:
        client = make_client()
    except Exception:
        logger.exception("GPT reader not configured — falling back to filename heuristics")
        return _fallback(names)

    def _one(item: tuple[Path, str]) -> list[DetectedAttachment]:
        path, name = item
        try:
            dets = _identify_vision(path, client)
            for det in dets:
                det.file_name = name
            return dets
        except Exception:
            logger.exception("attachment identification failed for %s — filename heuristic", name)
            return [DetectedAttachment(file_name=name, doc_key=heuristic_doc_key(name))]

    s = get_settings()
    with ThreadPoolExecutor(max_workers=max(1, min(s.gpt_vision_concurrency, len(paths)))) as pool:
        return [det for dets in pool.map(_one, list(zip(paths, names))) for det in dets]


def classify_attachments(markdown_by_file: list[tuple[str, str]]) -> list[DetectedAttachment]:
    """markdown_by_file: (file_name, markdown) per OCR'd upload. Files whose
    OCR failed should be passed with markdown '' — they still get a heuristic
    classification so the reviewer sees every upload accounted for."""
    names = [n for n, _ in markdown_by_file]
    readable = [(n, md) for n, md in markdown_by_file if md]
    if not readable:
        return _fallback(names)

    s = get_settings()
    client = OpenAI(api_key=s.azure_openai_api_key, base_url=s.azure_openai_base_url, timeout=s.gpt_judge_timeout_seconds, max_retries=1)
    corpus = "\n\n".join(f"[[FILE {n}]]\n{md}" for n, md in readable)
    try:
        response = client.chat.completions.create(
            model=s.azure_openai_model,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": corpus}],
        )
        known: dict[str, dict[str, DetectedAttachment]] = {}  # name -> doc_key -> det
        other: dict[str, DetectedAttachment] = {}
        for entry in _parse_json_list(response.choices[0].message.content or ""):
            fields = {k: str(v) for k, v in (entry.get("fields") or {}).items() if k in FIELD_KEYS and v}
            key = entry.get("doc_key", "other")
            name = str(entry.get("file_name", ""))
            if key not in ATTACHMENT_TYPES:
                key = heuristic_doc_key(name)  # model said "other"/unknown — let the filename try
            det = DetectedAttachment(file_name=name, doc_key=key, fields=fields)
            if key in ATTACHMENT_TYPES:
                known.setdefault(name, {}).setdefault(key, det)  # a bundle: several entries per file
            else:
                other.setdefault(name, det)
        # Every upload comes back classified, GPT-first, heuristic otherwise;
        # a bundle file contributes one detection per document found in it.
        out: list[DetectedAttachment] = []
        for n in names:
            dets = list(known.get(n, {}).values())
            out.extend(dets or [other.get(n) or DetectedAttachment(file_name=n, doc_key=heuristic_doc_key(n))])
        return out
    except Exception:
        logger.exception("attachment classification failed — falling back to filename heuristics")
        return _fallback(names)
