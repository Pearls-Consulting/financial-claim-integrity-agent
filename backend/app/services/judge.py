"""Judge seam — deterministic findings in, recommendation out.

The judge NEVER overrides a deterministic failure; it explains, prioritizes
and drafts the rejection reason. The human keeps approve/reject authority
(government context: the agent recommends with citations, it does not decide).

The mock derives the verdict purely from finding severities — enough for the
demo, and the honest baseline the LLM judge must beat.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.domain.models import Claim, GateRun, RunResult, Severity, Verdict

# Identical findings -> identical write-up: rationales are cached on disk keyed
# by (prompt, model, payload), so a re-scan of the same documents skips the
# model. claim_id is deliberately NOT part of the payload/key.
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "judge"


class Judge(Protocol):
    def judge(self, claim: Claim, gates: list[GateRun]) -> RunResult: ...


class MockJudge:
    def judge(self, claim: Claim, gates: list[GateRun]) -> RunResult:
        failed = [f for g in gates for f in g.findings if f.severity is Severity.fail]
        warned = [f for g in gates for f in g.findings if f.severity is Severity.warn]
        if failed:
            verdict = Verdict.reject
            rationale_en = "Reject: " + "; ".join(f.title_en for f in failed)
            rationale_ar = "مرفوضة: " + "؛ ".join(f.title_ar for f in failed)
        elif warned:
            verdict = Verdict.needs_human
            rationale_en = "Review needed: " + "; ".join(f.title_en for f in warned)
            rationale_ar = "تحتاج مراجعة: " + "؛ ".join(f.title_ar for f in warned)
        else:
            verdict = Verdict.approve
            rationale_en = "All deterministic checks passed across every gate."
            rationale_ar = "اجتازت المطالبة جميع الفحوصات في كل البوابات."
        return RunResult(
            claim_id=claim.id,
            gates=gates,
            verdict=verdict,
            rationale_en=rationale_en,
            rationale_ar=rationale_ar,
        )


class GptJudge:
    """GPT (Azure OpenAI) reasons over the deterministic findings.

    The verdict FLOOR comes from MockJudge — a deterministic failure can never
    be upgraded past needs_human by the model. GPT's added value is the
    write-up: prioritized bilingual rationale and a drafted rejection message
    in the wording the team sends vendors.
    """

    def judge(self, claim: Claim, gates: list[GateRun]) -> RunResult:
        from openai import OpenAI

        from app.core.config import get_settings as _s

        baseline = MockJudge().judge(claim, gates)
        findings = [
            {
                "gate": g.gate,
                "rule": f.rule_id,
                "severity": f.severity.value,
                "title_en": f.title_en,
                "detail_en": f.detail_en,
                "source": f"{f.source.doc} {f.source.ref}".strip(),
            }
            for g in gates
            for f in g.findings
        ]
        system = (
            "You are the review assistant for vendor payment claims at a Saudi government entity. "
            "You are given DETERMINISTIC check results (ground truth — never dispute or soften them) "
            "for one claim. Write the reviewer-facing recommendation.\n"
            "Return ONLY JSON: {\"rationale_en\": str, \"rationale_ar\": str}.\n"
            "rationale_en / rationale_ar: 2-4 sentences each, professional register; lead with the "
            "decisive findings, cite their sources, and if the claim fails include the exact "
            "correction the vendor or team must make. Arabic must be native procurement wording, "
            "not a literal translation."
        )
        payload = {
            "claim_type": claim.claim_type.value,
            "payment_no": claim.payment_no,
            "verdict_floor": baseline.verdict.value,
            "findings": findings,
        }
        settings = _s()

        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(f"{system}\x00{settings.azure_openai_model}\x00{payload_json}".encode()).hexdigest()
        cache_file = _CACHE_DIR / f"{digest}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                baseline.rationale_en = str(cached.get("rationale_en") or baseline.rationale_en)
                baseline.rationale_ar = str(cached.get("rationale_ar") or baseline.rationale_ar)
                return baseline
            except Exception:
                pass  # corrupt cache — fall through and re-ask the model

        client = OpenAI(api_key=settings.azure_openai_api_key, base_url=settings.azure_openai_base_url)
        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": payload_json},
            ]
            # The write-up is presentation, not analysis: low reasoning effort
            # keeps the call short. Fall back to the default if the deployment
            # rejects the parameter.
            try:
                response = client.chat.completions.create(
                    model=settings.azure_openai_model,
                    messages=messages,
                    **({"reasoning_effort": settings.gpt_judge_effort} if settings.gpt_judge_effort else {}),
                )
            except Exception:
                if not settings.gpt_judge_effort:
                    raise
                response = client.chat.completions.create(model=settings.azure_openai_model, messages=messages)
            raw = (response.choices[0].message.content or "").strip()
            start, end = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[start : end + 1])
            baseline.rationale_en = str(data.get("rationale_en") or baseline.rationale_en)
            baseline.rationale_ar = str(data.get("rationale_ar") or baseline.rationale_ar)
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps(
                        {"rationale_en": baseline.rationale_en, "rationale_ar": baseline.rationale_ar},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
        except Exception:
            # The judge is presentation-layer: on any model failure the
            # deterministic baseline rationale ships unchanged.
            import logging

            logging.getLogger(__name__).warning("GPT judge failed; using baseline rationale", exc_info=True)
        return baseline


def get_judge() -> Judge:
    engine = get_settings().judge_engine
    if engine == "mock":
        return MockJudge()
    if engine in ("gpt", "azure"):
        return GptJudge()
    # claude judge: port from pre-qualification-agent backend/app/services/judge
    raise NotImplementedError(f"Judge engine '{engine}' not implemented yet (mock | gpt)")
