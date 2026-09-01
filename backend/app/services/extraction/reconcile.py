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

import difflib
import hashlib
import json
import logging
import re
import unicodedata
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
{"invoice_line_codes": [{"line": <0-based index into invoice_lines>, "boq_item_code": "<BoQ item_code>"}], "contract_end_date": "YYYY-MM-DD" | null}

1. invoice_line_codes — when the invoice uses its own line numbering, or
   prints NO item numbers at all, map each invoice line (by its `line` index
   exactly as given in invoice_lines) to the PO/BoQ item whose description
   denotes the same goods/work. Match MEANING, not bytes: hamza/spelling
   variants (الكتروني = إلكتروني), punctuation, and rewordings of the same
   scope are the same item; an equal unit_price corroborates. BEWARE: the
   invoice's own serial numbering can COINCIDE with an unrelated BoQ code —
   code equality proves nothing; judge every line by its description, and
   return a mapping for any line whose current code points at the WRONG BoQ
   item. Omit lines already pointing at the right item, omit anything
   uncertain, and never map two invoice lines to the same BoQ item.
2. contract_end_date — ONLY when the contract's end_date is a DURATION phrase
   (e.g. "خمسة أشهر من تاريخ محضر بدء المشروع", "12 months from signing")
   AND the duration's OWN anchor event is explicitly dated in `anchors`: a
   duration from the commencement / site-handover minutes needs
   commencement_date or site_handover_date; "from signing" needs
   signing_date. Then end_date = anchor + duration as YYYY-MM-DD. NEVER anchor
   to the COC date, the contract's issue/print date or any other incidental
   date — a wrong end date fabricates delay. Otherwise null.
"""


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# Letter folds two correct readings of the same printed word disagree on:
# hamza carriers, alef maqsura, ta marbuta. Never digits, never word order.
_AR_FOLDS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_AR_MARKS = re.compile(r"[ـً-ْٰ]")  # tatweel, harakat
_NON_WORD = re.compile(r"[^0-9a-zء-ي]+")


def canonical_code(code: str) -> str:
    """"6.10" and "6.1", "٣٥" and "35" — the same printed item number in the
    forms different readers emit it (float semantics eat a trailing zero;
    the same schedule prints in either digit script). Only two-segment
    numeric codes lose trailing zeros; anything else ("2.1.1", "OF-101") is
    kept, digit-script- and case-normalized."""
    c = (code or "").strip().translate(_AR_DIGITS)
    m = re.fullmatch(r"(\d+)\.(\d+)", c)
    if not m:
        return c.lower()
    return f"{int(m.group(1))}.{m.group(2).rstrip('0') or '0'}"


def normalize_desc(s: str) -> str:
    """An item description reduced to what survives any correct reading of
    the same printed words: NFKC, digits to ASCII, harakat/tatweel dropped,
    hamza/ta-marbuta folds, punctuation to spaces, whitespace collapsed."""
    s = unicodedata.normalize("NFKC", (s or "")).translate(_AR_DIGITS).lower()
    s = _AR_MARKS.sub("", s).translate(_AR_FOLDS)
    return " ".join(t for t in _NON_WORD.split(s) if t)


def _strength(a: str, b: str) -> float:
    """How much two normalized descriptions look like the same printed item:
    1.0 identical; else the better of the symmetric ratio and the COVERAGE
    of the shorter string (how much of it the longer one reproduces, capped
    at 0.95) — a BoQ row is a full scope paragraph while the invoice prints
    its first clause, and a plain ratio punishes that truncation."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # autojunk=False: the default marks characters occurring in >1% of a
    # 200+ char string as junk — i.e. most Arabic letters in a long BoQ
    # scope paragraph — and guts the match.
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    r = sm.ratio()
    shorter = min(len(a), len(b))
    if shorter >= 15:
        cov = sum(bl.size for bl in sm.get_matching_blocks()) / shorter
        r = max(r, min(cov, 0.95))
    return r


def _boq_strength(desc_norm: str, boq_line: Any) -> float:
    return _strength(desc_norm, normalize_desc(boq_line.description_ar) or normalize_desc(boq_line.description_en))


def _best_boq_match(desc: str, unit_price: float, boq: list[Any], exclude: str | None = None) -> str | None:
    """The single BoQ code that is decisively the line's own, two ways:
    (a) WORDING alone — same words / near-identical / covered truncation,
    >= 0.9 with the runner-up clearly behind; (b) the printed UNIT PRICE
    singles out exactly ONE BoQ row AND the wording leans the same way
    (>= 0.45 and top-scoring) — an invoice that paraphrases the scope but
    bills the row's exact price. None on any ambiguity — never guess."""
    d = normalize_desc(desc)
    if not d:
        return None
    strength: dict[str, float] = {}
    price_eq: dict[str, bool] = {}
    for b in boq:
        if b.item_code == exclude:
            continue
        strength[b.item_code] = max(strength.get(b.item_code, 0.0), _boq_strength(d, b))
        if unit_price and abs(b.unit_price - unit_price) <= 0.01:
            price_eq[b.item_code] = True
    if not strength:
        return None
    scores = {c: s + (0.05 if price_eq.get(c) else 0.0) for c, s in strength.items()}
    best = max(scores, key=lambda c: scores[c])
    runner_up = max((v for c, v in scores.items() if c != best), default=0.0)
    if scores[best] >= 0.9 and runner_up <= scores[best] - 0.04:
        return best
    if len(price_eq) == 1 and price_eq.get(best) and strength[best] >= 0.45:
        return best
    return None


def align_codes(docs: ClaimDocuments) -> list[str]:
    """Deterministic pre-step over every invoice/receipt line.

    A line whose code is NOT in the BoQ: (1) exactly one BoQ code with the
    same canonical form takes it (a lost trailing zero, a digit-script
    change); (2) else the one BoQ line whose description is decisively its
    own takes it (an invoice that prints no item numbers, hamza/spelling
    variants).

    A line whose code IS in the BoQ can still be wrong — invoices number
    their own lines, and that serial numbering can coincide with a real BoQ
    code (a line "1" billing BoQ item 5). The code's own row must look like
    the line (description agreement, or an equal unit price for a tersely
    described line); when it does not AND another BoQ row decisively
    matches, the line moves there. Ambiguity, and a BoQ item already billed
    by another line of the same document, are left for the model / the
    rules. Returns the remaps made, for the log."""
    if not docs.boq:
        return []
    by_code = {line.item_code: line for line in docs.boq}
    by_canon: dict[str, set[str]] = {}
    for c in by_code:
        by_canon.setdefault(canonical_code(c), set()).add(c)
    remaps: list[str] = []
    for lines in ((docs.invoice.lines if docs.invoice else []), (docs.receipt.lines if docs.receipt else [])):
        taken = {line.item_code for line in lines}
        for line in lines:
            price = float(getattr(line, "unit_price", 0.0) or 0.0)
            if line.item_code in by_code:
                if _code_plausible(line, by_code[line.item_code]):
                    continue
                target = _best_boq_match(line.description_ar, price, docs.boq, exclude=line.item_code)
                note = " (code collided with an unrelated BoQ item)"
            else:
                target = None
                note = ""
                if line.item_code.strip():
                    cands = by_canon.get(canonical_code(line.item_code)) or set()
                    if len(cands) == 1:
                        target = next(iter(cands))
                if target is None:
                    target = _best_boq_match(line.description_ar, price, docs.boq)
            if target and target != line.item_code and target not in taken:
                remaps.append(f"{line.item_code or '(no code)'} -> {target}{note}")
                line.item_code = target
                taken.add(target)
    return remaps


def _code_plausible(line: Any, ref: Any) -> bool:
    """Whether the BoQ row a line's code points at looks like the line
    itself: the description agrees (a mere overbilling of the RIGHT item
    must stay put for the price rule to report), or the printed unit price
    is the row's own (a tersely described line billing its real code), or
    there is no description to judge by — code equality then stands."""
    d = normalize_desc(line.description_ar)
    if not d:
        return True
    price = float(getattr(line, "unit_price", 0.0) or 0.0)
    if price and abs(ref.unit_price - price) <= 0.01:
        return True
    return _boq_strength(d, ref) >= 0.5


def needs(docs: ClaimDocuments, anchors: dict[str, str]) -> tuple[bool, bool]:
    """(align item codes?, derive end date?) — both False means no call.
    Codes need the model when a line's code is not in the BoQ, or when it is
    but the row it points at does not look like the line (the invoice's own
    serial numbering colliding with a real code)."""
    inv_lines = docs.invoice.lines if docs.invoice else []
    by_code = {line.item_code: line for line in docs.boq}
    need_codes = bool(inv_lines) and bool(by_code) and any(
        line.item_code not in by_code or not _code_plausible(line, by_code[line.item_code])
        for line in inv_lines
    )
    end = (docs.contract.end_date if docs.contract else "") or ""
    need_date = bool(end) and not _ISO.match(end) and any(anchors.get(k) for k in _ANCHORS)
    return need_codes, need_date


def _payload(docs: ClaimDocuments, anchors: dict[str, str], need_codes: bool, need_date: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if need_codes:
        out["invoice_lines"] = [
            {"line": i, "item_code": line.item_code, "description_ar": line.description_ar, "unit_price": line.unit_price}
            for i, line in enumerate(docs.invoice.lines if docs.invoice else [])
        ]
        out["boq_lines"] = [
            {
                "item_code": line.item_code,
                "description_ar": line.description_ar,
                "description_en": line.description_en,
                "unit": line.unit,
                "unit_price": line.unit_price,
            }
            for line in docs.boq
        ]
    if need_date and docs.contract:
        out["contract"] = {"start_date": docs.contract.start_date, "end_date": docs.contract.end_date}
        out["anchors"] = {k: v for k, v in anchors.items() if v}
    return out


def apply_patch(docs: ClaimDocuments, patch: dict[str, Any]) -> ClaimDocuments:
    """Deterministic application: codes only remap onto codes the BoQ really
    has, each BoQ item at most once; the end date only lands on a non-ISO
    (duration) end date."""
    if docs.invoice:
        boq_codes = {line.item_code for line in docs.boq}
        lines = docs.invoice.lines
        used: set[str] = set()
        mappings = patch.get("invoice_line_codes")
        if isinstance(mappings, list):
            # Index-keyed: the only shape that can align lines whose printed
            # code is empty or duplicated (a code-keyed dict collapses them).
            for m in mappings:
                if not isinstance(m, dict):
                    continue
                i, target = m.get("line"), m.get("boq_item_code")
                if (
                    isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(lines)
                    and isinstance(target, str) and target in boq_codes and target not in used
                ):
                    lines[i].item_code = target
                    used.add(target)
        codes = patch.get("invoice_item_codes")  # pre-index patch shape (old caches)
        if isinstance(codes, dict):
            for line in lines:
                target = codes.get(line.item_code)
                if isinstance(target, str) and target in boq_codes and target not in used:
                    line.item_code = target
                    used.add(target)
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
