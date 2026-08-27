"""Decode QR / barcode payloads from PDFs — deterministic, offline, no LLM.

Ported from pre-qualification-agent (services/qr_extraction.py). pypdfium2
(BSD-3) renders pages to grayscale; zxing-cpp (Apache-2.0) decodes symbols.
For a compliant فاتورة ضريبية the QR text IS the base64 ZATCA TLV payload,
which feeds validators/zatca_qr.py directly.

PDFium is NOT thread-safe (global C state) — all pdfium work is serialized
under a process-wide lock; zxing decodes outside it on copied arrays.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.extraction.pdfium_lock import PDFIUM_LOCK

logger = logging.getLogger(__name__)

_PDFIUM_LOCK = PDFIUM_LOCK  # shared with the GPT reader and the evidence locate
_RENDER_DPI = 300  # government QRs are small; 300 DPI reads reliably where 200 drops some
_MAX_PAGES = 12


@dataclass
class QrHit:
    payload: str
    symbology: str
    source_file: str
    page: int


def extract_from_pdf(path: str | Path, *, max_pages: int = _MAX_PAGES) -> list[QrHit]:
    """Decode every barcode in one PDF. Best-effort: logs and returns [] on failure."""
    import pypdfium2 as pdfium
    import zxingcpp

    src = Path(path).name
    rendered: list[tuple[int, Any]] = []
    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception:
            logger.warning("QR extraction: could not open %s", path, exc_info=True)
            return []
        try:
            scale = _RENDER_DPI / 72.0
            for index in range(min(len(pdf), max_pages)):
                page = pdf[index]
                try:
                    bitmap = page.render(scale=scale, grayscale=True)
                    array = bitmap.to_numpy().copy()
                    bitmap.close()
                except Exception:
                    logger.warning("QR extraction: render failed on %s p%d", src, index + 1, exc_info=True)
                    continue
                finally:
                    page.close()
                rendered.append((index + 1, array))
        finally:
            pdf.close()

    found: list[QrHit] = []
    seen: set[tuple[int, str]] = set()
    for page_no, array in rendered:
        for sym in zxingcpp.read_barcodes(array):
            text = (sym.text or "").strip()
            if not text or (page_no, text) in seen:
                continue
            seen.add((page_no, text))
            found.append(QrHit(payload=text, symbology=str(sym.format), source_file=src, page=page_no))
    return found
