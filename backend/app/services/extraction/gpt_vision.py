"""GPT vision reader — stage 1 of the `gpt` extractor engine.

The model reads each file's PAGES as images (rendered here with PDFium, sent
on the Azure OpenAI Responses API), the shape proven by the prequalification
agent's analyzer v4:

  • one call per file — big PDFs are split into page chunks, each chunk its
    own call, chunk-relative page numbers rebased onto the file afterwards;
  • every file and chunk of a claim is in flight at once (one thread pool);
  • LOW reasoning effort — reading, not reasoning;
  • PAGE PROVENANCE on every value, so the evidence viewer lands on the exact
    page and the CU fallback OCRs that one page only (never the document).

Azure CU is NOT part of this path. It is kept solely for the viewer's
highlight fallback on scanned pages (extraction/locate.py) — the only place
that still needs word polygons.

Results are cached on disk per (file content, prompt, model), so the step-1/2
prefill reads and every cumulative gate run re-use the same read for free.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domain.models import ClaimDocuments
from app.services.extraction.pdfium_lock import PDFIUM_LOCK
from app.services.extraction.retry import with_retries

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "gpt_vision"

# Image types the Responses API takes as `input_image` directly; anything else
# raster (tif/bmp) is transcoded to PNG first.
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
_RASTER_EXTS = set(_IMAGE_MIME) | {".tif", ".tiff", ".bmp"}

_MAX_OUTPUT_CEILING = 64000

READ_SYSTEM = """\
You are reading ONE file (or one page-range chunk of it) belonging to a Saudi
vendor payment claim, and organizing what it PRINTS into a fixed JSON schema.
Read the pages visually and carefully: Arabic-Indic digits ٠١٢٣٤٥٦٧٨٩ one by
one, tables row by row, every page. Never invent, correct, or round a value;
whatever is absent is null / "" / [] / 0.

The file may be: the vendor's tax invoice (فاتورة ضريبية); a purchase order or
contract, possibly carrying a bill of quantities (جدول الكميات) or line items;
a delivery note / receiving record (إشعار تسليم / محضر استلام); a certificate
of completion (محضر الإنجاز). One file may hold several of these (a contract
with its BoQ). Fill every section the pages actually contain; leave the rest
null / empty. The caller's type hint says what the file is EXPECTED to be —
report what you actually see.

PAGE PROVENANCE — every "page" is the 1-based page number, counted within THE
PAGES YOU WERE GIVEN, where that value is printed: the header page for a
header object, the row's own page for a line, the clause's page for a penalty
term. These numbers drive the reviewer's evidence viewer, so be exact. Use 0
only if genuinely unknown.

OUTPUT — return ONLY this JSON (no markdown fences, no commentary):
{
  "invoice": {
    "invoice_no": "", "invoice_date": "YYYY-MM-DD if derivable from the printed date, else as printed",
    "seller_name_ar": "", "seller_vat_number": "",
    "total_with_vat": 0.0, "vat_amount": 0.0, "vat_exempt": false, "page": 0,
    "lines": [{"item_code": "", "description_ar": "", "unit_price": 0.0, "quantity": 0.0, "amount": 0.0, "page": 0}]
  } | null,
  "coc": {
    "coc_no": "", "coc_date": "", "claim_amount": 0.0,
    "has_delay": true|false|null, "has_stoppage": true|false|null,
    "has_observations": true|false|null, "delay_days": 0, "page": 0
  } | null,
  "boq": [{"item_code": "", "description_ar": "", "description_en": "", "unit": "", "unit_price": 0.0, "quantity": 0.0, "page": 0}],
  "contract": {
    "contract_no": "", "start_date": "YYYY-MM-DD if derivable, else as printed",
    "end_date": "YYYY-MM-DD if derivable, else as printed", "value_base": 0.0, "page": 0,
    "penalty_terms": [{
      "kind": "delay", "rate_percent": 0.0, "per": "day|week|",
      "basis": "", "cap_percent": 0.0, "text_ar": "", "ref": "", "page": 0
    }]
  } | null,
  "receipt": {
    "receipt_no": "", "receipt_date": "", "page": 0,
    "lines": [{"item_code": "", "description_ar": "", "quantity": 0.0, "page": 0}]
  } | null,
  "anchors": {"commencement_date": "", "site_handover_date": "", "signing_date": ""}
}

RULES
- "boq" = the purchase order / contract line items (item number, description,
  unit, unit price, quantity), EVERY row on every page, in printed order. Use
  the printed item numbers as item_code. Amounts are plain numbers.
- item_code (BoQ, invoice, receipt lines) is ALWAYS a JSON STRING copied
  exactly as printed — "6.10" keeps its trailing zero ("6.10" and "6.1" are
  different items), "OF-101" keeps its letters. Never emit it as a number.
  Do not re-emit a table's recap/summary pages as line items.
- "contract" = the contract/PO HEADER: its number, start date (contract date
  or site handover), END date (contract duration end / delivery deadline —
  phrases like مدة العقد حتى, تاريخ نهاية العقد, موعد التسليم) and the
  pre-VAT value. null when no contract/PO is present in these pages.
- end_date from a DURATION: when the contract states a duration instead of an
  end date (مدة العقد خمسة أشهر من تاريخ محضر بدء المشروع, "12 months from
  signing"), derive end_date = anchor date + duration (YYYY-MM-DD) ONLY when
  the duration's OWN anchor event is explicitly dated IN THESE PAGES (a
  duration from محضر بدء المشروع / تسليم الموقع needs that commencement /
  handover date; "from signing" needs the signing date). NEVER anchor a
  duration to the contract's issue/offer/print/version dates or any other
  incidental date — a wrongly derived end date fabricates delay downstream.
  Otherwise leave end_date as the printed duration phrase, verbatim.
- "anchors": dated events printed in THESE pages that a duration might run
  from — the work-commencement / site-handover date (محضر بدء المشروع, تسليم
  الموقع) and the signing date. YYYY-MM-DD if derivable, else as printed; ""
  when not printed. Report them even when you could not derive end_date.
- "penalty_terms" = the contract's PENALTY CLAUSES (الغرامات / غرامات
  التأخير / liquidated damages), NOT penalties imposed on the vendor. Per
  clause: kind "delay" for late-execution penalties, else "other";
  rate_percent = the printed percentage (10.0 for "(10%)" / "١٠٪"); per =
  "day"/"week" when the rate is per unit of delay time (عن كل يوم تأخير / لكل
  أسبوع), "" for a flat or maximum rate; basis = what the percentage applies
  to, verbatim; cap_percent = the overall ceiling if printed (لا يتجاوز
  إجمالي الغرامات ٢٠٪); text_ar = the clause sentence EXACTLY as printed (it
  anchors the evidence viewer); ref = the article/clause number as printed.
  Empty list when no penalty clause is present. Never invent rates.
- Invoice lines: item_code, description, unit price, quantity and amount
  exactly as printed on the INVOICE (its own line numbering; alignment with
  the PO happens later).
- "receipt" = a delivery note / receiving record, if present: its number,
  date, and the RECEIVED quantity per line with the printed item numbers.
- "coc" = محضر الإنجاز: number, date, claim_amount = the claim TOTAL
  INCLUDING VAT as certified (إجمالي المطالبة / شامل الضريبة) — NOT the
  pre-VAT current-claim value and NOT the VAT line — and the three yes/no
  declarations (delay? stoppage & resumption? observations?) as printed —
  null when not ticked/printed; delay_days as printed, else 0. A COC that
  quotes the contract value is still not a contract: leave "contract" null.
- Monetary values: plain numbers (no thousands separators, no currency).
- If no invoice / COC / contract / receipt is present in these pages, set
  that key to null. Omit nothing that is printed.
"""

# Coerced/validated after every read: the per-chunk output is exactly a
# ClaimDocuments (page fields included) plus the `anchors` block.
_ANCHOR_KEYS = ("commencement_date", "site_handover_date", "signing_date")

# ── key-field verify ─────────────────────────────────────────────────────────
#
# The schema-wide read above emits a long JSON and, while doing so, transposes
# digits inside long identifiers: measured on a crisp digital invoice, the
# 15-digit VAT number came back "301…" instead of "310…" in ~30% of reads at
# both 200 and 300 dpi — while a small, focused prompt on the same page image
# read it correctly 24/24. The prequalification agent's v4 verify is the same
# cure: re-read the header page for the few decision-critical values with a
# focused prompt, and let that value win. One short extra call per short
# document, issued concurrently with the main read.

KEY_FIELDS_SYSTEM = """\
You are reading the HEADER of one Saudi procurement document from its page
image(s), to confirm a few decision-critical values with maximum care. Copy
each value EXACTLY as printed — read every digit one by one (Arabic-Indic
٠١٢٣٤٥٦٧٨٩ = 0-9), never guess, never reorder. If a value is not printed,
use null. Monetary values as plain numbers.

Return ONLY this JSON (no fences, no commentary):
{
  "invoice": {"invoice_no": "", "invoice_date": "YYYY-MM-DD if derivable, else as printed",
              "seller_vat_number": "", "total_with_vat": 0.0, "vat_amount": 0.0} | null,
  "coc": {"coc_no": "", "coc_date": "", "claim_amount": 0.0, "delay_days": 0} | null,
  "receipt": {"receipt_no": "", "receipt_date": ""} | null,
  "contract": {"contract_no": "", "start_date": "", "end_date": "", "value_base": 0.0} | null
}
Set a section to null when the pages hold no such document (a tax invoice =
فاتورة ضريبية; a COC = محضر الإنجاز; a receipt = إشعار تسليم / محضر استلام;
a contract/PO = عقد / أمر شراء). A COC or invoice that merely QUOTES a
contract number/value is not a contract — "contract" stays null there.
seller_vat_number is the VAT registration number (الرقم الضريبي, 15 digits)
— not the CR number (السجل التجاري). coc.claim_amount is the claim TOTAL
INCLUDING VAT (إجمالي المطالبة / شامل الضريبة), never the pre-VAT amount or
the VAT line. contract.value_base is the contract value EXCLUDING VAT.
"""

# Which (section, field) pairs the focused read overrides. Only header values —
# lines, penalty clauses and BoQ rows stay with the full read.
_KEY_FIELDS = {
    "invoice": ("invoice_no", "invoice_date", "seller_vat_number", "total_with_vat", "vat_amount"),
    "coc": ("coc_no", "coc_date", "claim_amount", "delay_days"),
    "receipt": ("receipt_no", "receipt_date"),
    "contract": ("contract_no", "start_date", "end_date", "value_base"),
}


def read_key_fields(unit: Unit, *, client: Any | None = None) -> dict[str, Any]:
    """The focused header read → {section: {field: value}} (validated types,
    empties dropped)."""
    client = client or make_client()
    raw = call_json(
        client,
        system=KEY_FIELDS_SYSTEM,
        content=[*unit_blocks(unit), {"type": "input_text", "text": f"File: {unit.path.name}\nReturn ONLY the JSON object."}],
        model=vision_model(),
        effort=get_settings().gpt_vision_effort,
        max_tokens=1500,
    )
    out: dict[str, Any] = {}
    for section, fields in _KEY_FIELDS.items():
        block = raw.get(section)
        if not isinstance(block, dict):
            continue
        vals: dict[str, Any] = {}
        for f in fields:
            v = block.get(f)
            if v in (None, "", 0, 0.0, []):
                continue
            if f in ("total_with_vat", "vat_amount", "claim_amount", "value_base"):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
            elif f == "delay_days":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
            elif f.endswith("_date"):
                v = normalize_date(str(v).strip())
            else:
                v = str(v).strip()
            vals[f] = v
        if vals:
            out[section] = vals
    return out


def apply_key_fields(read: dict[str, Any], key: dict[str, Any]) -> dict[str, Any]:
    """The focused values win over the full read's header fields — only for
    sections the full read also saw (a focused read never conjures a
    document the full read found absent)."""
    docs = read["docs"]
    changed = []
    for section, vals in key.items():
        target = docs.get(section)
        if not isinstance(target, dict):
            continue
        for f, v in vals.items():
            # Same digits in another format ("2026-07-12" vs "12-07-2026",
            # 1394950 vs 1394950.0) is agreement — keep the full read's
            # (normalised) rendering. Only a real digit-level disagreement
            # is overridden.
            if _same(target.get(f), v):
                continue
            changed.append(f"{section}.{f}: {target.get(f)!r} -> {v!r}")
            target[f] = v
    if changed:
        logger.info("key-field verify corrected %s", "; ".join(changed))
    return read


@dataclass
class Unit:
    """One model call: a page range of a PDF (rendered to images in the
    worker that reads it), or one uploaded image."""

    path: Path
    doc_type: str
    page_offset: int = 0  # 0-based first page within the source file
    page_count: int = 1
    chunk_index: int = 0
    chunk_total: int = 1
    total_pages: int = 1


# ── file → units ─────────────────────────────────────────────────────────────


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            return len(pdf)
        finally:
            pdf.close()


def build_units(path: Path, doc_type: str, *, chunk_pages: int, max_pages: int | None = None) -> list[Unit]:
    """Fan one file out into call units (page ranges). `max_pages` caps how
    much of the file is read at all (identity documents: the first pages carry
    everything). Nothing is rendered here — the reading worker does that."""
    suffix = path.suffix.lower()
    if suffix in _RASTER_EXTS:
        return [Unit(path=path, doc_type=doc_type)]
    if suffix != ".pdf":
        raise ValueError(f"unsupported file type for the GPT reader: {suffix}")

    total = pdf_page_count(path)
    limit = min(total, max_pages) if max_pages else total
    chunk_pages = max(1, chunk_pages)
    n_chunks = max(1, (limit + chunk_pages - 1) // chunk_pages)
    units: list[Unit] = []
    for c in range(n_chunks):
        start, end = c * chunk_pages, min((c + 1) * chunk_pages, limit)
        units.append(
            Unit(
                path=path, doc_type=doc_type, page_offset=start, page_count=end - start,
                chunk_index=c, chunk_total=n_chunks, total_pages=total,
            )
        )
    return units


# ── page rendering ───────────────────────────────────────────────────────────
#
# Pages are sent as IMAGES, never as a PDF `input_file`: Azure's PDF ingestion
# hands the model the file's text layer next to the pixels, and Arabic text
# layers are routinely reversed / in presentation forms — measured on a clean
# digital invoice, the PDF path misspelled the seller name 6/6 times while a
# 200-dpi render read it exactly 3/3, at a third of the input tokens. Scanned
# pages have no text layer, so for them this only fixes the resolution.

_JPEG_OVER_BYTES = 1_500_000  # a PNG bigger than this (a scan) goes as JPEG


def _encode(img: Any) -> tuple[bytes, str]:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    if buf.tell() <= _JPEG_OVER_BYTES:
        return buf.getvalue(), "image/png"
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def render_pages(path: Path, start: int, end: int, *, dpi: int) -> list[tuple[bytes, str]]:
    """Rasterize pages [start, end) (0-based) with PDFium — the page's /Rotate
    applied, i.e. exactly what the viewer shows — as (bytes, mime) per page.
    PDFium work runs under the process-wide lock (it is not thread-safe);
    each page is encoded and released before the next is rendered."""
    import pypdfium2 as pdfium

    out: list[tuple[bytes, str]] = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(start, min(end, len(pdf))):
                page = pdf[i]
                try:
                    bitmap = page.render(scale=dpi / 72.0)
                    try:
                        img = bitmap.to_pil()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                # Encode and drop each bitmap at once (~12 MB apiece at 200
                # dpi): the service runs under a 1 GB memory cap shared by
                # every concurrent worker, so never hold a chunk's pages as
                # raw bitmaps.
                out.append(_encode(img))
                del img
        finally:
            pdf.close()
    return out


def _uploaded_image(path: Path) -> tuple[bytes, str]:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_MIME:
        return path.read_bytes(), _IMAGE_MIME[suffix]
    from PIL import Image  # tif/bmp → PNG (Pillow ships with the QR/locate stack)

    with Image.open(path) as im:
        return _encode(im)


def unit_blocks(unit: Unit) -> list[dict[str, Any]]:
    """The unit's pages as `input_image` blocks, in page order (image k = page
    k of the chunk, which is how the prompt defines page numbers)."""
    if unit.path.suffix.lower() in _RASTER_EXTS:
        pages = [_uploaded_image(unit.path)]
    else:
        pages = render_pages(
            unit.path, unit.page_offset, unit.page_offset + unit.page_count, dpi=get_settings().gpt_vision_render_dpi
        )
    return [
        {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{base64.standard_b64encode(data).decode('ascii')}",
            "detail": "high",
        }
        for data, mime in pages
    ]


# ── model call ───────────────────────────────────────────────────────────────


def make_client():
    from openai import OpenAI

    s = get_settings()
    if not (s.azure_openai_base_url and s.azure_openai_api_key):
        raise RuntimeError("GPT reader not configured: set AZURE_OPENAI_BASE_URL and AZURE_OPENAI_API_KEY.")
    return OpenAI(api_key=s.azure_openai_api_key, base_url=s.azure_openai_base_url, timeout=s.gpt_vision_timeout_seconds)


def vision_model() -> str:
    s = get_settings()
    return s.gpt_vision_model or s.azure_openai_model


def response_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            t = getattr(c, "text", None)
            if t:
                parts.append(t)
    return "\n".join(parts)


def _is_incomplete(resp: Any) -> bool:
    if getattr(resp, "status", None) == "incomplete":
        return True
    details = getattr(resp, "incomplete_details", None)
    return bool(details and getattr(details, "reason", None) == "max_output_tokens")


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def call_json(
    client: Any,
    *,
    system: str,
    content: list[dict[str, Any]],
    model: str,
    effort: str,
    max_tokens: int,
) -> dict[str, Any]:
    """One Responses-API call → parsed JSON. Retries transient failures; a
    truncated, unparseable answer gets one retry with a doubled output cap."""

    def _create(cap: int) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": content},
            ],
            "max_output_tokens": cap,
        }
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        try:
            return client.responses.create(**kwargs)
        except TypeError:  # older SDK without the reasoning param
            kwargs.pop("reasoning", None)
            return client.responses.create(**kwargs)

    resp = with_retries(lambda: _create(max_tokens), attempts=3, backoff_seconds=2.0)
    text = response_text(resp)
    usage = getattr(resp, "usage", None)
    logger.info(
        "gpt call %s: in=%s out=%s status=%s",
        model, getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"), getattr(resp, "status", "?"),
    )
    try:
        return parse_json(text)
    except ValueError:
        if _is_incomplete(resp) and max_tokens < _MAX_OUTPUT_CEILING:
            bigger = min(max_tokens * 2, _MAX_OUTPUT_CEILING)
            resp = with_retries(lambda: _create(bigger), attempts=2, backoff_seconds=2.0)
            return parse_json(response_text(resp))
        raise


# ── one unit → validated read ────────────────────────────────────────────────


def _user_text(unit: Unit) -> str:
    lines = [
        f"File: {unit.path.name} — attached as {unit.page_count} page image(s), in order "
        f"(image 1 = page 1 of what you were given).",
        f"Expected document type (hint): {unit.doc_type or 'unknown'}",
    ]
    if unit.chunk_total > 1:
        lines.append(
            f"NOTE: these are pages {unit.page_offset + 1}-{unit.page_offset + unit.page_count} of a "
            f"{unit.total_pages}-page file, split for processing (chunk {unit.chunk_index + 1} of "
            f"{unit.chunk_total}). A document may continue across chunk boundaries — organize what is "
            f"visible here. For every \"page\" use THIS chunk's own 1-based page numbers (1 = the first "
            f"page you were given)."
        )
    lines.append("Return ONLY the JSON object described in the system prompt.")
    return "\n".join(lines)


def _clean_pages(obj: Any, offset: int) -> Any:
    """Coerce every "page" to an int and rebase chunk-relative numbers onto the
    source file (0 stays 0 = unknown)."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "page":
                try:
                    p = int(v or 0)
                except (TypeError, ValueError):
                    p = 0
                out[k] = p + offset if p > 0 else 0
            else:
                out[k] = _clean_pages(v, offset)
        return out
    if isinstance(obj, list):
        return [_clean_pages(x, offset) for x in obj]
    return obj


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _real_boq_rows(rows: Any) -> list[Any]:
    """Keep only identified rows that carry a contractual quantity or price. A
    BoQ table also prints section headers ("1", "2" with nothing priced),
    subtotal rows with no item number or description, and recap pages
    repeating codes with zeros — none is a line the invoice can be matched
    against."""
    if not isinstance(rows, list):
        return []
    return [
        r for r in rows
        if isinstance(r, dict)
        and (str(r.get("item_code") or "").strip() or str(r.get("description_ar") or "").strip())
        and (_num(r.get("unit_price")) != 0 or _num(r.get("quantity")) != 0)
    ]


# A penalty rate is per unit of delay time only when the clause SAYS so.
_PER_DAY = re.compile(r"كل\s*\d*\s*(يوم|أيام|ايام)|يومي|باليوم|/\s*(يوم|اليوم)|per\s+day|daily", re.I)
_PER_WEEK = re.compile(r"كل\s*\d*\s*(أسبوع|اسبوع|أسابيع|اسابيع)|أسبوعي|اسبوعي|/\s*(أسبوع|اسبوع)|per\s+week|weekly", re.I)


def _fix_penalty_per(contract: Any) -> None:
    """Deterministic guard on the clause's own wording: "غرامة لا تتجاوز
    (10%) من قيمة البند" is a CEILING — the reader sometimes still emits
    per="day", which the final check would then narrate as "10% per day".
    `per` is kept only when the verbatim clause carries a per-day / per-week
    phrase (and set when it does and the reader missed it)."""
    if not isinstance(contract, dict):
        return
    for term in contract.get("penalty_terms") or []:
        if not isinstance(term, dict):
            continue
        text = f"{term.get('text_ar') or ''} {term.get('basis') or ''}"
        if _PER_DAY.search(text):
            term["per"] = "day"
        elif _PER_WEEK.search(text):
            term["per"] = "week"
        else:
            term["per"] = ""


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_YMD = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")
_DMY = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")


def normalize_date(value: Any) -> Any:
    """A printed Gregorian date → ISO "YYYY-MM-DD". Saudi documents print
    day-month-year ("12-07-2026"); the reader is asked for ISO but sometimes
    returns the printed form, and the rules' date parser must never see two
    conventions. Anything else (a duration phrase, a Hijri date, "") is
    returned verbatim."""
    if not isinstance(value, str):
        return value
    v = value.translate(_AR_DIGITS)
    m = _YMD.match(v)
    if m:
        y, mo, d = m.groups()
    else:
        m = _DMY.match(v)
        if not m:
            return value
        d, mo, y = m.groups()
    if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
        return value
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def _normalize_dates(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: (normalize_date(v) if k.endswith("_date") else _normalize_dates(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_dates(x) for x in obj]
    return obj


def _coerce_codes(obj: Any) -> Any:
    """An item_code the model emitted as a NUMBER becomes its string form so
    the read validates instead of round-tripping through a repair call that
    only loses more (the trailing zero is already gone by then)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "item_code" and isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = str(int(v)) if float(v).is_integer() else repr(float(v))
            else:
                out[k] = _coerce_codes(v)
        return out
    if isinstance(obj, list):
        return [_coerce_codes(x) for x in obj]
    return obj


def validate_read(data: dict[str, Any], *, page_offset: int = 0) -> dict[str, Any]:
    """Model JSON → {docs: ClaimDocuments-dict, anchors: dict}. Raises on
    schema mismatch (the caller retries the call with the error)."""
    data = _normalize_dates(_coerce_codes(_clean_pages(data, page_offset)))
    anchors_raw = data.pop("anchors", None) or {}
    for k in ("penalties", "attachments", "detected_attachments"):
        data.pop(k, None)  # ERP-owned / upload-time — never read here
    data["boq"] = _real_boq_rows(data.get("boq"))
    _fix_penalty_per(data.get("contract"))
    docs = ClaimDocuments.model_validate(data)
    anchors = {k: str(anchors_raw.get(k) or "").strip() for k in _ANCHOR_KEYS} if isinstance(anchors_raw, dict) else {}
    return {"docs": docs.model_dump(), "anchors": anchors}


def read_unit(unit: Unit, *, client: Any | None = None) -> dict[str, Any]:
    client = client or make_client()
    s = get_settings()
    content: list[dict[str, Any]] = [*unit_blocks(unit), {"type": "input_text", "text": _user_text(unit)}]
    last = ""
    t0 = time.monotonic()
    for attempt in range(2):  # one repair retry on schema mismatch
        raw = call_json(
            client,
            system=READ_SYSTEM,
            content=content,
            model=vision_model(),
            effort=s.gpt_vision_effort,
            max_tokens=s.gpt_vision_max_output_tokens,
        )
        try:
            out = validate_read(raw, page_offset=unit.page_offset)
            logger.info(
                "gpt read %s chunk %d/%d (pages %d-%d): %.1fs",
                unit.path.name, unit.chunk_index + 1, unit.chunk_total,
                unit.page_offset + 1, unit.page_offset + unit.page_count, time.monotonic() - t0,
            )
            return out
        except Exception as exc:
            last = str(exc)
            content = content + [
                {
                    "type": "input_text",
                    "text": f"Your previous output failed validation: {last[:800]}. Return ONLY the corrected JSON.",
                }
            ]
    raise RuntimeError(f"GPT read of {unit.path.name} failed validation twice: {last}")


# ── merge (chunks → file, files → claim) ─────────────────────────────────────


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == 0 or v == 0.0 or v == []


def _merge_obj(a: Any, b: Any) -> Any:
    """Field-level merge: `a` wins, `b` fills its gaps; lists concatenate."""
    if a is None:
        return copy.deepcopy(b)
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k not in out:
                out[k] = copy.deepcopy(v)
            elif isinstance(out[k], list) and isinstance(v, list):
                out[k] = _dedupe(out[k] + v)
            elif isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge_obj(out[k], v)
            elif _empty(out[k]) and not _empty(v):
                out[k] = copy.deepcopy(v)
        return out
    if isinstance(a, list) and isinstance(b, list):
        return _dedupe(a + b)
    return a if not _empty(a) else b


def _dedupe(items: list[Any]) -> list[Any]:
    """Drop exact repeats (a row read on both sides of a chunk boundary),
    ignoring the page number."""
    seen: set[str] = set()
    out: list[Any] = []
    for it in items:
        key = json.dumps({k: v for k, v in it.items() if k != "page"}, sort_keys=True, ensure_ascii=False) if isinstance(it, dict) else json.dumps(it, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def dedupe_boq_codes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per item code. A BoQ prints its table once and often again on
    recap/summary pages (subtotals, no description); when a code repeats,
    the described table row wins and the first of those is kept — never the
    last-seen recap figure."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        code = str(r.get("item_code") or "")
        described = bool(r.get("description_ar") or r.get("description_en"))
        if code not in best:
            best[code] = r
            order.append(code)
        elif described and not (best[code].get("description_ar") or best[code].get("description_en")):
            best[code] = r
    return [best[c] for c in order]


def merge_reads(reads: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge validated reads in order (chunks by page order, files by
    priority): first non-null header wins, empties fill from later reads,
    line lists concatenate, BoQ codes resolve to one row each."""
    docs: dict[str, Any] | None = None
    anchors: dict[str, str] = {}
    for r in reads:
        docs = _merge_obj(docs, r["docs"])
        for k, v in (r.get("anchors") or {}).items():
            if v and not anchors.get(k):
                anchors[k] = v
    docs = docs or ClaimDocuments().model_dump()
    docs["boq"] = dedupe_boq_codes(docs.get("boq") or [])
    return {"docs": docs, "anchors": anchors}


# ── consensus for short documents ────────────────────────────────────────────
#
# A single vision read of a one-page invoice is right almost every time — and
# "almost" is a false tampering fail when it flips two digits of the VAT
# number (seen once in four reads of the same clean invoice). Short units
# (invoice, COC, delivery note, a small PO) are therefore read `gpt_vision_passes`
# times CONCURRENTLY and majority-voted; when the passes disagree, one more read
# breaks the tie. Long documents (the chunked contract) are read once — the
# gates that consume them compare against other documents anyway.


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIGIT_RUN = re.compile(r"\d+")
# Free-text fields: wording (and the digits inside a description like
# "120*120", or a clause ref read RTL as ٣.٣.١ / ١.٣.٣) legitimately varies
# between two correct reads. Never part of the agreement signature.
_TEXT_KEYS = {"description_ar", "description_en", "text_ar", "basis", "seller_name_ar", "ref", "kind", "per", "unit"}


def _sig(obj: Any) -> Any:
    """What two reads must agree on: numbers, and the digits inside strings
    (identifiers, dates, amounts) — as a sorted bag of digit runs, so
    "2026-07-12" and "12-07-2026" are the same date, "1,394,950" the same
    amount. Free text (descriptions, names, clause wording) and page numbers
    are NOT part of the signature: wording varies read to read without any
    value being wrong, and a vote on it would only cost tie-break reads."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "page" or k in _TEXT_KEYS:
                continue
            sv = _sig(v)
            if sv is not None:
                out[k] = sv
        return out
    if isinstance(obj, list):
        return [_sig(x) for x in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, (int, float)):
        return round(float(obj), 2)
    if isinstance(obj, str):
        runs = _DIGIT_RUN.findall(obj.translate(_AR_DIGITS))
        return sorted(runs) if runs else None  # digit-free text is not compared
    return obj


def _same(a: Any, b: Any) -> bool:
    return _sig(a) == _sig(b)


def _without_key_fields(docs: Any) -> Any:
    """The documents minus the header fields the focused verify read settles
    anyway (VAT number, numbers, dates, totals). Two passes that differ only
    there must not cost a tie-break read: the verify value wins regardless."""
    if not isinstance(docs, dict):
        return docs
    out = {}
    for section, val in docs.items():
        if section in _KEY_FIELDS and isinstance(val, dict):
            out[section] = {k: v for k, v in val.items() if k not in _KEY_FIELDS[section]}
        else:
            out[section] = val
    return out


def _vote_value(cands: list[Any]) -> Any:
    """The value at least two reads agree on (see _sig); else, for objects,
    a field-by-field vote; else the first read's value. Nulls only win when
    every read is null — a read that missed a document never outvotes one
    that saw it."""
    live = [c for c in cands if c is not None]
    if not live:
        return None
    for c in live:
        if sum(1 for o in live if _same(c, o)) >= 2:
            return c
    if len(live) > 1 and all(isinstance(c, dict) for c in live):
        keys: list[str] = []
        for c in live:
            keys += [k for k in c if k not in keys]
        return {k: _vote_value([c.get(k) for c in live]) for k in keys}
    return live[0]


def vote(reads: list[dict[str, Any]]) -> dict[str, Any]:
    """Majority vote over independent reads of the SAME unit."""
    if len(reads) == 1:
        return reads[0]
    docs = _vote_value([r["docs"] for r in reads]) or ClaimDocuments().model_dump()
    # Anchors are soft hints for the reconcile step: a pass that noticed a
    # dated event fills in for one that did not; no vote, no tie-break.
    anchors: dict[str, str] = {}
    for r in reads:
        for k, v in (r.get("anchors") or {}).items():
            if v and not anchors.get(k):
                anchors[k] = v
    return {"docs": docs, "anchors": anchors}


def _passes_for(unit: Unit) -> int:
    s = get_settings()
    return max(1, s.gpt_vision_passes) if unit.page_count <= s.gpt_vision_consensus_max_pages else 1


def _read_units(units: list[Unit], client: Any) -> list[dict[str, Any] | None]:
    """Every (unit, pass) job through ONE pool — a 76-page contract's chunks
    and a 1-page invoice's two passes all in flight together. Per unit: vote;
    a disagreement between two passes gets one tie-break read. A unit whose
    reads all failed yields None."""
    s = get_settings()
    jobs: list[tuple[int, Unit, bool]] = [(ui, u, False) for ui, u in enumerate(units) for _ in range(_passes_for(u))]
    # The focused header re-read: for the first chunk of every file (the
    # header page is there), concurrent with the full reads.
    if s.gpt_vision_verify:
        jobs += [(ui, u, True) for ui, u in enumerate(units) if u.chunk_index == 0]
    workers = max(1, min(s.gpt_vision_concurrency, len(jobs)))

    def _run(job: tuple[int, Unit, bool]) -> tuple[int, bool, dict[str, Any] | None]:
        ui, unit, verify = job
        try:
            return ui, verify, (read_key_fields(unit, client=client) if verify else read_unit(unit, client=client))
        except Exception:
            logger.exception("GPT %s failed on %s (chunk %d)", "verify" if verify else "reader", unit.path.name, unit.chunk_index + 1)
            return ui, verify, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(_run, jobs))
    per_unit: list[list[dict[str, Any]]] = [[] for _ in units]
    key_fields: dict[int, dict[str, Any]] = {}
    for ui, verify, read in outcomes:
        if read is None:
            continue
        if verify:
            key_fields[ui] = read
        else:
            per_unit[ui].append(read)

    def _settle(ui: int) -> dict[str, Any] | None:
        reads = per_unit[ui]
        if not reads:
            return None
        verified = bool(key_fields.get(ui))
        a, b = (reads[0]["docs"], reads[1]["docs"]) if len(reads) == 2 else (None, None)
        if verified:
            a, b = _without_key_fields(a), _without_key_fields(b)
        if len(reads) == 2 and not _same(a, b):
            logger.info("gpt read %s chunk %d: passes disagree — tie-break read", units[ui].path.name, units[ui].chunk_index + 1)
            try:
                reads.append(read_unit(units[ui], client=client))
            except Exception:
                logger.exception("tie-break read failed on %s", units[ui].path.name)
        settled = vote(reads)
        if key_fields.get(ui):
            settled = apply_key_fields(settled, key_fields[ui])
        return settled

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_settle, range(len(units))))


# ── file-level read with cache ───────────────────────────────────────────────


def _file_cache_key(path: Path, data: bytes, *, max_pages: int | None) -> Path:
    s = get_settings()
    stamp = (
        f"{READ_SYSTEM}\x00{vision_model()}\x00{s.gpt_vision_chunk_pages}\x00{max_pages or 0}"
        f"\x00img{s.gpt_vision_render_dpi}\x00passes{s.gpt_vision_passes}/{s.gpt_vision_consensus_max_pages}"
        f"\x00verify{int(s.gpt_vision_verify)}"
    )
    digest = hashlib.sha256(data + b"\x00" + stamp.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"{digest}.json"


def _load_cache(cache: Path) -> dict[str, Any] | None:
    if get_settings().extraction_cache and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _store_cache(cache: Path, merged: dict[str, Any]) -> None:
    if not get_settings().extraction_cache:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def readable_by_vision(path: Path) -> bool:
    return path.suffix.lower() in _RASTER_EXTS or path.suffix.lower() == ".pdf"


def read_via_cu(path: Path, doc_type: str) -> dict[str, Any]:
    """Files the vision reader cannot rasterize (.docx and friends — the
    wizard accepts them) go through the earlier CU OCR + GPT structuring
    path, which understands Office documents. Same {docs, anchors} contract;
    no page provenance (CU markdown has no page geometry for Word files)."""
    from app.services.extraction.cu_client import analyze_layout
    from app.services.extraction.structuring import structure_documents

    result = analyze_layout(path)
    if not result.ok:
        raise RuntimeError(f"{path.name}: {result.error}")
    docs = structure_documents([(path.name, doc_type, result.markdown)])
    return {"docs": docs.model_dump(), "anchors": {}}


def read_file(path: Path, doc_type: str, *, max_pages: int | None = None, client: Any | None = None) -> dict[str, Any]:
    """Read one file (all chunks and passes concurrently) → merged {docs, anchors}."""
    if not readable_by_vision(path):
        return read_via_cu(path, doc_type)
    cache = _file_cache_key(path, path.read_bytes(), max_pages=max_pages)
    cached = _load_cache(cache)
    if cached is not None:
        return cached
    s = get_settings()
    units = build_units(path, doc_type, chunk_pages=s.gpt_vision_chunk_pages, max_pages=max_pages)
    reads = _read_units(units, client or make_client())
    if any(r is None for r in reads):
        raise RuntimeError(f"GPT reader: {path.name} could not be read completely")
    merged = merge_reads([r for r in reads if r])
    _store_cache(cache, merged)
    return merged


def read_files(files: list[tuple[Path, str]]) -> list[dict[str, Any] | None]:
    """Read several files with ONE shared pool over every file's chunks and
    passes, so a 76-page contract and a 1-page invoice finish together. A
    file whose read fails yields None (logged) — the others still land."""
    if not files:
        return []
    s = get_settings()
    results: list[dict[str, Any] | None] = [None] * len(files)

    # Cached files short-circuit without touching the pool; Office files take
    # the CU path (no page rendering possible).
    pending: list[tuple[int, Path, list[Unit]]] = []
    for i, (path, doc_type) in enumerate(files):
        if not readable_by_vision(path):
            try:
                results[i] = read_via_cu(path, doc_type)
            except Exception:
                logger.exception("CU fallback failed for %s", path.name)
            continue
        cached = _load_cache(_file_cache_key(path, path.read_bytes(), max_pages=None))
        if cached is not None:
            results[i] = cached
            continue
        try:
            pending.append((i, path, build_units(path, doc_type, chunk_pages=s.gpt_vision_chunk_pages)))
        except Exception:
            logger.exception("GPT reader: cannot read %s", path.name)
    if not pending:
        return results

    all_units = [u for _, _, units in pending for u in units]
    reads = _read_units(all_units, make_client())
    pos = 0
    for i, path, units in pending:
        file_reads = reads[pos : pos + len(units)]
        pos += len(units)
        if any(r is None for r in file_reads):
            continue  # a missing chunk would silently drop lines — treat the file as unread
        merged = merge_reads([r for r in file_reads if r])
        results[i] = merged
        _store_cache(_file_cache_key(path, path.read_bytes(), max_pages=None), merged)
    return results


def to_documents(merged: dict[str, Any]) -> ClaimDocuments:
    return ClaimDocuments.model_validate(merged["docs"])
