"""The PDF text layer as a second witness for the GPT vision read.

Born-digital invoices (the majority of what vendors submit) carry their
digits EXACTLY in the text layer — no perception step, so no transposed or
miscounted digits. The same text layer is unreliable about everything else:
Arabic comes out reversed or as mojibake from custom font CMaps, table cells
land out of order, currency glyphs map to random code points ("元" for ر.س).
The vision model has the opposite profile: it knows WHICH value is the total
and which the VAT, but on a long digit string it transposes ("301…" for
"310…") or loses count of repeated zeros.

So the two are paired with a fixed division of labour: the image decides what
a value IS; the text layer decides its exact digits. Two uses:

  • a HINT: when the text layer is usable it is appended to the focused
    key-field verify call, so the model can copy digit strings from text it
    sees confirmed in the image (never from garbled text alone);
  • a deterministic CHECK, in code, after the reads are settled: every
    identifier and amount the model produced is looked for in the normalised
    text. Found → confident. Not found, but a unique near-variant (one
    transposition / one digit / a zero-run of a different length) is → that
    variant replaces the read, for identifiers outright, for amounts only if
    the invoice arithmetic (lines = net, net + VAT = total) holds afterwards
    and did not before. Nothing is reported to the reviewer — this is the
    reader getting the digits right, not a finding.

Scanned pages have no text layer (or an OCR layer we do not trust: it fails
the digit-run gate or simply matches nothing) and take today's image-only
path unchanged.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.services.extraction.pdfium_lock import PDFIUM_LOCK

logger = logging.getLogger(__name__)

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
_DIGIT_RUN = re.compile(r"\d+")
_NUM_TOKEN = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")
# Comma / thin- or narrow-nbsp / Arabic thousands separator (never a plain
# space: "200.00 330.00" are two cells): between a digit and EXACTLY three
# digits not followed by a fourth ("63,333.33" → "63333.33"; a decimal comma
# "2,50" stays). Mirrors the viewer's normalisation.
_THOUSANDS = re.compile(r"(?<=\d)[,٬  ](?=\d{3}(?!\d))")

_IDENTIFIER_FIELDS = {
    "invoice": ("invoice_no", "seller_vat_number"),
    "coc": ("coc_no", "invoice_ref", "contract_no", "award_letter_no"),
    "receipt": ("receipt_no",),
    "contract": ("contract_no",),
}
_MIN_ID_LEN = 5  # shorter identifiers are too easy to "find" by accident
_AMOUNT_TOL = 1.0  # SAR; rounding on printed totals
_VAT_RATE = 0.15


# ── extraction ───────────────────────────────────────────────────────────────


def pages_text(path: Path, start: int, end: int) -> str:
    """Text of pages [start, end) (0-based) joined by form feeds; "" when the
    file is not a PDF, is unreadable, or the pages carry no text layer."""
    if path.suffix.lower() != ".pdf":
        return ""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ""
    chunks: list[str] = []
    try:
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path))
            try:
                for i in range(start, min(end, len(pdf))):
                    page = pdf[i]
                    try:
                        tp = page.get_textpage()
                        try:
                            chunks.append(tp.get_text_range() or "")
                        finally:
                            tp.close()
                    finally:
                        page.close()
            finally:
                pdf.close()
    except Exception:
        logger.debug("text layer unavailable for %s", path.name, exc_info=True)
        return ""
    return "\f".join(chunks)


def unit_text(unit: Any) -> str:
    """The text layer of one reader unit (a page range of a PDF)."""
    return pages_text(unit.path, unit.page_offset, unit.page_offset + unit.page_count)


# ── normalisation ────────────────────────────────────────────────────────────


def _is_arabic_mark(c: str) -> bool:
    cp = ord(c)
    return cp == 0x0640 or 0x064B <= cp <= 0x0652 or cp == 0x0670


def normalize(s: str) -> str:
    """Matching form: Arabic-Indic digits → ASCII, Arabic decimal separator →
    ".", presentation forms folded (NFKC), tatweel/harakat dropped, thousands
    separators dropped, lower-cased, whitespace collapsed. Digits and their
    order are the only thing that survives intact — which is the point."""
    s = (s or "").translate(_AR_DIGITS)
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in s if not _is_arabic_mark(c))
    s = _THOUSANDS.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def usable(text: str) -> bool:
    """Whether the text layer is worth showing the model as a hint: it has
    the digit runs a document header carries (an identifier or an amount is
    4+ digits) and is not mostly replacement / control garbage. Garbled
    Arabic is fine — the digits are what the hint is for."""
    if not text:
        return False
    runs = [r for r in _DIGIT_RUN.findall(text.translate(_AR_DIGITS)) if len(r) >= 4]
    if len(runs) < 3:
        return False
    junk = sum(1 for c in text if c == "�" or (ord(c) < 32 and c not in "\n\r\t\f"))
    return junk <= len(text) * 0.05


def hint_block(text: str, *, max_chars: int = 12000) -> str:
    """The prompt fragment carrying the text layer to the verify read."""
    body = text if len(text) <= max_chars else text[:max_chars] + "\n…[truncated]"
    return (
        "TEXT LAYER of the same page(s), extracted directly from the PDF (not OCR). "
        "Its digits are exact, but its words may be reversed, garbled or out of order — "
        "ignore it wherever it is unreadable, and never take a value from it that you cannot "
        "see in the image. For an identifier or an amount you DO see in the image, copy the "
        "digit string from here rather than transcribing it from the pixels.\n"
        "<<<\n" + body + "\n>>>"
    )


# ── matching ─────────────────────────────────────────────────────────────────


def amount_forms(v: float) -> list[str]:
    """Printed renderings of a number, normalised: "230000.00", "230000",
    "2.5" — thousands separators are already gone from the text."""
    out = [f"{v:.2f}"]
    if float(v).is_integer():
        out.append(str(int(v)))
    else:
        g = f"{v:g}"
        if g not in out:
            out.append(g)
    return out


def _bounded(text: str, needle: str) -> bool:
    """`needle` occurs in `text` not glued to more digits on either side (so
    "230000" is not found inside "1230000"; "230000" IS found in "230000.00")."""
    i = text.find(needle)
    while i != -1:
        before = text[i - 1] if i > 0 else ""
        after = text[i + len(needle)] if i + len(needle) < len(text) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        i = text.find(needle, i + 1)
    return False


def contains_amount(norm_text: str, v: float) -> bool:
    return any(_bounded(norm_text, f) for f in amount_forms(v))


def contains_identifier(norm_text: str, ident: str) -> bool:
    """The identifier as printed ("inv-2026-0342"), or — spacing and
    punctuation aside — its digit runs in sequence in the text's digit runs."""
    n = normalize(ident)
    if not n:
        return False
    if _bounded(norm_text, n):
        return True
    runs = _DIGIT_RUN.findall(n)
    if not runs:
        return False
    text_runs = _DIGIT_RUN.findall(norm_text)
    k = len(runs)
    return any(text_runs[i : i + k] == runs for i in range(len(text_runs) - k + 1))


def _collapse_zero_runs(s: str) -> str:
    return re.sub(r"0+", "0", s)


def near(a: str, b: str) -> bool:
    """One vision slip apart: an adjacent transposition, one substituted /
    inserted / dropped character, or zero-runs of different lengths
    ("100000" vs "1000000"). Identical strings are NOT near — that is a
    match, handled before this is asked."""
    if a == b or not a or not b:
        return False
    la, lb = len(a), len(b)
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return True
    elif abs(la - lb) == 1:
        s, t = (a, b) if la < lb else (b, a)
        i = 0
        while i < len(s) and s[i] == t[i]:
            i += 1
        if s[i:] == t[i + 1 :]:
            return True
    if abs(la - lb) <= 2 and _collapse_zero_runs(a) == _collapse_zero_runs(b):
        return True
    return False


def _unique(cands: set[str]) -> str | None:
    return next(iter(cands)) if len(cands) == 1 else None


def near_identifier(norm_text: str, ident: str) -> str | None:
    """The ONE token in the text a single slip away from `ident` — in its
    printed form, or in digits-only form for a purely numeric identifier.
    None when nothing or more than one thing is near (ambiguous = leave it)."""
    n = normalize(ident)
    if len(n) < _MIN_ID_LEN:
        return None
    tokens = set(re.split(r"[\s:;,()\[\]<>\"']+", norm_text))
    hits = {t for t in tokens if len(t) >= _MIN_ID_LEN and near(t, n)}
    if hits:
        return _unique(hits)
    if n.isdigit():
        runs = {r for r in _DIGIT_RUN.findall(norm_text) if len(r) >= _MIN_ID_LEN and near(r, n)}
        return _unique(runs)
    return None


def near_amounts(norm_text: str, v: float) -> list[float]:
    """Every distinct number printed in the text a single slip away from `v`
    (in any of its printed forms). Several are common — "200000" and
    "230000" are both one slip from a miscounted "2300000" — so the caller
    disambiguates with the invoice arithmetic, never by picking one."""
    forms = amount_forms(v)
    hits: set[float] = set()
    for tok in _NUM_TOKEN.findall(norm_text):
        f = _safe_float(tok)
        if f is None or f == v:
            continue
        if any(near(c, form) for c in amount_forms(f) for form in forms):
            hits.add(f)
    return sorted(hits)


def _safe_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


# ── invoice arithmetic ───────────────────────────────────────────────────────


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _close(a: float, b: float, tol: float = _AMOUNT_TOL) -> bool:
    return abs(a - b) <= tol


def invoice_consistent(inv: dict[str, Any]) -> bool | None:
    """True/False when the header can be checked (lines sum to the net, or
    the VAT is 15% of the net); None when nothing in it can be checked."""
    total, vat = _f(inv.get("total_with_vat")), _f(inv.get("vat_amount"))
    lines = [ln for ln in (inv.get("lines") or []) if isinstance(ln, dict)]
    net = total - vat
    checks: list[bool] = []
    if lines and any(_f(ln.get("amount")) for ln in lines):
        checks.append(_close(sum(_f(ln.get("amount")) for ln in lines), net))
    if vat > 0 and net > 0:
        checks.append(_close(vat, net * _VAT_RATE, max(_AMOUNT_TOL, net * 0.001)))
    if not checks:
        return None
    return all(checks)


def coc_consistent(coc: dict[str, Any]) -> bool | None:
    """The COC restates the claim as net + VAT = total (and VAT = 15% of
    net); True/False when at least one of those can be checked, None when
    the print carries only the total."""
    net, vat, total = _f(coc.get("claim_net")), _f(coc.get("vat_amount")), _f(coc.get("claim_amount"))
    checks: list[bool] = []
    if net > 0 and vat > 0 and total > 0:
        checks.append(_close(net + vat, total))
    if net > 0 and vat > 0:
        checks.append(_close(vat, net * _VAT_RATE, max(_AMOUNT_TOL, net * 0.001)))
    if not checks:
        return None
    return all(checks)


def line_consistent(ln: dict[str, Any]) -> bool:
    q, u, a = _f(ln.get("quantity")), _f(ln.get("unit_price")), _f(ln.get("amount"))
    return a > 0 and _close(q * u, a, max(_AMOUNT_TOL, a * 0.001))


# ── reconcile ────────────────────────────────────────────────────────────────


def reconcile(docs: dict[str, Any], text: str) -> list[str]:
    """Settle the model's digit strings against the page text, in place.
    Returns a log of the corrections made (empty = every value checked was
    found in the text, or nothing could be settled)."""
    norm = normalize(text)
    if len(_DIGIT_RUN.findall(norm)) < 3:
        return []  # no text layer to speak of — a scan; nothing to check against
    changed: list[str] = []

    for section, fields in _IDENTIFIER_FIELDS.items():
        doc = docs.get(section)
        if not isinstance(doc, dict):
            continue
        for f in fields:
            cur = doc.get(f)
            if not isinstance(cur, str) or len(normalize(cur)) < _MIN_ID_LEN or contains_identifier(norm, cur):
                continue
            alt = near_identifier(norm, cur)
            if alt and alt != normalize(cur):
                doc[f] = _restyle(cur, alt)
                changed.append(f"{section}.{f}: {cur!r} -> {doc[f]!r}")

    inv = docs.get("invoice")
    if isinstance(inv, dict):
        changed += _reconcile_invoice(inv, norm)
    coc = docs.get("coc")
    if isinstance(coc, dict):
        changed += _reconcile_coc(coc, norm)
    return changed


def _restyle(original: str, alt: str) -> str:
    """Give the text-layer token the original's casing when it is the same
    token up to case ("inv-…" back to "INV-…"); digits-only stays as is."""
    if original.lower() == alt.lower():
        return original
    return alt.upper() if any(c.isalpha() for c in alt) and original.isupper() else alt


def _reconcile_coc(coc: dict[str, Any], norm: str) -> list[str]:
    """Same gate as the invoice header: only a COC whose printed net + VAT
    + total do not add up as read, and only the single near-variant that
    makes them add up."""
    changed: list[str] = []
    if coc_consistent(coc) is not False:
        return changed
    for k in ("claim_amount", "claim_net", "vat_amount"):
        cur = _f(coc.get(k))
        if cur <= 0 or contains_amount(norm, cur):
            continue
        repairs = [alt for alt in near_amounts(norm, cur) if coc_consistent({**coc, k: alt}) is True]
        if len(repairs) == 1:
            changed.append(f"coc.{k}: {cur!r} -> {repairs[0]!r}")
            coc[k] = repairs[0]
            break
    return changed


def _reconcile_invoice(inv: dict[str, Any], norm: str) -> list[str]:
    changed: list[str] = []

    # Lines first: a line whose own arithmetic fails, with exactly one of its
    # three numbers absent from the text and a near-variant that repairs the
    # arithmetic, takes the variant.
    for ln in inv.get("lines") or []:
        if not isinstance(ln, dict) or line_consistent(ln):
            continue
        missing = [k for k in ("amount", "unit_price", "quantity") if _f(ln.get(k)) > 0 and not contains_amount(norm, _f(ln.get(k)))]
        if len(missing) != 1:
            continue
        k = missing[0]
        repairs = [alt for alt in near_amounts(norm, _f(ln.get(k))) if line_consistent({**ln, k: alt})]
        if len(repairs) == 1:
            changed.append(f"invoice.lines[{ln.get('item_code')}].{k}: {ln.get(k)!r} -> {repairs[0]!r}")
            ln[k] = repairs[0]

    # Header totals: only when the invoice does not add up as read, and a
    # single near-variant makes it add up.
    before = invoice_consistent(inv)
    if before is True:
        return changed
    for k in ("total_with_vat", "vat_amount"):
        cur = _f(inv.get(k))
        if cur <= 0 or contains_amount(norm, cur):
            continue
        repairs = [alt for alt in near_amounts(norm, cur) if invoice_consistent({**inv, k: alt}) is True]
        if len(repairs) == 1:
            changed.append(f"invoice.{k}: {cur!r} -> {repairs[0]!r}")
            inv[k] = repairs[0]
            break  # one fix settles the header; the trial already checked it with the new value
    return changed
