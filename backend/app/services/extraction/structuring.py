"""Stage 2: GPT structures CU markdown into the ClaimDocuments schema.

GPT reads the OCR markdown as TEXT (never an image) and only ORGANIZES it —
the prompt forbids re-guessing digits/dates, the pattern proven in the
prequalification agent. Output is validated by pydantic; one repair retry on
schema mismatch. QR payloads are NOT taken from the model — the deterministic
decoder (extraction/qr.py) injects them afterwards.
"""

from __future__ import annotations

import json
import re

from openai import OpenAI

from app.core.config import get_settings
from app.domain.models import ClaimDocuments

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
  "boq": [{"item_code": "", "description_ar": "", "description_en": "", "unit": "", "unit_price": 0.0, "quantity": 0.0}]
}

RULES
- "boq" comes from the purchase order / contract line items (item number, unit
  price, quantity). Use the PO's printed item numbers as item_code.
- Invoice lines: if the invoice prints its own line numbers but the lines
  clearly correspond to PO items by description, use the PO item numbers as
  item_code so the two sides align; keep description/prices exactly as printed
  on the INVOICE. If no correspondence is clear, keep the invoice's own codes.
- Monetary values: plain numbers (no thousands separators, no currency).
- Omit nothing that is printed; set null only for genuinely absent values.
- If no invoice / no COC file is present, set that key to null.
"""


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
        f"[[FILE {name} | type={hint}]]\n{md}" for name, hint, md in markdown_by_file
    )

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
            return ClaimDocuments.model_validate(data)
        except Exception as exc:
            last_error = str(exc)
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f"That output failed validation: {exc}. Return ONLY the corrected JSON."}
            )
    raise RuntimeError(f"GPT structuring failed schema validation twice: {last_error}")
