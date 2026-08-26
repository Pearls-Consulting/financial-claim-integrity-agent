"""Stage 2: GPT structures CU markdown into the ClaimDocuments schema.

GPT reads the OCR markdown as TEXT (never an image) and only ORGANIZES it —
the prompt forbids re-guessing digits/dates, the pattern proven in the
prequalification agent. Output is validated by pydantic; one repair retry on
schema mismatch. QR payloads are NOT taken from the model — the deterministic
decoder (extraction/qr.py) injects them afterwards.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from openai import OpenAI

from app.core.config import get_settings
from app.domain.models import ClaimDocuments

# Same-documents re-runs skip the model entirely: results are cached on disk
# keyed by (prompt, model, corpus) — the same pattern as cu_client.
_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "structuring"

_SYSTEM = """\
You are organizing already-OCR'd Saudi procurement documents into a fixed JSON
schema. The input is MARKDOWN produced by a layout engine, one section per file,
annotated like [[FILE <name> | type=<hint>]]. Treat the characters as GROUND
TRUTH: never change, correct, or re-guess any digit, date, name, or number.
Organize only. If a value is absent, use null/empty — never invent.

The files belong to ONE vendor payment claim and may include: the vendor's tax
invoice (فاتورة ضريبية), a purchase order / contract with a bill of quantities
or line items, a delivery note, a certificate of completion (محضر الإنجاز).

OUTPUT — return ONLY this JSON (no markdown fences, no commentary):
{
  "invoice": {
    "invoice_no": "", "invoice_date": "YYYY-MM-DD if derivable from the printed date, else as printed",
    "seller_name_ar": "", "seller_vat_number": "",
    "total_with_vat": 0.0, "vat_amount": 0.0, "vat_exempt": false,
    "lines": [{"item_code": "", "description_ar": "", "unit_price": 0.0, "quantity": 0.0, "amount": 0.0}]
  } | null,
  "coc": {
    "coc_no": "", "coc_date": "", "claim_amount": 0.0,
    "has_delay": true|false|null, "has_stoppage": true|false|null,
    "has_observations": true|false|null, "delay_days": 0
  } | null,
  "boq": [{"item_code": "", "description_ar": "", "description_en": "", "unit": "", "unit_price": 0.0, "quantity": 0.0}],
  "contract": {
    "contract_no": "", "start_date": "YYYY-MM-DD if derivable, else as printed",
    "end_date": "YYYY-MM-DD if derivable, else as printed", "value_base": 0.0,
    "penalty_terms": [{
      "kind": "delay", "rate_percent": 0.0, "per": "day|week|",
      "basis": "", "cap_percent": 0.0, "text_ar": "", "ref": "", "page": 0
    }]
  } | null,
  "receipt": {
    "receipt_no": "", "receipt_date": "",
    "lines": [{"item_code": "", "description_ar": "", "quantity": 0.0}]
  } | null
}

RULES
- "boq" comes from the purchase order / contract line items (item number, unit
  price, quantity). Use the PO's printed item numbers as item_code.
- "contract" is the contract/PO HEADER: its number, start date (contract date
  or site handover), END date (contract duration end / delivery deadline —
  phrases like مدة العقد حتى, تاريخ نهاية العقد, موعد التسليم) and the
  pre-VAT value. null if no contract/PO document is present.
- end_date from a DURATION: when the contract states a duration instead of an
  end date (مدة العقد خمسة أشهر من تاريخ محضر بدء المشروع, "12 months from
  signing"), derive end_date = anchor date + duration (YYYY-MM-DD) ONLY when
  the duration's OWN anchor event is explicitly dated in the documents: a
  duration running from محضر بدء المشروع / تسليم الموقع needs that
  commencement/handover date itself (from the محضر, or another document that
  prints it, e.g. the COC); "from signing" needs the signing date. NEVER
  anchor a duration to the contract's issue/offer/print/version dates or any
  other incidental date — a wrongly derived end date fabricates delay
  downstream. When the anchor date is absent from the documents, leave
  end_date as the printed duration phrase, verbatim.
- "penalty_terms" are the contract's PENALTY CLAUSES (الغرامات / غرامات
  التأخير / liquidated damages / delay damages), NOT penalties imposed on the
  vendor. For each clause: kind "delay" for late-execution penalties, else
  "other"; rate_percent = the printed percentage (e.g. 10.0 for "(10%)" /
  "١٠٪"); per = "day"/"week" when the rate is per unit of delay time (عن كل
  يوم تأخير / لكل أسبوع), "" when it is a flat or maximum rate; basis = what
  the percentage applies to, verbatim (e.g. "قيمة البند حسب جدول الكميات",
  "القيمة الإجمالية للعقد"); cap_percent = the overall ceiling percentage if
  one is printed (لا يتجاوز إجمالي الغرامات ٢٠٪); text_ar = the clause
  sentence EXACTLY as printed (it anchors the evidence viewer); ref = the
  article/clause number as printed; page = the [[PAGE n]] marker number the
  clause sits under (0 if the file carries no markers). Empty list when no
  penalty clause is present. Never invent rates.
- Invoice lines: if the invoice prints its own line numbers but the lines
  clearly correspond to PO items by description, use the PO item numbers as
  item_code so the two sides align; keep description/prices exactly as printed
  on the INVOICE. If no correspondence is clear, keep the invoice's own codes.
- "receipt" comes from a delivery note / receiving record (إشعار تسليم /
  محضر استلام / إيصال استلام), if one is present: its number, date, and the
  RECEIVED quantity per line. Align item_code with the PO/invoice item numbers
  the same way as invoice lines. If no such document is present, set null.
- Monetary values: plain numbers (no thousands separators, no currency).
- Omit nothing that is printed; set null only for genuinely absent values.
- If no invoice / no COC file is present, set that key to null.
"""


_PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->")


def annotate_pages(markdown: str) -> str:
    """Replace CU's PageBreak comments with explicit [[PAGE n]] markers so the
    model can report WHICH page a clause sits on (the prequalification agent's
    proven provenance pattern). Page 1 is implicit; the first break opens
    page 2, and so on."""
    page = 1

    def bump(_: re.Match) -> str:
        nonlocal page
        page += 1
        return f"[[PAGE {page}]]"

    return f"[[PAGE 1]]\n{_PAGE_BREAK.sub(bump, markdown)}"


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def structure_documents(markdown_by_file: list[tuple[str, str, str]]) -> ClaimDocuments:
    """markdown_by_file: (file_name, doc_type_hint, markdown) per successfully OCR'd file."""
    s = get_settings()
    client = OpenAI(api_key=s.azure_openai_api_key, base_url=s.azure_openai_base_url)
    corpus = "\n\n".join(
        f"[[FILE {name} | type={hint}]]\n{annotate_pages(md)}" for name, hint, md in markdown_by_file
    )

    digest = hashlib.sha256(f"{_SYSTEM}\x00{s.azure_openai_model}\x00{corpus}".encode()).hexdigest()
    cache_file = _CACHE_DIR / f"{digest}.json"
    if cache_file.exists():
        try:
            return ClaimDocuments.model_validate_json(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass  # stale schema / corrupt file — fall through and re-extract

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": corpus},
    ]
    last_error = ""
    for _ in range(2):  # one repair retry on schema mismatch
        response = client.chat.completions.create(model=s.azure_openai_model, messages=messages)
        raw = response.choices[0].message.content or ""
        try:
            data = _parse_json(raw)
            data.pop("penalties", None)  # ERP-owned, never extracted
            data.pop("attachments", None)
            data.pop("detected_attachments", None)  # classified at upload time
            docs = ClaimDocuments.model_validate(data)
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(docs.model_dump_json(), encoding="utf-8")
            except OSError:
                pass
            return docs
        except Exception as exc:
            last_error = str(exc)
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f"That output failed validation: {exc}. Return ONLY the corrected JSON."}
            )
    raise RuntimeError(f"GPT structuring failed schema validation twice: {last_error}")
