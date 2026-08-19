"""Azure Content Understanding (Layout) client.

Stage 1 of the extraction pipeline: CU 'prebuilt-layout' reads a document into
faithful markdown (accurate Arabic-Indic digits, tables, structure). That
markdown is fed to GPT for field structuring — GPT never re-reads the pixels.

Ported from pre-qualification-agent (services/analyzer/cu_client.py), trimmed
to what this project needs. Results are cached on disk by content hash so
repeated demo runs don't re-OCR (or re-bill) the same file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.extraction.retry import with_retries

_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "cu"


@dataclass
class CUResult:
    document: str
    ok: bool
    pages: int
    markdown: str = ""
    error: str = ""


def _build_client():
    from azure.ai.contentunderstanding import ContentUnderstandingClient
    from azure.core.credentials import AzureKeyCredential

    s = get_settings()
    if not (s.azure_cu_endpoint and s.azure_cu_key):
        raise RuntimeError("Missing CU config: set AZURE_CU_ENDPOINT and AZURE_CU_KEY.")
    return ContentUnderstandingClient(
        endpoint=s.azure_cu_endpoint,
        credential=AzureKeyCredential(s.azure_cu_key),
        api_version=s.azure_cu_api_version,
    )


def analyze_layout(path: Path, *, client: Any = None) -> CUResult:
    try:
        data = path.read_bytes()
    except Exception as exc:
        return CUResult(document=path.name, ok=False, pages=0, error=f"reading file failed: {exc}")

    digest = hashlib.sha1(data).hexdigest()
    cache_file = _CACHE_DIR / f"{digest}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return CUResult(**cached)

    client = client or _build_client()
    content_type = _MIME.get(path.suffix.lower(), "application/octet-stream")

    def _call() -> Any:
        poller = client.begin_analyze_binary(
            analyzer_id=get_settings().azure_cu_analyzer,
            binary_input=data,
            content_type=content_type,
        )
        return poller.result()

    try:
        result = with_retries(_call)
    except Exception as exc:
        return CUResult(document=path.name, ok=False, pages=0, error=f"OCR failed: {exc}")

    markdown_parts: list[str] = []
    pages = 0
    for content in getattr(result, "contents", None) or []:
        md = getattr(content, "markdown", None)
        if md:
            markdown_parts.append(md)
        cpages = getattr(content, "pages", None)
        if cpages:
            pages += len(cpages)

    out = CUResult(
        document=path.name,
        ok=bool(markdown_parts),
        pages=pages,
        markdown="\n\n".join(markdown_parts),
        error="" if markdown_parts else "no markdown content returned",
    )
    if out.ok:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out.__dict__, ensure_ascii=False), encoding="utf-8")
    return out
