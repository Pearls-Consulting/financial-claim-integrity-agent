"""Survey the candidate contracts: pages, text-layer vs scanned, and a rough
token budget for a full vision read — before spending anything.

  .venv/Scripts/python scripts/experiments/contract_read/survey.py ../supporting_docs/contracts
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pypdfium2 as pdfium  # noqa: E402

from pages import MIN_TEXT_CHARS  # noqa: E402

# Rough per-page prompt cost: a detail=high A4 image ~1,100-1,600 tokens on
# GPT-4o-class tokenisers; an Arabic text page ~600-1,200 tokens.
IMG_TOK, TXT_TOK = 1400, 900


def main(root: str) -> None:
    rows = []
    files = sorted({p for p in Path(root).rglob("*") if p.suffix.lower() == ".pdf"})
    for p in files:
        pdf = pdfium.PdfDocument(str(p))
        n = len(pdf)
        sample = range(0, n, max(1, n // 12))  # ~12 pages spread across the doc
        text_pages = 0
        for i in sample:
            tp = pdf[i].get_textpage()
            if len((tp.get_text_bounded() or "").strip()) >= MIN_TEXT_CHARS:
                text_pages += 1
            tp.close()
        pdf.close()
        text_ratio = text_pages / len(sample)
        est = int(n * (text_ratio * TXT_TOK + (1 - text_ratio) * IMG_TOK))
        rows.append((n, text_ratio, est, f"{p.parent.name}/{p.name}"))
    print(f"{'pages':>5}  {'text%':>5}  {'~prompt tok':>11}  file")
    for n, r, est, name in rows:
        print(f"{n:>5}  {r * 100:>4.0f}%  {est:>11,}  {name}")
    print(f"\n{sum(r[0] for r in rows)} pages total, ~{sum(r[2] for r in rows):,} prompt tokens for a full read of everything")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../supporting_docs/contracts")
