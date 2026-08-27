"""On-demand CU layout locate: find a value's polygons on a document page.

Fallback for the document viewer's highlight when the PDF text layer can't be
matched — scanned pages (the real client contracts), rotated BoQ tables, or a
corrupt/reversed Arabic text layer. Azure CU OCRs ONE page and returns
word-level quadrilaterals; we match the value against the words and return
polygons as page-relative (0..1) fractions for the client to draw over the
rendered page.

Ported from the prequalification agent's cu_locate (its proven normalisation +
matching ladder), with one simplification: pages are always rasterized with
PDFium before the CU call. That gives a single code path for scanned, rotated
and digital pages alike — PDFium applies the page's /Rotate exactly like the
viewer's pdf.js does, so the fractions line up on screen by construction.

Cost is bounded two ways: CU runs at most once per (document content, page) —
the layout is cached on disk keyed by the file's content hash, and documents
are immutable so the cache never invalidates — and a lookup never OCRs the
whole document: the extractor cites the page every value was read from, so
the fallback OCRs that page and at most its nearest neighbours
(LOCATE_MAX_PAGES, default 3). CU bills per page; the 76-page contract must
cost one page per evidence click, not 76.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.extraction.pdfium_lock import PDFIUM_LOCK

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "cu_locate"

# Image inputs CU can OCR directly (no page split — a single page by nature).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# Non-literal field values (normalised) that never correspond to page text.
_NON_LITERAL = {"yes", "no", "true", "false", "n/a", "na", "none", "unknown"}

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# Arabic letter variants that carry the same letter for matching (NFKC does not
# unify these): Persian kaf/yeh, hamza'd alef forms. Yeh-maqsura is deliberately
# NOT folded — it changes real words (على/علي).
_AR_LETTERS = str.maketrans("کیأإآٱ", "كياااا")
# tatweel/kashida + harakat + superscript alef
_MARKS = re.compile(r"[ـً-ْٰ]")


def _norm(s: str) -> str:
    """Normalise for matching — mirrors the viewer's client-side normaliser:
    Arabic-Indic digits → ASCII; in-number separators dropped; NFKC; Perso-
    Arabic letter variants unified; tatweel/harakat dropped; whitespace removed
    (CU emits per-word tokens); lower-cased."""
    s = (s or "").translate(_AR_DIGITS).replace("٬", ",").replace("٫", ".")
    # A thousands separator is a comma/space followed by EXACTLY three digits
    # ("23,115,000" → "23115000"); a decimal comma ("2,50") stays, so the
    # printed 2,50% can match 2.50%.
    s = re.sub(r"(?<=\d)[,\s](?=\d{3}(?!\d))", "", s)
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_AR_LETTERS)
    s = _MARKS.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _parse_source(src: str) -> list[tuple[float, float]]:
    """'D(1,x1,y1,x2,y2,x3,y3,x4,y4)' → [(x1,y1),…,(x4,y4)] (page units)."""
    try:
        inside = src[src.index("(") + 1 : src.rindex(")")]
        nums = [float(x) for x in inside.split(",")]
    except (ValueError, IndexError):
        return []
    coords = nums[1:]  # drop the leading page number
    return [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]


def _render_page_png(doc_path: Path, page: int, dpi: int = 200) -> bytes:
    """Rasterize one 1-based PDF page to PNG with PDFium. The render applies
    the page's /Rotate flag — the same view pdf.js shows — so CU's polygons
    come back in the on-screen coordinate space."""
    import io

    import pypdfium2 as pdfium

    with PDFIUM_LOCK:  # PDFium is not thread-safe (a reader run may be rendering)
        pdf = pdfium.PdfDocument(str(doc_path))
        try:
            pg = pdf[page - 1]
            try:
                bitmap = pg.render(scale=dpi / 72.0)
                img = bitmap.to_pil()
                bitmap.close()
            finally:
                pg.close()
        finally:
            pdf.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _page_count(doc_path: Path) -> int:
    try:
        import pypdfium2 as pdfium

        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(doc_path))
            try:
                return len(pdf)
            finally:
                pdf.close()
    except Exception:
        return 0


def _extract_layout(rd: dict[str, Any]) -> dict[str, Any]:
    """Pull page width/height + word & line geometry out of the CU response."""
    contents = rd.get("contents") or []
    pages = (contents[0].get("pages") if contents else None) or []
    if not pages:
        return {"w": 0.0, "h": 0.0, "words": [], "lines": []}
    p = pages[0]

    def _items(key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in p.get(key) or []:
            poly = _parse_source(it.get("source") or "")
            if poly:
                out.append({"content": it.get("content") or "", "poly": poly})
        return out

    return {
        "w": float(p.get("width") or 0.0),
        "h": float(p.get("height") or 0.0),
        "words": _items("words"),
        "lines": _items("lines"),
    }


def _get_page_layout(doc_path: Path, page: int) -> dict[str, Any]:
    """Return {w, h, words, lines} for one page, from cache or a CU call."""
    data = doc_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    is_image = doc_path.suffix.lower() in _IMAGE_EXTS
    cache_page = 1 if is_image else page
    cache = _CACHE_DIR / sha / f"p{cache_page}.json.gz"
    if cache.exists():
        try:
            return json.loads(gzip.decompress(cache.read_bytes()).decode("utf-8"))
        except Exception:  # corrupt cache — fall through and re-fetch
            pass

    if is_image:
        binary, content_type = data, {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
            doc_path.suffix.lower().lstrip("."), "application/octet-stream"
        )
    else:
        binary, content_type = _render_page_png(doc_path, page), "image/png"

    from azure.ai.contentunderstanding import ContentUnderstandingClient
    from azure.core.credentials import AzureKeyCredential

    s = get_settings()
    if not (s.azure_cu_endpoint and s.azure_cu_key):
        raise RuntimeError("CU not configured (AZURE_CU_ENDPOINT / AZURE_CU_KEY)")
    client = ContentUnderstandingClient(
        endpoint=s.azure_cu_endpoint,
        credential=AzureKeyCredential(s.azure_cu_key),
        api_version=s.azure_cu_api_version,
    )
    poller = client.begin_analyze_binary(
        analyzer_id=s.azure_cu_analyzer,
        binary_input=binary,
        content_type=content_type,
    )
    layout = _extract_layout(poller.result().as_dict())
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(gzip.compress(json.dumps(layout).encode("utf-8")))
    except Exception as exc:
        logger.warning("locate: cache write failed: %s", exc)
    return layout


def _flatten(items: list[dict[str, Any]]) -> tuple[str, list[int]]:
    """Concatenated normalised text of every item + the owning item index of
    each char. Items are joined WITHOUT a separator so a value the OCR split
    across words still matches as one run."""
    norm = ""
    owner: list[int] = []
    for i, it in enumerate(items):
        for ch in _norm(it["content"]):
            norm += ch
            owner.append(i)
    return norm, owner


def _on_boundary(norm: str, owner: list[int], idx: int, end: int) -> bool:
    """True when the match [idx, end) isn't glued to more text of the SAME
    item — comparing owners is what makes this mean "word boundary"."""
    left = idx == 0 or owner[idx - 1] != owner[idx] or not norm[idx - 1].isalnum()
    right = end >= len(norm) or owner[end] != owner[end - 1] or not norm[end].isalnum()
    return left and right


_NUMERIC_NEEDLE = re.compile(r"^[\d.,:/%\-()]+$")


def _is_numeric(needle: str) -> bool:
    return bool(needle) and bool(_NUMERIC_NEEDLE.match(needle)) and any(c.isdigit() for c in needle)


def _on_digit_boundary(norm: str, owner: list[int], idx: int, end: int, *, allow_fraction: bool = False) -> bool:
    """A number must be the WHOLE printed number: "89.9" is not inside "189.9"
    or "89.95", and item "2.14" is not inside clause "3.2.14" or "2.14.1"
    (a separator + digit on either side continues the number). Only text of
    the SAME item counts — items are flattened without separators, so the
    previous word's last digit must not veto a match."""
    if idx > 0 and owner[idx - 1] == owner[idx]:
        if norm[idx - 1].isdigit():
            return False
        if norm[idx - 1] in "./" and idx > 1 and owner[idx - 2] == owner[idx] and norm[idx - 2].isdigit():
            return False
    if end < len(norm) and owner[end] == owner[end - 1]:
        if norm[end].isdigit():
            return False
        # An integer digit run may continue into a fraction ("7017614" in
        # "7017614.10"); an exact needle may not ("2.14" in "2.14.1").
        if not allow_fraction and norm[end] in "./" and end + 1 < len(norm) and owner[end + 1] == owner[end - 1] and norm[end + 1].isdigit():
            return False
    return True


Polys = list[list[tuple[float, float]]]


def _match_all(items: list[dict[str, Any]], needle: str) -> list[Polys]:
    """Every occurrence of `needle` (already normalised) as a contiguous run
    of item contents; each occurrence is the polygons of the items spanning
    it. Short needles must sit on a word boundary; numeric needles must be
    the whole printed number (digit boundary)."""
    norm, owner = _flatten(items)
    short = len(needle) < 4
    numeric = _is_numeric(needle)
    out: list[Polys] = []
    start = 0
    while True:
        idx = norm.find(needle, start)
        if idx == -1:
            return out
        end = idx + len(needle)
        ok = (not short or _on_boundary(norm, owner, idx, end)) and (not numeric or _on_digit_boundary(norm, owner, idx, end))
        if ok:
            i0, i1 = owner[idx], owner[end - 1]
            out.append([items[i]["poly"] for i in range(i0, i1 + 1)])
        start = idx + 1


def _match(items: list[dict[str, Any]], needle: str) -> Polys:
    """First occurrence (see _match_all)."""
    occ = _match_all(items, needle)
    return occ[0] if occ else []


def _rtl_percent(value: str) -> str | None:
    """"28%" as an Arabic line actually prints it: the OCR emits the '%' word
    BEFORE the digits, so the page reads "%28"."""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*%\s*", value or "")
    return f"%{m.group(1)}" if m else None


_MONEY_WORDS = re.compile(r"\b(sar|usd|eur|gbp|aed|ريال|درهم|دولار|يورو)\b", re.I)


def _value_digits(value: str) -> str:
    """Significant integer digits of a numeric value ("SAR 7,017,614.10" →
    "7017614"), or "" when the value isn't essentially a number."""
    s = str(value or "")
    residue = _MONEY_WORDS.sub("", s)
    residue = re.sub(r"[\d\s.,/:%٬٫\-()]+", "", residue)
    if len(residue) > 2:  # substantial non-numeric text ⇒ not an amount
        return ""
    m = re.search(r"\d[\d,\s٬٫.]*", _norm(value))
    if not m:
        return ""
    return re.split(r"[.٫/]", m.group(0))[0].lstrip("0")


def _match_number_all(items: list[dict[str, Any]], digits: str) -> list[Polys]:
    """Locate an amount by its significant INTEGER digits on a digit boundary —
    finds "7,017,614.10" whether printed "٧٬٠١٧٬٦١٤٫١٠" or "7017614/10".
    Only for runs ≥ 4 digits (shorter is too ambiguous)."""
    if len(digits) < 4:
        return []
    norm, owner = _flatten(items)
    out: list[Polys] = []
    start = 0
    while True:
        idx = norm.find(digits, start)
        if idx == -1:
            return out
        end = idx + len(digits)
        if _on_digit_boundary(norm, owner, idx, end, allow_fraction=True):
            i0, i1 = owner[idx], owner[end - 1]
            out.append([items[i]["poly"] for i in range(i0, i1 + 1)])
        start = idx + 1


def _match_number(items: list[dict[str, Any]], digits: str) -> Polys:
    occ = _match_number_all(items, digits)
    return occ[0] if occ else []


_EDGE_PUNCT = "،,.:;()[]{}«»\"'“”‘’-—…"


def _tok(word: str) -> str:
    return _norm(word).strip(_EDGE_PUNCT)


def _word_tokens(value: str) -> list[str]:
    return [t for t in (_tok(w) for w in re.split(r"\s+", value or "")) if len(t) >= 2]


def _match_fuzzy(items: list[dict[str, Any]], values: list[str], *, gap: int = 2) -> Polys:
    """Fallback for long phrases OCR transcribed imperfectly: highlight the run
    of a value's own words covering the most DISTINCT tokens (gap-tolerant).
    Fires only for multi-word values (≥3 distinctive words)."""
    best_n, best_s, best_e = 0.0, -1, -1
    for v in values:
        toks = _word_tokens(v)
        if len(toks) < 3:
            continue
        nset = set(toks)
        cur_start: int | None = None
        cur_seen: set[str] = set()
        last = gapc = 0

        def flush() -> None:
            nonlocal best_n, best_s, best_e
            if cur_start is not None and len(cur_seen) > best_n:
                best_n, best_s, best_e = len(cur_seen), cur_start, last

        for i, it in enumerate(items):
            w = _tok(it["content"])
            if w in nset:
                if cur_start is None:
                    cur_start = i
                    cur_seen = set()
                cur_seen.add(w)
                last = i
                gapc = 0
            elif cur_start is not None:
                gapc += 1
                if gapc > gap:
                    flush()
                    cur_start, cur_seen, gapc = None, set(), 0
        flush()
        if best_n >= 4 or (best_n >= 3 and best_n >= 0.6 * len(nset)):
            return [items[i]["poly"] for i in range(best_s, best_e + 1)]
    return []


def _locatable(values: list[str]) -> bool:
    return not all(_norm(v) in _NON_LITERAL for v in values if (v or "").strip())


def _decimal_comma(value: str) -> str | None:
    """"2.50" as an Arabic/European page prints it: "2,50". Not for a 3-digit
    fraction ("1.500" → "1,500" would read as a thousand)."""
    if not re.search(r"\d\.\d", value or "") or re.search(r"\d\.\d{3}(?!\d)", value):
        return None
    return re.sub(r"(?<=\d)\.(?=\d)", ",", value)


def _needles(values: list[str]) -> list[str]:
    """Normalised search forms of the values, in priority order: as given,
    then the RTL percent form ("28%" printed "%28"), then the decimal-comma
    form ("2.50%" printed "2,50%")."""
    forms: list[str] = list(values)
    for v in values:
        swapped = _rtl_percent(v)
        if swapped:
            forms.append(swapped)
    for v in list(forms):
        comma = _decimal_comma(v)
        if comma:
            forms.append(comma)
    needles: list[str] = []
    for f in forms:
        n = _norm(f)
        if n and n not in needles:
            needles.append(n)
    return needles


def _centroid(polys: Polys) -> tuple[float, float]:
    pts = [pt for poly in polys for pt in poly]
    if not pts:
        return (0.0, 0.0)
    return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))


def _nearest(occurrences: list[Polys], ref: Polys | None) -> Polys:
    """The occurrence closest to the reference region (the matched anchor
    clause), else the first one."""
    if not occurrences:
        return []
    if not ref:
        return occurrences[0]
    rx, ry = _centroid(ref)
    return min(occurrences, key=lambda occ: (lambda c: (c[0] - rx) ** 2 + (c[1] - ry) ** 2)(_centroid(occ)))


def _find_anchor(layout: dict[str, Any], anchors: list[str]) -> Polys:
    """Where the verbatim clause/excerpt sits on the page — exact run first,
    then the fuzzy word-run. Used to choose BETWEEN several occurrences of
    the value, never instead of it."""
    for needle in _needles(anchors):
        polys = _match(layout["words"], needle) or _match(layout["lines"], needle)
        if polys:
            return polys
    return _match_fuzzy(layout["words"], anchors)


def _find_value(layout: dict[str, Any], values: list[str], *, anchor: Polys | None) -> Polys:
    """The matching ladder for ONE value on ONE page, in priority order:
    exact run in words (line-level only for text — a number found inside a
    line must be the word, never the whole line); the integer digit run of
    an amount; a fuzzy word-run for long phrases. Among several occurrences,
    the one nearest the anchor clause wins."""
    for needle in _needles(values):
        occ = _match_all(layout["words"], needle)
        if not occ and not _is_numeric(needle):
            occ = _match_all(layout["lines"], needle)
        if occ:
            return _nearest(occ, anchor)
    for v in values:  # amounts: retry on the integer digit run alone
        digits = _value_digits(v)
        if digits:
            occ = _match_number_all(layout["words"], digits)
            if occ:
                return _nearest(occ, anchor)
    return _match_fuzzy(layout["words"], values)


def locate_value_on_page(
    doc_path: Path, page: int, values: list[str], *, anchors: list[str] | None = None
) -> dict[str, Any]:
    """Locate any of `values` on `page`; return {found, polygons} with
    page-relative fractional coordinates. `anchors` (the verbatim clause the
    value came from) only disambiguate between several occurrences."""
    if not _locatable(values) or not _needles(values):
        return {"found": False, "polygons": []}
    layout = _get_page_layout(doc_path, page)
    w, h = layout["w"], layout["h"]
    if not w or not h:
        return {"found": False, "polygons": []}
    anchor = _find_anchor(layout, anchors) if anchors else None
    polys = _find_value(layout, values, anchor=anchor)
    frac = [[{"x": x / w, "y": y / h} for (x, y) in poly] for poly in polys]
    return {"found": bool(frac), "polygons": frac}


def scan_order(page: int, total: int, max_pages: int) -> list[int]:
    """Pages to try, nearest first: the cited page, then its neighbours
    outward (p-1, p+1, p-2, ...), at most `max_pages` in total."""
    total = max(total, 1)
    page = min(max(page, 1), total)
    order = [page]
    step = 1
    limit = max(1, max_pages)
    while len(order) < limit and (page - step >= 1 or page + step <= total):
        if page - step >= 1:
            order.append(page - step)
        if page + step <= total and len(order) < limit:
            order.append(page + step)
        step += 1
    return order


def locate_value_in_document(
    doc_path: Path,
    page: int,
    values: list[str],
    *,
    also: list[str] | None = None,
    anchors: list[str] | None = None,
    max_scan_pages: int | None = None,
) -> dict[str, Any]:
    """Find the VALUE first, everything else after — {found, polygons, page}.

    1. the value (any of its renderings) on the cited page, then on its
       nearest neighbours (bounded by LOCATE_MAX_PAGES; one disk-cached OCR
       per page) — never the whole document: the page comes from the
       extractor's provenance, so a miss is an off-by-one, not unknown;
    2. `also` — the row's item code etc. — on the CITED PAGE ONLY: it
       confirms the row when the number itself could not be matched, and
       never chooses a page (an item code like "2.14" matches clause
       numbering on unrelated pages — the live-demo blunder);
    3. `anchors` — the verbatim clause / excerpt — last, as a value in its
       own right, on the cited page and neighbours. While looking for the
       value they only disambiguate between several occurrences on a page.
    """
    scan_ok = doc_path.suffix.lower() not in _IMAGE_EXTS
    budget = get_settings().locate_max_pages if max_scan_pages is None else max_scan_pages
    pages = scan_order(page, _page_count(doc_path), budget) if scan_ok else [page]

    def find(terms: list[str], on: list[int], *, anchors_: list[str] | None = None) -> dict[str, Any] | None:
        if not terms or not _locatable(terms):
            return None
        for p in on:
            r = locate_value_on_page(doc_path, p, terms, anchors=anchors_)
            if r["found"]:
                return {**r, "page": p}
        return None

    hit = find(values, pages, anchors_=anchors)
    if hit:
        return hit
    hit = find(list(also or []), pages[:1])
    if hit:
        return hit
    hit = find(list(anchors or []), pages)
    if hit:
        return hit
    return {"found": False, "polygons": [], "page": pages[0]}
