"""One process-wide lock for every PDFium call.

PDFium is not thread-safe: two threads opening or rendering documents at the
same time race on its global state and fail ("Data format error" on a valid
file) or, worse, hand back silently corrupted bitmaps that the reader then
transcribes with confidence. Measured here with 16 reader workers on one
scanned contract. The prequalification agent serialised PDFium behind a lock
for the same reason; this is that lock, shared by the GPT reader (page
renders), the evidence locate (one-page renders), and the QR decoder.

Everything outside PDFium — encoding, base64, the model call — runs outside
the lock, so concurrency is only lost for the milliseconds a page takes to
rasterize.
"""

from __future__ import annotations

import threading

PDFIUM_LOCK = threading.RLock()
