"""PDF -> per-page payloads for a vision/LLM read. No Azure CU involved.

Each page becomes either its text layer (when the PDF has a usable one — free
and exact) or a rendered JPEG (scanned pages). pypdfium2 only (BSD), no PyMuPDF.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import pypdfium2 as pdfium

MIN_TEXT_CHARS = 200  # below this a page is treated as scanned


@dataclass
class Page:
    number: int  # 1-based, as printed in the evidence
    text: str | None  # text layer, or None when scanned
    image_b64: str | None  # JPEG data when rendered

    @property
    def kind(self) -> str:
        return "text" if self.text is not None else "image"


def page_count(path: str) -> int:
    pdf = pdfium.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def load_pages(
    path: str,
    numbers: list[int],
    *,
    mode: str = "auto",
    scale: float = 1.5,
    jpeg_quality: int = 72,
) -> list[Page]:
    """mode: auto (text layer when present, else image) | text | vision."""
    pdf = pdfium.PdfDocument(path)
    out: list[Page] = []
    try:
        for n in numbers:
            page = pdf[n - 1]
            text = None
            if mode in ("auto", "text"):
                tp = page.get_textpage()
                raw = tp.get_text_bounded() or ""
                tp.close()
                if mode == "text" or len(raw.strip()) >= MIN_TEXT_CHARS:
                    text = raw
            image_b64 = None
            if text is None:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil().convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            out.append(Page(number=n, text=text, image_b64=image_b64))
            page.close()
    finally:
        pdf.close()
    return out


def parse_page_spec(spec: str | None, total: int) -> list[int]:
    """'1-10,15,40-' -> [1..10, 15, 40..total]; None -> all."""
    if not spec:
        return list(range(1, total + 1))
    nums: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else total
            nums.extend(range(start, min(end, total) + 1))
        else:
            nums.append(int(part))
    return sorted({n for n in nums if 1 <= n <= total})
