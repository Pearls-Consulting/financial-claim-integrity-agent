"""Stage 2 of the `gpt` extractor: a small TEXT-ONLY cross-document patch.

Files are read independently and in parallel (gpt_vision.py), so two things
the old single-corpus prompt did across documents need one more, cheap call:

  1. invoice line item codes -> the PO/BoQ item numbers they correspond to
     (the invoice often prints its own numbering; the BoQ gate matches on
     item_code);
  2. the contract end date from a printed DURATION plus its dated anchor
     event, when the anchor is printed in ANOTHER document (the COC or
     commencement minutes) than the contract.

The model returns a PATCH, never a re-emission of the documents: every digit,
date and page read from the pages stays exactly as read. The call is skipped
outright when there is nothing to reconcile, and cached by its input.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domain.models import ClaimDocuments

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "gpt_reconcile"
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ANCHORS = ("commencement_date", "site_handover_date", "signing_date")

RECONCILE_SYSTEM = """\
You reconcile fields already read from the documents of ONE Saudi vendor
payment claim. The values are GROUND TRUTH — never change a digit, date, name
or description. You do exactly two things and return a PATCH.

Return ONLY this JSON (no fences, no commentary):
{"invoice_item_codes": {"<invoice item_code>": "<BoQ item_code>"}, "contract_end_date": "YYYY-MM-DD" | null}

1. invoice_item_codes — when the invoice uses its own line numbering, map each
   invoice line's item_code to the PO/BoQ item whose description clearly
   denotes the same goods/work. Only CLEAR correspondences (same item, same
   unit of work); omit anything uncertain and omit lines whose code already
   equals a BoQ code. Never map two invoice lines to the same BoQ item unless
   they are genuinely the same item.
2. contract_end_date — ONLY when the contract's end_date is a DURATION phrase
   (e.g. "خمسة أشهر من تاريخ محضر بدء المشروع", "12 months from signing")
   AND the duration's OWN anchor event is explicitly dated in `anchors`: a
   duration from the commencement / site-handover minutes needs
   commencement_date or site_handover_date; "from signing" needs
   signing_date. Then end_date = anchor + duration as YYYY-MM-DD. NEVER anchor
   to the COC date, the contract's issue/print date or any other incidental
   date — a wrong end date fabricates delay. Otherwise null.
"""


def canonical_code(code: str) -> str:
    """"6.10" and "6.1", "10.10" and "10.1" — what a dotted item number
    becomes when a reader emits it as a NUMBER (float semantics: trailing
    zeros of the last segment vanish). Only two-segment numeric codes are
    affected; anything else ("2.1.1", "OF-101") is kept as is."""
    c = (code or "").strip()
    m = re.fullmatch(r"(\d+)\.(\d+)", c)
    if not m:
        return c.lower()
    return f"{int(m.group(1))}.{m.group(2).rstrip('0') or '0'}"


def align_codes(docs: ClaimDocuments) -> list[str]:
    """Deterministic pre-step: an invoice/receipt line whose code is not a
    BoQ code but whose CANONICAL form matches exactly one BoQ code takes that
    BoQ code (the two documents printed the same item; one read lost a
    trailing zero). Ambiguous matches are left for the model / the rules.
    Returns the remaps made, for the log."""
    boq_codes = [line.item_code for line in docs.boq]
    if not boq_codes:
        return []
    by_canon: dict[str, list[str]] = {}
    for c in boq_codes:
        by_canon.setdefault(canonical_code(c), []).append(c)
    exact = set(boq_codes)
    remaps: list[str] = []
    for lines in ((docs.invoice.lines if docs.invoice else []), (docs.receipt.lines if docs.receipt else [])):
        for line in lines:
            if line.item_code in exact:
                continue
            cands = by_canon.get(canonical_code(line.item_code)) or []
            if len(cands) == 1:
                remaps.append(f"{line.item_code} -> {cands[0]}")
                line.item_code = cands[0]
    return remaps


def needs(docs: ClaimDocuments, anchors: dict[str, str]) -> tuple[bool, bool]:
    """(align item codes?, derive end date?) — both False means no call."""
    inv_lines = docs.invoice.lines if docs.invoice else []
    boq_codes = {line.item_code for line in docs.boq}
    need_codes = bool(inv_lines) and bool(boq_codes) and any(line.item_code not in boq_codes for line in inv_lines)
    end = (docs.contract.end_date if docs.contract else "") or ""
    need_date = bool(end) and not _ISO.match(end) and any(anchors.get(k) for k in _ANCHORS)
    return need_codes, need_date


def _payload(docs: ClaimDocuments, anchors: dict[str, str], need_codes: bool, need_date: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if need_codes:
        out["invoice_lines"] = [
            {"item_code": line.item_code, "description_ar": line.description_ar}
            for line in (docs.invoice.lines if docs.invoice else [])
        ]
        out["boq_lines"] = [
            {
                "item_code": line.item_code,
                "description_ar": line.description_ar,
                "description_en": line.description_en,
                "unit": line.unit,
            }
            for line in docs.boq
        ]
    if need_date and docs.contract:
        out["contract"] = {"start_date": docs.contract.start_date, "end_date": docs.contract.end_date}
        out["anchors"] = {k: v for k, v in anchors.items() if v}
    return out


def apply_patch(docs: ClaimDocuments, patch: dict[str, Any]) -> ClaimDocuments:
    """Deterministic application: codes only remap onto codes the BoQ really
    has; the end date only lands on a non-ISO (duration) end date."""
    codes = patch.get("invoice_item_codes") or {}
    if docs.invoice and isinstance(codes, dict):
        boq_codes = {line.item_code for line in docs.boq}
        for line in docs.invoice.lines:
            target = codes.get(line.item_code)
            if isinstance(target, str) and target in boq_codes:
                line.item_code = target
    end = patch.get("contract_end_date")
    if docs.contract and isinstance(end, str) and _ISO.match(end) and not _ISO.match(docs.contract.end_date or ""):
        docs.contract.end_date = end[:10]
    return docs


def reconcile(docs: ClaimDocuments, anchors: dict[str, str]) -> ClaimDocuments:
    remaps = align_codes(docs)
    if remaps:
        logger.info("reconcile: canonical item-code alignment %s", "; ".join(remaps))
    need_codes, need_date = needs(docs, anchors)
    if not (need_codes or need_date):
        return docs
    s = get_settings()
    model = s.gpt_reconcile_model or s.gpt_vision_model or s.azure_openai_model
    payload = json.dumps(_payload(docs, anchors, need_codes, need_date), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{RECONCILE_SYSTEM}\x00{model}\x00{payload}".encode("utf-8")).hexdigest()
    cache = _CACHE_DIR / f"{digest}.json"
    patch: dict[str, Any] | None = None
    if s.extraction_cache and cache.exists():
        try:
            patch = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            patch = None
    if patch is None:
        from app.services.extraction.gpt_vision import call_json, make_client

        try:
            patch = call_json(
                make_client(),
                system=RECONCILE_SYSTEM,
                content=[{"type": "input_text", "text": payload}],
                model=model,
                effort=s.gpt_reconcile_effort,
                max_tokens=4000,
            )
        except Exception:
            # Reconciliation is a refinement: on failure the documents ship as read.
            logger.exception("GPT reconcile failed; documents kept as read")
            return docs
        if s.extraction_cache:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    return apply_patch(docs, patch)
