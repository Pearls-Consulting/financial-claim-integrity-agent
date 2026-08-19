"""Rules-as-data engine.

The procedure document (SP-01-04-05-02) defines only the choreography — the
actual validation rules live in law, ZATCA regulations, the contract/BoQ and
the client policy (POC-P01, not yet provided). So rules are DATA: each gate
has a YAML rulepack whose entries cite their normative source, and each rule
points at a deterministic check implemented here. Editing/adding rules should
never require touching the pipeline.

A check returns ok=True/False, or None for "not applicable" (skipped, not
counted against the claim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from app.domain.models import Claim, ClaimType, Finding, GateRun, RuleSource, Severity
from app.services.validators import zatca_qr

RULEPACK_DIR = Path(__file__).parent / "rulepacks"


@dataclass
class CheckOutcome:
    ok: bool | None
    detail_en: str = ""
    detail_ar: str = ""
    evidence: dict = field(default_factory=dict)
    # A check whose failure modes differ in gravity can override the rulepack's
    # severity for this outcome (e.g. phase-2 QR: absent=warn, tampered=fail).
    severity_override: Severity | None = None


CheckFn = Callable[[Claim, dict], CheckOutcome]
CHECKS: dict[str, CheckFn] = {}


def check(name: str) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        CHECKS[name] = fn
        return fn

    return register


# --------------------------------------------------------------------------
# Intake checks
# --------------------------------------------------------------------------


@check("metadata_complete")
def metadata_complete(claim: Claim, params: dict) -> CheckOutcome:
    missing = [f for f in params.get("fields", []) if not getattr(claim, f, None)]
    return CheckOutcome(
        ok=not missing,
        detail_en=f"Missing vendor-entered fields: {', '.join(missing)}" if missing else "All claim metadata present.",
        detail_ar=f"حقول ناقصة: {', '.join(missing)}" if missing else "بيانات المطالبة مكتملة.",
        evidence={"missing": missing},
    )


def _bidi_variants(value: str) -> set[str]:
    """Reading-order variants of an identifier extracted from Arabic documents.

    OCR reads pixels in visual order, so a Latin identifier inside an RTL line
    comes out segment-reversed ('INV-2026-0117' -> '0117-2026-INV') even when
    the PDF text layer is correct. Deterministic normalization — never the
    LLM's job to 'fix' digits."""
    variants = {value}
    for sep in ("-", "/"):
        if sep in value:
            variants.add(sep.join(reversed(value.split(sep))))
    return variants


@check("invoice_no_matches")
def invoice_no_matches(claim: Claim, params: dict) -> CheckOutcome:
    inv = claim.documents.invoice
    if inv is None:
        return CheckOutcome(ok=False, detail_en="No invoice document on the claim.", detail_ar="لا توجد فاتورة مرفقة بالمطالبة.")
    exact = inv.invoice_no == claim.invoice_no
    normalized = not exact and claim.invoice_no in _bidi_variants(inv.invoice_no)
    ok = exact or normalized
    note = " (matched after RTL reading-order normalization)" if normalized else ""
    return CheckOutcome(
        ok=ok,
        detail_en=f"Claim says '{claim.invoice_no}', invoice document says '{inv.invoice_no}'.{note}",
        detail_ar=f"المطالبة: '{claim.invoice_no}'، الفاتورة: '{inv.invoice_no}'." + (" (تطابق بعد تصحيح اتجاه القراءة)" if normalized else ""),
        evidence={"claim": claim.invoice_no, "invoice": inv.invoice_no, "bidi_normalized": normalized},
    )


@check("vat_math")
def vat_math(claim: Claim, params: dict) -> CheckOutcome:
    expected = round(claim.claim_amount_base + claim.vat_amount, 2)
    ok = abs(expected - claim.claim_amount_total) <= 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=f"base {claim.claim_amount_base:,.2f} + VAT {claim.vat_amount:,.2f} = {expected:,.2f}; claim total is {claim.claim_amount_total:,.2f}.",
        detail_ar=f"الأساس {claim.claim_amount_base:,.2f} + الضريبة {claim.vat_amount:,.2f} = {expected:,.2f}؛ إجمالي المطالبة {claim.claim_amount_total:,.2f}.",
        evidence={"expected": expected, "actual": claim.claim_amount_total},
    )


@check("qr_present")
def qr_present(claim: Claim, params: dict) -> CheckOutcome:
    inv = claim.documents.invoice
    if inv is not None and inv.vat_exempt:
        return CheckOutcome(ok=None, detail_en="VAT-exempt invoice — QR requirement not applied.", detail_ar="فاتورة معفاة من الضريبة — لا يُطبق شرط رمز الاستجابة.")
    ok = bool(inv and inv.qr_payload)
    return CheckOutcome(
        ok=ok,
        detail_en="Invoice carries a QR payload." if ok else "No QR code found on the invoice — not a compliant tax invoice.",
        detail_ar="الفاتورة تحمل رمز استجابة." if ok else "لا يوجد رمز استجابة على الفاتورة — ليست فاتورة ضريبية نظامية.",
    )


@check("qr_authentic")
def qr_authentic(claim: Claim, params: dict) -> CheckOutcome:
    inv = claim.documents.invoice
    if inv is None or not inv.qr_payload:
        return CheckOutcome(ok=None, detail_en="No QR to verify.", detail_ar="لا يوجد رمز للتحقق منه.")
    decoded = zatca_qr.decode_tlv(inv.qr_payload)
    if not decoded.valid_tlv:
        return CheckOutcome(
            ok=False,
            detail_en=f"QR payload is not valid ZATCA TLV ({decoded.error}).",
            detail_ar="محتوى رمز الاستجابة ليس بصيغة هيئة الزكاة والضريبة النظامية.",
            evidence={"error": decoded.error},
        )
    problems: list[str] = []
    f = decoded.fields
    if not zatca_qr.vat_number_ok(f.get("vat_number", "")):
        problems.append(f"VAT number '{f.get('vat_number', '')}' is not a valid 15-digit ZATCA number")
    if inv.seller_vat_number and f.get("vat_number") != inv.seller_vat_number:
        problems.append("QR VAT number differs from the invoice face")
    if not zatca_qr.amounts_match(f.get("total", ""), inv.total_with_vat):
        problems.append(f"QR total {f.get('total')} differs from invoice total {inv.total_with_vat:,.2f}")
    if not zatca_qr.amounts_match(f.get("vat", ""), inv.vat_amount):
        problems.append(f"QR VAT {f.get('vat')} differs from invoice VAT {inv.vat_amount:,.2f}")
    return CheckOutcome(
        ok=not problems,
        detail_en="; ".join(problems) if problems else "QR decodes and matches the invoice (seller VAT, totals).",
        detail_ar="رمز الاستجابة لا يطابق بيانات الفاتورة — يُشتبه بأنها فاتورة غير ضريبية." if problems else "رمز الاستجابة سليم ويطابق بيانات الفاتورة.",
        evidence={"qr_fields": f, "problems": problems},
    )


@check("qr_phase2")
def qr_phase2(claim: Claim, params: dict) -> CheckOutcome:
    """Tiered by design, NOT a strict requirement: phase-2 obligation arrives
    per-vendor by ZATCA integration wave, which a receiver cannot determine
    offline. Absent/pseudo material warns and routes to the Fatoora-app check;
    only well-formed cryptography that fails verification is a hard fail."""
    inv = claim.documents.invoice
    if inv is None or not inv.qr_payload:
        return CheckOutcome(ok=None, detail_en="No QR to assess.", detail_ar="لا يوجد رمز للتقييم.")
    result = zatca_qr.validate_phase2(inv.qr_payload)
    evidence = {"status": result.status, "problems": result.problems, "has_zatca_stamp": result.has_stamp}
    if result.notes:
        evidence["notes"] = result.notes
    if result.status == "valid":
        notes = [] if result.has_stamp else ["ZATCA stamp tag absent — attestation not confirmed"]
        notes += [n for n in result.notes if "secp256k1" in n]
        note = f" ({'; '.join(notes)})" if notes else ""
        return CheckOutcome(
            ok=True,
            detail_en=f"Phase-2 signature verifies over the invoice hash{note}.",
            detail_ar="التوقيع الرقمي للمرحلة الثانية صحيح ومطابق لبصمة الفاتورة.",
            evidence=evidence,
        )
    if result.status == "invalid_signature":
        return CheckOutcome(
            ok=False,
            severity_override=Severity.fail,
            detail_en="Phase-2 material is well-formed but the ECDSA signature does NOT verify — tampering indicator.",
            detail_ar="بيانات المرحلة الثانية سليمة الشكل لكن التوقيع الرقمي غير صحيح — مؤشر تلاعب.",
            evidence=evidence,
        )
    if result.status == "pseudo":
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en=(
                "QR carries imitation phase-2 tags that are not real cryptographic material "
                f"({'; '.join(result.problems)}). Confirm the invoice against ZATCA records via the Fatoora app."
            ),
            detail_ar="يحمل الرمز وسوماً تحاكي المرحلة الثانية دون مواد تشفير حقيقية — يُتحقق من الفاتورة عبر تطبيق فاتورة.",
            evidence=evidence,
        )
    return CheckOutcome(  # absent — clean phase-1
        ok=False,
        severity_override=Severity.warn,
        detail_en=(
            "Phase-1 QR only (no cryptographic attestation). Acceptable if the vendor is not yet in a "
            "ZATCA integration wave — confirm via the Fatoora app; acceptance policy pending POC-P01."
        ),
        detail_ar="رمز من المرحلة الأولى فقط دون توثيق تشفيري — يُتحقق عبر تطبيق فاتورة، وقبوله قرار سياسة معلق على POC-P01.",
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# BoQ / contract checks
# --------------------------------------------------------------------------


@check("boq_lines_match")
def boq_lines_match(claim: Claim, params: dict) -> CheckOutcome:
    inv = claim.documents.invoice
    boq = {line.item_code: line for line in claim.documents.boq}
    if inv is None or not inv.lines:
        return CheckOutcome(ok=None, detail_en="No invoice lines to match.", detail_ar="لا توجد بنود فاتورة للمطابقة.")
    mismatches: list[dict] = []
    for line in inv.lines:
        ref = boq.get(line.item_code)
        if ref is None:
            mismatches.append({"item": line.item_code, "issue": "not in BoQ"})
        elif abs(ref.unit_price - line.unit_price) > 0.01:
            mismatches.append({"item": line.item_code, "issue": "unit price", "boq": ref.unit_price, "invoice": line.unit_price})
    return CheckOutcome(
        ok=not mismatches,
        detail_en=f"{len(mismatches)} invoice line(s) deviate from the BoQ." if mismatches else f"All {len(inv.lines)} invoice lines match the BoQ.",
        detail_ar=f"{len(mismatches)} بند/بنود لا تطابق جدول الكميات." if mismatches else "جميع بنود الفاتورة مطابقة لجدول الكميات.",
        evidence={"mismatches": mismatches},
    )


@check("cumulative_within_contract")
def cumulative_within_contract(claim: Claim, params: dict) -> CheckOutcome:
    cumulative = round(claim.cumulative_prior + claim.claim_amount_total, 2)
    ok = cumulative <= claim.contract_value + 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=f"Cumulative claimed {cumulative:,.2f} vs contract value {claim.contract_value:,.2f}.",
        detail_ar=f"إجمالي المطالبات {cumulative:,.2f} مقابل قيمة العقد {claim.contract_value:,.2f}.",
        evidence={"cumulative": cumulative, "contract_value": claim.contract_value},
    )


@check("payment_sequence")
def payment_sequence(claim: Claim, params: dict) -> CheckOutcome:
    expected = claim.prior_payment_count + 1
    ok = claim.payment_no == expected
    return CheckOutcome(
        ok=ok,
        detail_en=f"Payment no. {claim.payment_no}; expected {expected} after {claim.prior_payment_count} prior payment(s).",
        detail_ar=f"رقم الدفعة {claim.payment_no}؛ المتوقع {expected} بعد {claim.prior_payment_count} دفعة/دفعات سابقة.",
        evidence={"payment_no": claim.payment_no, "expected": expected},
    )


@check("final_claim_closes_contract")
def final_claim_closes_contract(claim: Claim, params: dict) -> CheckOutcome:
    if claim.claim_type is not ClaimType.final:
        return CheckOutcome(ok=None, detail_en="Not a final claim.", detail_ar="ليست دفعة نهائية.")
    cumulative = round(claim.cumulative_prior + claim.claim_amount_total, 2)
    ok = abs(cumulative - claim.contract_value) <= 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=f"Final claim: cumulative {cumulative:,.2f} should equal contract value {claim.contract_value:,.2f}.",
        detail_ar=f"دفعة نهائية: الإجمالي {cumulative:,.2f} يجب أن يساوي قيمة العقد {claim.contract_value:,.2f}.",
        evidence={"cumulative": cumulative, "contract_value": claim.contract_value},
    )


# --------------------------------------------------------------------------
# Three-way match checks (contract/BoQ ↔ ERP product receipt ↔ invoice)
# --------------------------------------------------------------------------

_QTY_TOL = 1e-6


@check("receipt_present")
def receipt_present(claim: Claim, params: dict) -> CheckOutcome:
    rec = claim.documents.receipt
    if rec is None:
        return CheckOutcome(
            ok=False,
            detail_en="No product receipt (إيصال استلام) posted in the ERP for this claim — the three-way match cannot be completed.",
            detail_ar="لا يوجد إيصال استلام منتجات مسجل في النظام لهذه المطالبة — لا يمكن إتمام المطابقة الثلاثية.",
        )
    return CheckOutcome(
        ok=True,
        detail_en=f"Product receipt {rec.receipt_no} posted with {len(rec.lines)} line(s).",
        detail_ar=f"إيصال الاستلام {rec.receipt_no} مسجل ويتضمن {len(rec.lines)} بند/بنود.",
        evidence={"receipt_no": rec.receipt_no, "receipt_date": rec.receipt_date},
    )


@check("billed_vs_received")
def billed_vs_received(claim: Claim, params: dict) -> CheckOutcome:
    inv, rec = claim.documents.invoice, claim.documents.receipt
    if rec is None or inv is None or not inv.lines:
        return CheckOutcome(ok=None, detail_en="No receipt/invoice lines to reconcile.", detail_ar="لا توجد بنود للمطابقة.")
    received = {line.item_code: line.quantity for line in rec.lines}
    boq = {line.item_code: line for line in claim.documents.boq}
    rows: list[dict] = []
    over: list[str] = []
    for line in inv.lines:
        got = received.get(line.item_code, 0.0)
        row = {"item": line.item_code, "billed": line.quantity, "received": got}
        if line.item_code in boq:
            row["boq_qty"] = boq[line.item_code].quantity
        rows.append(row)
        if line.quantity > got + _QTY_TOL:
            over.append(f"{line.item_code}: billed {line.quantity:g} vs received {got:g}")
    return CheckOutcome(
        ok=not over,
        detail_en="Billed beyond the receipt — " + "; ".join(over) if over else f"All {len(inv.lines)} invoice line(s) billed within the received quantities.",
        detail_ar="فوترة تتجاوز الكميات المستلمة — " + "؛ ".join(over) if over else "جميع بنود الفاتورة ضمن الكميات المستلمة.",
        evidence={"lines": rows},
    )


@check("received_within_boq")
def received_within_boq(claim: Claim, params: dict) -> CheckOutcome:
    rec = claim.documents.receipt
    boq = {line.item_code: line for line in claim.documents.boq}
    if rec is None or not boq:
        return CheckOutcome(ok=None, detail_en="No receipt/BoQ to compare.", detail_ar="لا يوجد إيصال أو جدول كميات للمقارنة.")
    issues: list[str] = []
    for line in rec.lines:
        ref = boq.get(line.item_code)
        if ref is None:
            issues.append(f"{line.item_code}: received item not in the BoQ")
        elif line.quantity > ref.quantity + _QTY_TOL:
            issues.append(f"{line.item_code}: received {line.quantity:g} vs BoQ {ref.quantity:g}")
    return CheckOutcome(
        ok=not issues,
        detail_en="; ".join(issues) if issues else "All received quantities stay within the contracted BoQ.",
        detail_ar="؛ ".join(issues) if issues else "جميع الكميات المستلمة ضمن كميات جدول الكميات التعاقدية.",
        evidence={"issues": issues},
    )


@check("claimed_within_certified_value")
def claimed_within_certified_value(claim: Claim, params: dict) -> CheckOutcome:
    rec = claim.documents.receipt
    boq = {line.item_code: line for line in claim.documents.boq}
    if rec is None or not boq:
        return CheckOutcome(ok=None, detail_en="No receipt/BoQ to value.", detail_ar="لا يوجد إيصال أو جدول كميات للتقييم.")
    certified = round(sum(line.quantity * boq[line.item_code].unit_price for line in rec.lines if line.item_code in boq), 2)
    ok = claim.claim_amount_base <= certified + 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=f"Claimed (excl. VAT) {claim.claim_amount_base:,.2f} vs value of received work at BoQ prices {certified:,.2f}.",
        detail_ar=f"المطالبة (بدون الضريبة) {claim.claim_amount_base:,.2f} مقابل قيمة الأعمال المستلمة بأسعار الجدول {certified:,.2f}.",
        evidence={"claimed_base": claim.claim_amount_base, "certified_value": certified},
    )


# --------------------------------------------------------------------------
# COC consistency checks
# --------------------------------------------------------------------------


@check("coc_amount_matches_claim")
def coc_amount_matches_claim(claim: Claim, params: dict) -> CheckOutcome:
    coc = claim.documents.coc
    if coc is None:
        return CheckOutcome(ok=None, detail_en="No COC generated yet.", detail_ar="لم يصدر محضر الإنجاز بعد.")
    ok = abs(coc.claim_amount - claim.claim_amount_total) <= 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=f"COC amount {coc.claim_amount:,.2f} vs claim total {claim.claim_amount_total:,.2f}.",
        detail_ar=f"مبلغ المحضر {coc.claim_amount:,.2f} مقابل إجمالي المطالبة {claim.claim_amount_total:,.2f}.",
        evidence={"coc": coc.claim_amount, "claim": claim.claim_amount_total},
    )


@check("coc_delay_vs_penalties")
def coc_delay_vs_penalties(claim: Claim, params: dict) -> CheckOutcome:
    coc = claim.documents.coc
    penalties = claim.documents.penalties
    if coc is None:
        return CheckOutcome(ok=None, detail_en="No COC generated yet.", detail_ar="لم يصدر محضر الإنجاز بعد.")
    total_penalties = sum(p.amount for p in penalties)
    contradiction = total_penalties > 0 and coc.has_delay is False and coc.has_stoppage is False and coc.has_observations is False
    return CheckOutcome(
        ok=not contradiction,
        detail_en=(
            f"Vendor has {len(penalties)} penalty(ies) totalling {total_penalties:,.2f}, but the COC states no delay, no stoppage and no observations."
            if contradiction
            else "COC delay/stoppage/observation answers are consistent with the penalty record."
        ),
        detail_ar=(
            f"على المورد غرامات بمجموع {total_penalties:,.2f} بينما يذكر محضر الإنجاز عدم وجود تأخير أو إيقاف أو ملاحظات."
            if contradiction
            else "إجابات المحضر متسقة مع سجل الغرامات."
        ),
        evidence={"penalties_total": total_penalties, "coc_has_delay": coc.has_delay, "coc_has_stoppage": coc.has_stoppage, "coc_has_observations": coc.has_observations},
    )


# --------------------------------------------------------------------------
# Pre-finance package checks
# --------------------------------------------------------------------------


@check("attachments_complete")
def attachments_complete(claim: Claim, params: dict) -> CheckOutcome:
    have = {a.strip().lower() for a in claim.documents.attachments}
    missing = [r for r in params.get("required", []) if r.strip().lower() not in have]
    return CheckOutcome(
        ok=not missing,
        detail_en=f"Missing attachments: {', '.join(missing)}" if missing else "All required attachments filed.",
        detail_ar=f"مرفقات ناقصة: {', '.join(missing)}" if missing else "جميع المرفقات المطلوبة موجودة.",
        evidence={"missing": missing},
    )


@check("vat_treatment_declared")
def vat_treatment_declared(claim: Claim, params: dict) -> CheckOutcome:
    inv = claim.documents.invoice
    if inv is None:
        return CheckOutcome(ok=None, detail_en="No invoice document.", detail_ar="لا توجد فاتورة.")
    ok = inv.vat_exempt or inv.vat_amount > 0
    return CheckOutcome(
        ok=ok,
        detail_en="Invoice neither charges VAT nor declares exemption." if not ok else ("VAT-exempt invoice." if inv.vat_exempt else "VAT charged."),
        detail_ar="الفاتورة لا تحتسب ضريبة ولا تصرح بالإعفاء." if not ok else ("فاتورة معفاة من الضريبة." if inv.vat_exempt else "الضريبة محتسبة."),
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def run_rulepack(gate_id: str, rulepack_file: str, claim: Claim) -> GateRun:
    spec = yaml.safe_load((RULEPACK_DIR / rulepack_file).read_text(encoding="utf-8"))
    findings: list[Finding] = []
    for rule in spec.get("rules", []):
        fn = CHECKS.get(rule["check"])
        if fn is None:  # rulepack references a check not implemented yet
            continue
        outcome = fn(claim, rule.get("params", {}))
        if outcome.ok is None:
            continue
        if outcome.ok:
            severity = Severity.ok
        else:
            severity = outcome.severity_override or Severity(rule.get("severity", "fail"))
        findings.append(
            Finding(
                rule_id=rule["id"],
                gate=gate_id,
                severity=severity,
                title_en=rule["title_en"],
                title_ar=rule["title_ar"],
                detail_en=outcome.detail_en,
                detail_ar=outcome.detail_ar,
                source=RuleSource(**rule.get("source", {"doc": "unspecified"})),
                evidence=outcome.evidence,
            )
        )
    worst = Severity.ok
    for f in findings:
        if f.severity is Severity.fail:
            worst = Severity.fail
            break
        if f.severity is Severity.warn:
            worst = Severity.warn
    return GateRun(gate=gate_id, severity=worst, findings=findings)
