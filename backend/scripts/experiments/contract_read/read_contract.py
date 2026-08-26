"""Experiment: read a long contract with the GPT vision model directly — no Azure CU.

Map/reduce over pages:
  1. pages -> batches (text layer when present, rendered JPEG otherwise)
  2. one model call per batch -> findings with verbatim evidence + page number
  3. one merge call -> consolidated contract record, BoQ lines, conflicts, missing fields

Writes JSON + a Markdown report + token/cost accounting under backend/.experiments/.
Not wired into the app. Usage (from backend/):

  .venv/Scripts/python scripts/experiments/contract_read/read_contract.py \
      "../supporting_docs/contracts/hhc/RCU - HHC - Service  Supply Contract - Final 12.11.2024.pdf" \
      --pages 1-20 --batch 8

  --mode auto|vision|text   auto = text layer if the page has one, else image (default)
  --model <deployment>      defaults to AZURE_OPENAI_MODEL
  --price-in / --price-out  USD per 1M tokens, for the cost line (optional)
  --concurrency N           parallel batch calls (default 4)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE))

from openai import OpenAI  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.extraction.retry import with_retries  # noqa: E402
from pages import Page, load_pages, page_count, parse_page_spec  # noqa: E402
from prompts import BATCH_SYSTEM, MERGE_SYSTEM  # noqa: E402

OUT_ROOT = BACKEND / ".experiments" / "contract_read"


def _json_loads(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def _batch_content(pages: list[Page]) -> list[dict]:
    parts: list[dict] = []
    for p in pages:
        parts.append({"type": "text", "text": f"PAGE {p.number} ({p.kind})"})
        if p.text is not None:
            parts.append({"type": "text", "text": p.text})
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{p.image_b64}", "detail": "high"},
            })
    return parts


class Usage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add(self, label: str, resp, seconds: float) -> None:
        u = resp.usage
        self.calls.append({
            "label": label,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "seconds": round(seconds, 1),
        })

    def totals(self) -> dict:
        return {
            "calls": len(self.calls),
            "prompt_tokens": sum(c["prompt_tokens"] for c in self.calls),
            "completion_tokens": sum(c["completion_tokens"] for c in self.calls),
            "seconds": round(sum(c["seconds"] for c in self.calls), 1),
        }


def call_model(client: OpenAI, model: str, system: str, content, usage: Usage, label: str) -> dict:
    def _do():
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        usage.add(label, resp, time.time() - t0)
        return _json_loads(resp.choices[0].message.content or "{}")

    return with_retries(_do)


def read_batch(client, model, pdf, numbers, mode, scale, usage, idx) -> dict:
    pages = load_pages(str(pdf), numbers, mode=mode, scale=scale)
    kinds = "+".join(sorted({p.kind for p in pages}))
    label = f"batch{idx:02d} p{numbers[0]}-{numbers[-1]} ({kinds})"
    print(f"  -> {label}", flush=True)
    try:
        data = call_model(client, model, BATCH_SYSTEM, _batch_content(pages), usage, label)
    except Exception as exc:  # keep going; report the hole
        print(f"  !! {label} failed: {exc}", flush=True)
        return {"findings": [], "page_summaries": [], "error": str(exc), "pages": numbers}
    data.setdefault("findings", [])
    data.setdefault("page_summaries", [])
    for f in data["findings"]:
        f.setdefault("page", numbers[0])
    data["pages"] = numbers
    return data


def merge(client, model, batches, usage) -> dict:
    findings = [f for b in batches for f in b["findings"]]
    summaries = [s for b in batches for s in b["page_summaries"]]
    payload = json.dumps({"findings": findings, "page_summaries": summaries}, ensure_ascii=False)
    return call_model(client, model, MERGE_SYSTEM, payload, usage, "merge")


def _cell(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s.replace("|", "/").replace("\n", " ")


def write_report(out_dir: Path, pdf: Path, merged: dict, batches: list[dict], usage: Usage, args) -> Path:
    tot = usage.totals()
    cost = None
    if args.price_in is not None and args.price_out is not None:
        cost = tot["prompt_tokens"] / 1e6 * args.price_in + tot["completion_tokens"] / 1e6 * args.price_out
    lines = [
        f"# Contract read — {pdf.name}",
        "",
        f"- model: `{args.model}` · mode: `{args.mode}` · pages: {args.pages or 'all'} · batch: {args.batch}",
        f"- calls: {tot['calls']} · prompt tokens: {tot['prompt_tokens']:,} · "
        f"completion tokens: {tot['completion_tokens']:,} · model time: {tot['seconds']}s"
        + (f" · est. cost: ${cost:.3f}" if cost is not None else ""),
        "",
        "## Contract record",
        "",
        "| field | value | pages | conf | evidence |",
        "|---|---|---|---|---|",
    ]
    for k, v in (merged.get("contract") or {}).items():
        if isinstance(v, dict):
            val, ev, pgs, conf = v.get("value"), v.get("evidence", ""), v.get("pages", []), v.get("confidence", "")
        else:
            val, ev, pgs, conf = v, "", [], ""
        lines.append(f"| {k} | {_cell(val)} | {','.join(map(str, pgs))} | {conf} | {_cell(ev)[:200]} |")
    boq = merged.get("boq_lines") or []
    lines += [
        "", f"## BoQ lines ({len(boq)})", "",
        "| # | description | unit | qty | unit price | total | page |",
        "|---|---|---|---|---|---|---|",
    ]
    for line in boq[:200]:
        lines.append(
            f"| {_cell(line.get('item_no', ''))} | {_cell(line.get('description', ''))[:80]} | "
            f"{_cell(line.get('unit', ''))} | {_cell(line.get('quantity', ''))} | "
            f"{_cell(line.get('unit_price', ''))} | {_cell(line.get('total', ''))} | {line.get('page', '')} |"
        )
    if len(boq) > 200:
        lines.append(f"| … | {len(boq) - 200} more lines in merged.json | | | | | |")
    lines += ["", "## Missing", "", ", ".join(merged.get("missing") or []) or "—"]
    lines += ["", "## Conflicts", ""]
    for c in merged.get("conflicts") or []:
        lines.append(f"- **{c.get('field')}** → {c.get('resolution', '')}")
        for cand in c.get("candidates", []):
            lines.append(f"  - p{cand.get('page')}: {_cell(cand.get('value'))} — _{_cell(cand.get('evidence', ''))[:120]}_")
    lines += ["", "## Document map", ""]
    for d in merged.get("document_map") or []:
        lines.append(f"- p{d.get('pages')}: **{d.get('kind')}** — {d.get('summary', '')}")
    failed = [b for b in batches if b.get("error")]
    if failed:
        lines += ["", "## Failed batches", ""]
        lines += [f"- pages {b['pages'][0]}-{b['pages'][-1]}: {b['error']}" for b in failed]
    lines += ["", "## Calls", "", "| call | prompt | completion | s |", "|---|---|---|---|"]
    lines += [f"| {c['label']} | {c['prompt_tokens']:,} | {c['completion_tokens']:,} | {c['seconds']} |" for c in usage.calls]
    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="e.g. 1-20,45,100-")
    ap.add_argument("--batch", type=int, default=8, help="pages per model call")
    ap.add_argument("--mode", choices=["auto", "vision", "text"], default="auto")
    ap.add_argument("--scale", type=float, default=1.5, help="render scale (1.0 = 72 dpi)")
    ap.add_argument("--model")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--price-in", type=float, help="USD per 1M prompt tokens")
    ap.add_argument("--price-out", type=float, help="USD per 1M completion tokens")
    ap.add_argument("--no-merge", action="store_true", help="skip the consolidation call")
    args = ap.parse_args()

    os.chdir(BACKEND)  # so .env resolves like the app
    s = get_settings()
    args.model = args.model or s.azure_openai_model
    if not s.azure_openai_api_key:
        sys.exit("AZURE_OPENAI_API_KEY not set in backend/.env")
    client = OpenAI(api_key=s.azure_openai_api_key, base_url=s.azure_openai_base_url)

    pdf = Path(args.pdf).resolve()
    total = page_count(str(pdf))
    numbers = parse_page_spec(args.pages, total)
    batches_spec = [numbers[i : i + args.batch] for i in range(0, len(numbers), args.batch)]
    print(f"{pdf.name}: {total} pages, reading {len(numbers)} in {len(batches_spec)} batches with {args.model}")

    usage = Usage()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        batches = list(pool.map(
            lambda ib: read_batch(client, args.model, pdf, ib[1], args.mode, args.scale, usage, ib[0]),
            enumerate(batches_spec),
        ))
    merged = {} if args.no_merge else merge(client, args.model, batches, usage)
    wall = time.time() - t0

    stem = "".join(ch if ch.isalnum() else "_" for ch in pdf.stem)[:60]
    out_dir = OUT_ROOT / f"{stem}__{args.mode}_p{numbers[0]}-{numbers[-1]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "batches.json").write_text(json.dumps(batches, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "merged.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "usage.json").write_text(
        json.dumps({"totals": usage.totals(), "wall_seconds": round(wall, 1), "calls": usage.calls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = write_report(out_dir, pdf, merged, batches, usage, args)
    tot = usage.totals()
    print(f"\ndone in {wall:.0f}s · {tot['prompt_tokens']:,} in / {tot['completion_tokens']:,} out over {tot['calls']} calls")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
