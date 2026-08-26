"""Pre-finance attachment identification: which government/vendor-file
document is each upload, and what identity fields does it carry?

Same division of labor as the rest of the pipeline: CU OCR reads, GPT only
ORGANIZES the markdown (classify + lift printed fields, never invent), and a
deterministic filename heuristic backs the model up so a demo upload is never
left unclassified — a wrong-but-visible detection beats a silent "other".
"""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from app.core.config import get_settings
from app.domain.models import DetectedAttachment

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
