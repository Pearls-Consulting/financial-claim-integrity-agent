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
    "commercial registration": ("Commercial registration", "السجل التجاري", ["cr-", "cr_", "commercial", "سجل", "registration"]),
    "zakat certificate": ("Zakat certificate", "شهادة الزكاة", ["zakat", "زكاة", "زكاه"]),
    "gosi certificate": ("GOSI certificate", "شهادة التأمينات الاجتماعية", ["gosi", "تأمينات", "insurance"]),
}

FIELD_KEYS = ["vendor_name_ar", "cr_number", "vat_number", "reference_no", "issue_date", "expiry_date"]

_SYSTEM = f"""\
You are identifying already-OCR'd Saudi vendor-file documents attached to a
payment claim. The input is MARKDOWN, one section per file, annotated like
[[FILE <name>]]. Treat the characters as GROUND TRUTH: never change, correct,
or re-guess any digit, date, name, or number. Organize only.

For EVERY file, decide which document it is and lift the identity fields it
prints. doc_key must be exactly one of:
{json.dumps(list(ATTACHMENT_TYPES), ensure_ascii=False)}
or "other" if none applies. Guidance: العقد = contract; جدول الكميات = boq;
خطاب الترسية / إشعار الترسية = award letter; محضر البدء بالأعمال / محضر تسليم
الموقع = work commencement; السجل التجاري = commercial registration; شهادة
الزكاة والدخل = zakat certificate; شهادة التأمينات الاجتماعية (GOSI) = gosi
certificate.

OUTPUT — return ONLY this JSON (no fences, no commentary), one entry per
input file, same order:
[{{"file_name": "", "doc_key": "", "fields": {{
  "vendor_name_ar": "", "cr_number": "", "vat_number": "",
  "reference_no": "", "issue_date": "", "expiry_date": ""}}}}]

RULES
- fields: only what the document PRINTS; use "" for anything absent.
- reference_no = the document's own number (award letter no., certificate
  no., minutes no. ...). Dates as printed (YYYY-MM-DD if derivable).
- Every input file must appear exactly once in the output.
"""


def heuristic_doc_key(file_name: str) -> str:
    """Filename-based fallback classification — deterministic, never fails."""
    name = file_name.lower()
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


_VISION_SYSTEM = f"""\
You are identifying ONE Saudi vendor-file document attached to a payment
claim, reading its pages visually. Never change, correct, or re-guess any
digit, date, name, or number — report what is printed, Arabic-Indic digits
٠١٢٣٤٥٦٧٨٩ read one by one.

Decide which document it is. doc_key must be exactly one of:
{json.dumps(list(ATTACHMENT_TYPES), ensure_ascii=False)}
or "other" if none applies. Guidance: العقد = contract; جدول الكميات = boq;
خطاب الترسية / إشعار الترسية = award letter; محضر البدء بالأعمال / محضر تسليم
الموقع = work commencement; السجل التجاري = commercial registration; شهادة
الزكاة والدخل = zakat certificate; شهادة التأمينات الاجتماعية (GOSI) = gosi
certificate.

OUTPUT — return ONLY this JSON (no fences, no commentary):
{{"doc_key": "", "page": 1, "fields": {{
  "vendor_name_ar": "", "cr_number": "", "vat_number": "",
  "reference_no": "", "issue_date": "", "expiry_date": ""}}}}

RULES
- fields: only what the document PRINTS; use "" for anything absent.
- reference_no = the document's own number (award letter no., certificate
  no., minutes no. ...). Dates as printed (YYYY-MM-DD if derivable).
- page = the 1-based page (within the pages given) where the identity
  fields / document title are printed — usually 1.
"""


def _identify_vision(path: Path, client: Any) -> DetectedAttachment:
    """GPT vision read of ONE upload (first pages only), cached by content."""
    from app.services.extraction.gpt_vision import build_units, call_json, unit_blocks, vision_model

    s = get_settings()
    data = path.read_bytes()
    stamp = f"{_VISION_SYSTEM}\x00{vision_model()}\x00{s.gpt_vision_attachment_max_pages}\x00img{s.gpt_vision_render_dpi}"
    cache = _CACHE_DIR / f"{hashlib.sha256(data + stamp.encode('utf-8')).hexdigest()}.json"
    if s.extraction_cache and cache.exists():
        try:
            return DetectedAttachment.model_validate_json(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    units = build_units(path, "attachment", chunk_pages=max(1, s.gpt_vision_attachment_max_pages), max_pages=s.gpt_vision_attachment_max_pages)
    unit = units[0]  # identity documents: the first pages carry everything
    raw = call_json(
        client,
        system=_VISION_SYSTEM,
        content=[*unit_blocks(unit), {"type": "input_text", "text": f"File: {path.name}\nReturn ONLY the JSON object."}],
        model=vision_model(),
        effort=s.gpt_vision_effort,
        max_tokens=2000,
    )
    key = str(raw.get("doc_key") or "other")
    if key not in ATTACHMENT_TYPES:
        key = heuristic_doc_key(path.name)  # model said "other"/unknown — let the filename try
    fields = {k: str(v) for k, v in (raw.get("fields") or {}).items() if k in FIELD_KEYS and v}
    try:
        page = int(raw.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    det = DetectedAttachment(file_name=path.name, doc_key=key, fields=fields, page=max(page, 0))
    if s.extraction_cache:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(det.model_dump_json(), encoding="utf-8")
        except OSError:
            pass
    return det


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

    def _one(item: tuple[Path, str]) -> DetectedAttachment:
        path, name = item
        try:
            det = _identify_vision(path, client)
            det.file_name = name
            return det
        except Exception:
            logger.exception("attachment identification failed for %s — filename heuristic", name)
            return DetectedAttachment(file_name=name, doc_key=heuristic_doc_key(name))

    s = get_settings()
    with ThreadPoolExecutor(max_workers=max(1, min(s.gpt_vision_concurrency, len(paths)))) as pool:
        return list(pool.map(_one, list(zip(paths, names))))


def classify_attachments(markdown_by_file: list[tuple[str, str]]) -> list[DetectedAttachment]:
    """markdown_by_file: (file_name, markdown) per OCR'd upload. Files whose
    OCR failed should be passed with markdown '' — they still get a heuristic
    classification so the reviewer sees every upload accounted for."""
    names = [n for n, _ in markdown_by_file]
    readable = [(n, md) for n, md in markdown_by_file if md]
    if not readable:
        return _fallback(names)

    s = get_settings()
    client = OpenAI(api_key=s.azure_openai_api_key, base_url=s.azure_openai_base_url)
    corpus = "\n\n".join(f"[[FILE {n}]]\n{md}" for n, md in readable)
    try:
        response = client.chat.completions.create(
            model=s.azure_openai_model,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": corpus}],
        )
        by_name: dict[str, DetectedAttachment] = {}
        for entry in _parse_json_list(response.choices[0].message.content or ""):
            fields = {k: str(v) for k, v in (entry.get("fields") or {}).items() if k in FIELD_KEYS and v}
            key = entry.get("doc_key", "other")
            name = str(entry.get("file_name", ""))
            if key not in ATTACHMENT_TYPES:
                key = heuristic_doc_key(name)  # model said "other"/unknown — let the filename try
            by_name[name] = DetectedAttachment(file_name=name, doc_key=key, fields=fields)
        # Every upload comes back classified, GPT-first, heuristic otherwise.
        return [by_name.get(n) or DetectedAttachment(file_name=n, doc_key=heuristic_doc_key(n)) for n in names]
    except Exception:
        logger.exception("attachment classification failed — falling back to filename heuristics")
        return _fallback(names)
