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

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import yaml

from app.domain.models import PenaltyTerm, Claim, ClaimType, ContractKind, Finding, GateRun, RuleSource, Severity
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
    # Like-for-like basis: BoQ prices, the contract value, and the disbursement
    # history are all VAT-exclusive, so the claim's BASE amount is what counts
    # against the ceiling. (Whether client contracts state the ceiling incl. or
    # excl. VAT is a POC-P01 question — this is the BoQ-consistent default.)
    cumulative = round(claim.cumulative_prior + claim.claim_amount_base, 2)
    if claim.contract_value <= 0:
        # A zero header value means "not provided", not "the contract is worth
        # nothing" — failing against a fabricated ceiling would overstate what
        # the agent knows. Warn and route to the reviewer instead.
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en=f"No contract value on the claim header — the {cumulative:,.2f} cumulative claims (excl. VAT) cannot be assessed against a ceiling.",
            detail_ar=f"لا توجد قيمة عقد في بيانات المطالبة — يتعذر التحقق من إجمالي المطالبات {cumulative:,.2f} (بدون الضريبة) مقابل سقف العقد.",
            evidence={"cumulative_base": cumulative, "contract_value": None},
        )
    remaining = round(claim.contract_value - cumulative, 2)
    ok = cumulative <= claim.contract_value + 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"Cumulative claimed (excl. VAT) {cumulative:,.2f} is within the contract value {claim.contract_value:,.2f} — the claim passes, with {remaining:,.2f} of the contract remaining."
            if ok
            else f"Cumulative claimed (excl. VAT) {cumulative:,.2f} exceeds the contract value {claim.contract_value:,.2f} by {-remaining:,.2f}."
        ),
        detail_ar=(
            f"إجمالي المطالبات (بدون الضريبة) {cumulative:,.2f} ضمن قيمة العقد {claim.contract_value:,.2f} — المطالبة مقبولة ويتبقى {remaining:,.2f} من قيمة العقد."
            if ok
            else f"إجمالي المطالبات (بدون الضريبة) {cumulative:,.2f} يتجاوز قيمة العقد {claim.contract_value:,.2f} بمقدار {-remaining:,.2f}."
        ),
        evidence={"cumulative_base": cumulative, "contract_value": claim.contract_value, "remaining": remaining},
    )


@check("payment_not_already_disbursed")
def payment_not_already_disbursed(claim: Claim, params: dict) -> CheckOutcome:
    # Prior payments on a contract are numbered 1..prior_payment_count, so a
    # claim reusing a number in that range asks to pay the same دفعة twice —
    # a verbatim rejection reason in the client's claims screen ("رقم الدفعة
    # تم صرفها مسبقاً"), stated as such rather than as a sequence slip.
    if claim.payment_no < 1:
        return CheckOutcome(ok=None, detail_en="No payment number on the claim.", detail_ar="لا يوجد رقم دفعة في المطالبة.")
    duplicate = claim.payment_no <= claim.prior_payment_count
    return CheckOutcome(
        ok=not duplicate,
        detail_en=(
            f"Payment no. {claim.payment_no} was already disbursed — {claim.prior_payment_count} payment(s) are on record for this contract."
            if duplicate
            else f"Payment no. {claim.payment_no} has not been disbursed before ({claim.prior_payment_count} prior payment(s))."
        ),
        detail_ar=(
            f"رقم الدفعة {claim.payment_no} تم صرفها مسبقاً — عدد الدفعات المصروفة على هذا العقد {claim.prior_payment_count}."
            if duplicate
            else f"رقم الدفعة {claim.payment_no} لم يُصرف مسبقاً ({claim.prior_payment_count} دفعة/دفعات سابقة)."
        ),
        evidence={"payment_no": claim.payment_no, "prior_payment_count": claim.prior_payment_count},
    )


@check("payment_sequence")
def payment_sequence(claim: Claim, params: dict) -> CheckOutcome:
    if 1 <= claim.payment_no <= claim.prior_payment_count:
        # Reusing an already-disbursed number is the stronger duplicate finding
        # (payment_not_already_disbursed) — don't double-report it as a slip.
        return CheckOutcome(ok=None, detail_en="Covered by the duplicate-disbursement check.", detail_ar="مشمول بفحص تكرار الصرف.")
    expected = claim.prior_payment_count + 1
    ok = claim.payment_no == expected
    return CheckOutcome(
        ok=ok,
        detail_en=f"Payment no. {claim.payment_no}; expected {expected} after {claim.prior_payment_count} prior payment(s).",
        detail_ar=f"رقم الدفعة {claim.payment_no}؛ المتوقع {expected} بعد {claim.prior_payment_count} دفعة/دفعات سابقة.",
        evidence={"payment_no": claim.payment_no, "expected": expected},
    )


@check("claim_type_consistent")
def claim_type_consistent(claim: Claim, params: dict) -> CheckOutcome:
    """نوع المستخلص must agree with the payment history — reviewers reject for
    this verbatim ('تعديل نوع المستخلص لدوري'). Exact final settlement math is
    the separate final_claim_closes_contract check."""
    if claim.claim_type is ClaimType.final:
        if claim.contract_value <= 0:
            return CheckOutcome(
                ok=None,
                detail_en="No contract value — whether this is really the final claim cannot be judged.",
                detail_ar="لا توجد قيمة عقد — يتعذر الحكم على كون المطالبة نهائية.",
            )
        shortfall = round(claim.contract_value - claim.cumulative_prior - claim.claim_amount_base, 2)
        if shortfall > 0.01:
            return CheckOutcome(
                ok=False,
                detail_en=(
                    f"Claim is typed 'final' but {shortfall:,.2f} of the contract value would remain unclaimed after it — "
                    "per the disbursement record this is not the final claim; change the claim type to 'periodic'."
                ),
                detail_ar=(
                    f"نوع المستخلص 'نهائي' بينما يتبقى {shortfall:,.2f} من قيمة العقد بعد هذه المطالبة — "
                    "وفق سجل الصرف ليست هذه الدفعة النهائية؛ يجب تعديل نوع المستخلص لدوري."
                ),
                evidence={"claim_type": claim.claim_type.value, "remaining_after_claim": shortfall, "contract_value": claim.contract_value},
            )
        return CheckOutcome(
            ok=True,
            detail_en="Final claim type is consistent — the contract value is fully consumed.",
            detail_ar="نوع المستخلص النهائي متسق — قيمة العقد مستنفدة بالكامل.",
            evidence={"claim_type": claim.claim_type.value, "contract_value": claim.contract_value},
        )
    if claim.claim_type is ClaimType.first and claim.prior_payment_count > 0:
        return CheckOutcome(
            ok=False,
            detail_en=(
                f"Claim is typed 'first payment' but {claim.prior_payment_count} payment(s) were already disbursed on this contract — the type should be 'periodic'."
            ),
            detail_ar=f"نوع المستخلص 'دفعة أولى' رغم صرف {claim.prior_payment_count} دفعة/دفعات سابقة على هذا العقد — يجب تعديل نوع المستخلص لدوري.",
            evidence={"claim_type": claim.claim_type.value, "prior_payment_count": claim.prior_payment_count},
        )
    remaining = round(claim.contract_value - claim.cumulative_prior - claim.claim_amount_base, 2)
    # Exact closure only: an overshoot is cumulative_within_contract's failure,
    # not a typing problem.
    if claim.contract_value > 0 and abs(remaining) <= 0.01:
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en=(
                f"This claim consumes the full remaining contract value but is typed '{claim.claim_type.value}' — expected a final claim (مستخلص نهائي)."
            ),
            detail_ar="هذه المطالبة تستنفد كامل قيمة العقد المتبقية لكن نوعها ليس نهائياً — يُتوقع مستخلص نهائي.",
            evidence={"claim_type": claim.claim_type.value, "remaining_after_claim": remaining, "contract_value": claim.contract_value},
        )
    return CheckOutcome(
        ok=True,
        detail_en=f"Claim type '{claim.claim_type.value}' is consistent with the payment history.",
        detail_ar="نوع المستخلص متسق مع سجل الدفعات.",
        evidence={"claim_type": claim.claim_type.value, "prior_payment_count": claim.prior_payment_count},
    )


@check("final_claim_closes_contract")
def final_claim_closes_contract(claim: Claim, params: dict) -> CheckOutcome:
    if claim.claim_type is not ClaimType.final:
        return CheckOutcome(ok=None, detail_en="Not a final claim.", detail_ar="ليست دفعة نهائية.")
    cumulative = round(claim.cumulative_prior + claim.claim_amount_base, 2)
    if claim.contract_value <= 0:
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en="No contract value on the claim header — final settlement cannot be verified.",
            detail_ar="لا توجد قيمة عقد في بيانات المطالبة — يتعذر التحقق من الإقفال النهائي للعقد.",
            evidence={"cumulative_base": cumulative, "contract_value": None},
        )
    difference = round(cumulative - claim.contract_value, 2)
    if difference < -0.01:
        # A shortfall means it's not really the final claim — that's
        # claim_type_consistent's finding ('change the type to periodic').
        return CheckOutcome(ok=None, detail_en="Covered by the claim-type check.", detail_ar="مشمول بفحص نوع المستخلص.")
    ok = abs(difference) <= 0.01
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"Final claim: cumulative (excl. VAT) {cumulative:,.2f} equals the contract value — the contract closes out fully."
            if ok
            else f"Final claim: cumulative (excl. VAT) {cumulative:,.2f} should equal contract value {claim.contract_value:,.2f} (difference {difference:+,.2f})."
        ),
        detail_ar=(
            f"دفعة نهائية: الإجمالي (بدون الضريبة) {cumulative:,.2f} يساوي قيمة العقد — العقد مُقفل بالكامل."
            if ok
            else f"دفعة نهائية: الإجمالي (بدون الضريبة) {cumulative:,.2f} يجب أن يساوي قيمة العقد {claim.contract_value:,.2f} (الفارق {difference:+,.2f})."
        ),
        evidence={"cumulative_base": cumulative, "contract_value": claim.contract_value, "difference": difference},
    )


# --------------------------------------------------------------------------
# Three-way match checks (contract/BoQ ↔ ERP product receipt ↔ invoice)
# --------------------------------------------------------------------------

_QTY_TOL = 1e-6


@check("acceptance_present")
def acceptance_present(claim: Claim, params: dict) -> CheckOutcome:
    """One acceptance document per contract kind: the goods receipt for goods,
    the Certificate of Completion for works. The ERP product receipt posting
    is a cross-check when it exists, never a second acceptance."""
    if claim.contract_kind is ContractKind.goods:
        rec = claim.documents.receipt
        if rec is None:
            return CheckOutcome(
                ok=False,
                detail_en="Goods contract: no goods receipt (إيصال الاستلام) on the claim — acceptance of the delivery is not evidenced; the three-way match cannot be completed.",
                detail_ar="عقد توريد: لا يوجد إيصال استلام للبضاعة على المطالبة — الاستلام غير مُثبت ولا يمكن إتمام المطابقة الثلاثية.",
            )
        return CheckOutcome(
            ok=True,
            detail_en=f"Goods receipt {rec.receipt_no or '(unnumbered)'} dated {rec.receipt_date or '—'} with {len(rec.lines)} line(s) evidences acceptance.",
            detail_ar=f"إيصال الاستلام {rec.receipt_no or '(بدون رقم)'} بتاريخ {rec.receipt_date or '—'} ويتضمن {len(rec.lines)} بند/بنود يُثبت الاستلام.",
            evidence={"receipt_no": rec.receipt_no, "receipt_date": rec.receipt_date},
        )
    coc = claim.documents.coc
    if coc is None:
        return CheckOutcome(
            ok=False,
            detail_en="Works contract: no Certificate of Completion (محضر الإنجاز) on the claim — acceptance of the works is not evidenced.",
            detail_ar="عقد أعمال: لا يوجد محضر إنجاز على المطالبة — استلام الأعمال غير مُثبت.",
        )
    return CheckOutcome(
        ok=True,
        detail_en=f"Certificate of Completion {coc.coc_no or '(unnumbered)'} dated {coc.coc_date or '—'} evidences acceptance of the works.",
        detail_ar=f"محضر الإنجاز {coc.coc_no or '(بدون رقم)'} بتاريخ {coc.coc_date or '—'} يُثبت استلام الأعمال.",
        evidence={"coc_no": coc.coc_no, "coc_date": coc.coc_date},
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


def _ident_key(value: str) -> str:
    """Identifier comparison form: case, spacing and punctuation aside
    ("INV/2026/00070" = "INV-2026-00070" = "inv 2026 00070")."""
    return re.sub(r"[^0-9A-Za-z\u0600-\u06FF]+", "", (value or "").translate(_AR_DIGITS_RULES)).upper()


_AR_DIGITS_RULES = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_NO_COC = CheckOutcome(ok=None, detail_en="No COC generated yet.", detail_ar="لم يصدر محضر الإنجاز بعد.")


@check("coc_invoice_ref_matches")
def coc_invoice_ref_matches(claim: Claim, params: dict) -> CheckOutcome:
    """The COC names the invoice / financial claim it certifies (رقم المطالبة
    المالية) — it must be THIS claim's invoice, not an earlier payment's."""
    coc, inv = claim.documents.coc, claim.documents.invoice
    if coc is None:
        return _NO_COC
    if not coc.invoice_ref:
        return CheckOutcome(ok=None, detail_en="The COC does not print an invoice / claim reference.", detail_ar="لا يطبع المحضر رقم المطالبة المالية.")
    expected = inv.invoice_no if inv is not None and inv.invoice_no else claim.invoice_no
    where_en, where_ar = ("invoice", "الفاتورة") if inv is not None and inv.invoice_no else ("claim form", "نموذج المطالبة")
    ok = _ident_key(coc.invoice_ref) == _ident_key(expected) or _ident_key(coc.invoice_ref) in {_ident_key(v) for v in _bidi_variants(expected)}
    return CheckOutcome(
        ok=ok,
        detail_en=f"COC certifies claim '{coc.invoice_ref}'; the {where_en} says '{expected}'." + ("" if ok else " The COC was issued for a different invoice."),
        detail_ar=f"المحضر صادر للمطالبة '{coc.invoice_ref}'؛ {where_ar}: '{expected}'." + ("" if ok else " المحضر صادر لفاتورة أخرى."),
        evidence={"coc_ref": coc.invoice_ref, "invoice": expected},
    )


@check("coc_amounts_match_invoice")
def coc_amounts_match_invoice(claim: Claim, params: dict) -> CheckOutcome:
    """The COC restates the claim as net + VAT (قيمة المطالبة الحالية /
    مبلغ الضريبة); both must equal the invoice's, not only the total."""
    coc, inv = claim.documents.coc, claim.documents.invoice
    if coc is None:
        return _NO_COC
    if inv is None or (coc.claim_net <= 0 and coc.vat_amount <= 0):
        return CheckOutcome(ok=None, detail_en="No invoice, or the COC prints only the total.", detail_ar="لا توجد فاتورة، أو المحضر يطبع الإجمالي فقط.")
    inv_net = round(inv.total_with_vat - inv.vat_amount, 2)
    off = []
    if coc.claim_net > 0 and abs(coc.claim_net - inv_net) > 0.01:
        off.append(("net (excl. VAT)", "المبلغ قبل الضريبة"))
    if coc.vat_amount > 0 and abs(coc.vat_amount - inv.vat_amount) > 0.01:
        off.append(("VAT", "الضريبة"))
    ok = not off
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"COC net {coc.claim_net:,.2f} / VAT {coc.vat_amount:,.2f} vs invoice net {inv_net:,.2f} / VAT {inv.vat_amount:,.2f}."
            + ("" if ok else " Differs on: " + ", ".join(e for e, _ in off) + ".")
        ),
        detail_ar=(
            f"المحضر: {coc.claim_net:,.2f} قبل الضريبة / ضريبة {coc.vat_amount:,.2f} مقابل الفاتورة: {inv_net:,.2f} / {inv.vat_amount:,.2f}."
            + ("" if ok else " اختلاف في: " + "، ".join(a for _, a in off) + ".")
        ),
        evidence={"coc_net": coc.claim_net, "invoice_net": inv_net, "coc_vat": coc.vat_amount, "invoice_vat": inv.vat_amount},
    )


@check("coc_payment_matches_claim")
def coc_payment_matches_claim(claim: Claim, params: dict) -> CheckOutcome:
    """The payment ordinal (ترتيب الدفعة) and claim type (نوع المستخلص) the
    COC prints must be the ones on the claim — a COC certifying "the fifth
    payment, periodic" attached to a final claim no. 6 is a re-used COC."""
    coc = claim.documents.coc
    if coc is None:
        return _NO_COC
    if coc.payment_no < 1 and not coc.claim_type:
        return CheckOutcome(ok=None, detail_en="The COC prints neither a payment ordinal nor a claim type.", detail_ar="لا يطبع المحضر ترتيب الدفعة ولا نوع المستخلص.")
    off = []
    if coc.payment_no >= 1 and claim.payment_no >= 1 and coc.payment_no != claim.payment_no:
        off.append((f"payment no. {coc.payment_no} vs {claim.payment_no}", f"رقم الدفعة {coc.payment_no} مقابل {claim.payment_no}"))
    if coc.claim_type and coc.claim_type != claim.claim_type.value:
        off.append((f"type '{coc.claim_type}' vs '{claim.claim_type.value}'", f"النوع '{coc.claim_type}' مقابل '{claim.claim_type.value}'"))
    ok = not off
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"COC: payment no. {coc.payment_no or '—'}, type {coc.claim_type or '—'}; claim: payment no. {claim.payment_no or '—'}, type {claim.claim_type.value}."
            + ("" if ok else " Mismatch — " + "; ".join(e for e, _ in off) + ".")
        ),
        detail_ar=(
            f"المحضر: الدفعة {coc.payment_no or '—'}، النوع {coc.claim_type or '—'}؛ المطالبة: الدفعة {claim.payment_no or '—'}، النوع {claim.claim_type.value}."
            + ("" if ok else " عدم تطابق — " + "؛ ".join(a for _, a in off) + ".")
        ),
        evidence={"coc_payment": coc.payment_no, "claim_payment_no": claim.payment_no, "coc_claim_type": coc.claim_type, "claim_type": claim.claim_type.value},
    )


@check("coc_contract_value_matches")
def coc_contract_value_matches(claim: Claim, params: dict) -> CheckOutcome:
    """قيمة التعميد / العقد (شامل الضريبة) on the COC vs the contract
    (its printed incl-VAT value, else the claim header's base value + 15%)."""
    coc, contract = claim.documents.coc, claim.documents.contract
    if coc is None:
        return _NO_COC
    if coc.contract_value_with_vat <= 0:
        return CheckOutcome(ok=None, detail_en="The COC does not print the contract value.", detail_ar="لا يطبع المحضر قيمة العقد.")
    if contract is not None and contract.value_with_vat > 0:
        expected, src_en, src_ar = contract.value_with_vat, "contract document", "مستند العقد"
    elif claim.contract_value > 0:
        expected, src_en, src_ar = round(claim.contract_value * 1.15, 2), "claim header (base + 15% VAT)", "بيانات المطالبة (الأساس + 15٪)"
    else:
        return CheckOutcome(ok=None, detail_en="No contract value to compare against.", detail_ar="لا توجد قيمة عقد للمقارنة.")
    ok = abs(coc.contract_value_with_vat - expected) <= 1.0
    return CheckOutcome(
        ok=ok,
        detail_en=f"COC contract value {coc.contract_value_with_vat:,.2f} vs {expected:,.2f} per the {src_en}.",
        detail_ar=f"قيمة العقد في المحضر {coc.contract_value_with_vat:,.2f} مقابل {expected:,.2f} وفق {src_ar}.",
        evidence={"coc_value_with_vat": coc.contract_value_with_vat, "contract_value_with_vat": expected},
    )


@check("coc_contract_end_matches")
def coc_contract_end_matches(claim: Claim, params: dict) -> CheckOutcome:
    """تاريخ نهاية التعميد / العقد on the COC vs the contract end date the
    claim runs its delay inference on — the ERP's own view of the deadline
    must not differ from the contract's."""
    coc, contract = claim.documents.coc, claim.documents.contract
    if coc is None:
        return _NO_COC
    coc_end = _parse_date(coc.contract_end_date)
    end = _parse_date(claim.contract_end_date) or (_parse_date(contract.end_date) if contract else None)
    if coc_end is None or end is None:
        return CheckOutcome(ok=None, detail_en="Contract end date missing on the COC or on the contract.", detail_ar="تاريخ نهاية العقد غير متوفر في المحضر أو في العقد.")
    # A contract that states a DURATION has its end date derived (anchor +
    # months); a day or two of slack there is not the COC citing another
    # deadline. The rulepack sets the allowance.
    if _coc_prints_month_first(claim):
        return CheckOutcome(
            ok=True,
            detail_en=(
                f"COC prints its dates in month/day order (D365 US locale): its contract end reads "
                f"{_swap_day_month(coc_end).isoformat()} — the contract's own deadline. (Day-first reading {coc_end.isoformat()} was a format artifact, not another date.)"
            ),
            detail_ar=(
                f"يطبع المحضر تواريخه بترتيب الشهر/اليوم (تنسيق D365 الأمريكي): نهاية العقد فيه "
                f"{_swap_day_month(coc_end).isoformat()} — وهي موعد العقد نفسه. (القراءة يوم/شهر {coc_end.isoformat()} أثر تنسيق لا تاريخاً آخر.)"
            ),
            evidence={"coc_end_date": _swap_day_month(coc_end).isoformat(), "contract_end": end.isoformat(), "date_order": "month-first"},
        )
    tolerance = int(params.get("tolerance_days", 0))
    apart = abs((coc_end - end).days)
    ok = apart <= tolerance
    return CheckOutcome(
        ok=ok,
        detail_en=f"COC says the contract ends {coc_end.isoformat()}; the contract says {end.isoformat()}." + ("" if ok else f" {apart} day(s) apart — the COC's deadline is not the contract's."),
        detail_ar=f"نهاية العقد في المحضر {coc_end.isoformat()}؛ في العقد {end.isoformat()}." + ("" if ok else f" فارق {_days_ar(apart)} — موعد المحضر ليس موعد العقد."),
        evidence={"coc_end_date": coc_end.isoformat(), "contract_end": end.isoformat()},
    )


@check("coc_award_letter_matches")
def coc_award_letter_matches(claim: Claim, params: dict) -> CheckOutcome:
    """رقم خطاب الترسية on the COC vs the award letter filed with the claim."""
    coc = claim.documents.coc
    if coc is None:
        return _NO_COC
    letter = next((a for a in claim.documents.detected_attachments if a.doc_key == "award letter"), None)
    ref = (letter.fields.get("reference_no") if letter else "") or ""
    if not coc.award_letter_no or not ref:
        return CheckOutcome(ok=None, detail_en="No award letter number to compare (COC or filed letter).", detail_ar="لا يوجد رقم خطاب ترسية للمقارنة (في المحضر أو في الخطاب المرفق).")
    ok = _ident_key(coc.award_letter_no) == _ident_key(ref)
    return CheckOutcome(
        ok=ok,
        detail_en=f"COC cites award letter {coc.award_letter_no}; the filed award letter is {ref}.",
        detail_ar=f"يشير المحضر إلى خطاب الترسية {coc.award_letter_no}؛ الخطاب المرفق رقمه {ref}.",
        evidence={"coc_award_letter": coc.award_letter_no, "award_letter": ref},
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


_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DMY_RE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")


def _days_ar(n: int) -> str:
    """Arabic count form for days: يوم واحد / يومان / 3-10 أيام / 11+ يوماً."""
    n = abs(n)
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومين"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوماً"


def _parse_date(value: str) -> date | None:
    """ISO first; else the printed Saudi day-month-year ("12-07-2026") — the
    extractor normalises to ISO, this is the belt to its braces: a date the
    rules cannot parse silently disables every delay check."""
    m = _DATE_RE.search(value or "")
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DMY_RE.search(value or "")
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def _swap_day_month(d: date | None) -> date | None:
    """The same print read in the other day/month order; None when the swap
    is not a real date (day > 12 pins the convention)."""
    if d is None:
        return None
    try:
        return date(d.year, d.day, d.month)
    except ValueError:
        return None


def _coc_prints_month_first(claim: Claim) -> bool:
    """Detect a COC printed in US month/day order — the client's D365 report
    locale — while the contract (and this system) read day-first. Anchor: the
    COC's contract-end equals the contract's deadline ONLY once day and month
    are swapped. Fielded 2026-09-02 (VRM-900005): COC "07/01/2026" meant
    1 July, was read as 7 January — 175 phantom days. One anchor decides for
    the whole document; a report never mixes conventions within itself."""
    coc, contract = claim.documents.coc, claim.documents.contract
    if coc is None:
        return False
    coc_end = _parse_date(coc.contract_end_date)
    end = _parse_date(claim.contract_end_date) or (_parse_date(contract.end_date) if contract else None)
    return bool(coc_end and end and coc_end != end and _swap_day_month(coc_end) == end)


def _completion_vs_end(claim: Claim) -> tuple[date | None, date | None, str]:
    """(contract end, acceptance date, acceptance label) — the date pair the
    delay inference runs on. Contract end comes from the claim header, else the
    contract document, else the deadline the COC itself prints (the ERP's
    view — coc_contract_end_matches flags it when it differs from the
    contract's); the acceptance date is the COC date for works and the
    receipt date for goods. Either side may be None (unknown)."""
    contract = claim.documents.contract
    coc, rec = claim.documents.coc, claim.documents.receipt
    end = (
        _parse_date(claim.contract_end_date)
        or (_parse_date(contract.end_date) if contract else None)
        or (_parse_date(coc.contract_end_date) if coc else None)
    )
    if claim.contract_kind is ContractKind.goods:
        return end, (_parse_date(rec.receipt_date) if rec else None), "delivery date"
    done = _parse_date(coc.coc_date) if coc else None
    if done and _coc_prints_month_first(claim):
        done = _swap_day_month(done) or done  # same report, same date order
    return end, done, "COC date"


@check("delay_from_dates")
def delay_from_dates(claim: Claim, params: dict) -> CheckOutcome:
    """Infer delay from dates alone — independent of what the COC declares and
    of whether anyone logged a penalty. Contractual end date (claim header,
    else the contract document) vs the acceptance date (COC date for works,
    receipt date for goods)."""
    coc = claim.documents.coc
    end, done, done_label = _completion_vs_end(claim)
    if end is None or done is None:
        return CheckOutcome(
            ok=None,
            detail_en="Contract end date or acceptance date not available — delay cannot be inferred.",
            detail_ar="تاريخ نهاية العقد أو تاريخ الاستلام غير متوفر — يتعذر استنتاج التأخير.",
        )
    delay = (done - end).days
    penalties_total = round(sum(p.amount for p in claim.documents.penalties), 2)
    evidence = {
        "contract_end": end.isoformat(),
        "completion_date": done.isoformat(),
        "delay_days": max(delay, 0),
        "penalties_total": penalties_total,
    }
    if coc is not None:
        evidence["coc_has_delay"] = coc.has_delay
    if delay <= 0:
        return CheckOutcome(
            ok=True,
            detail_en=f"{done_label.capitalize()} {done.isoformat()} is within the contract period (ends {end.isoformat()}, {-delay} day(s) to spare).",
            detail_ar=f"تاريخ الاستلام {done.isoformat()} ضمن مدة العقد (تنتهي في {end.isoformat()}، قبل الموعد بـ {_days_ar(delay)}).",
            evidence=evidence,
        )
    if claim.contract_kind is ContractKind.works and coc is not None and coc.has_delay is False:
        return CheckOutcome(
            ok=False,
            detail_en=f"The dates show {delay} day(s) of delay ({done_label} {done.isoformat()} vs contract end {end.isoformat()}) but the COC declares no delay — contradiction.",
            detail_ar=f"التواريخ تُظهر تأخيراً قدره {_days_ar(delay)} (تاريخ المحضر {done.isoformat()} مقابل نهاية العقد {end.isoformat()}) بينما يذكر محضر الإنجاز عدم وجود تأخير — تناقض.",
            evidence=evidence,
        )
    if penalties_total <= 0:
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en=f"{delay} day(s) of delay per the dates ({done_label} {done.isoformat()} vs contract end {end.isoformat()}) with no penalty on record — assess the delay penalty under the contract / procurement law.",
            detail_ar=f"تأخير قدره {_days_ar(delay)} وفق التواريخ (الاستلام {done.isoformat()} مقابل نهاية العقد {end.isoformat()}) دون غرامة مسجلة — يلزم تقدير غرامة التأخير وفق العقد / نظام المنافسات.",
            evidence=evidence,
        )
    return CheckOutcome(
        ok=True,
        detail_en=f"{delay} day(s) of delay per the dates; penalties totalling {penalties_total:,.2f} are on record.",
        detail_ar=f"تأخير قدره {_days_ar(delay)} وفق التواريخ؛ غرامات بمجموع {penalties_total:,.2f} مسجلة.",
        evidence=evidence,
    )


@check("penalties_vs_contract_terms")
def penalties_vs_contract_terms(claim: Claim, params: dict) -> CheckOutcome:
    """Measure the penalty RECORD against the contract's own penalty CLAUSES,
    as read from the contract document (extraction/structuring.py).

    Deterministic on what the clauses allow computing:
    - a per-day / per-week delay rate -> the expected penalty for the inferred
      delay (capped by the printed ceiling), compared to the recorded total;
    - a flat/max rate (e.g. "لا تتجاوز ١٠٪ من قيمة البند") -> presence when
      delay exists, and the ceiling as an upper bound;
    - a cap (٢٠٪ من قيمة العقد) -> the recorded total must not exceed it.
    """
    contract = claim.documents.contract
    terms = [t for t in (contract.penalty_terms if contract else []) if t.kind == "delay" and (t.rate_percent or t.cap_percent)]
    if not terms:
        return CheckOutcome(
            ok=None,
            detail_en="No penalty clause read from the contract — nothing to measure the penalty record against.",
            detail_ar="لم تُقرأ بنود غرامات من العقد — لا يوجد مرجع تعاقدي لمطابقة سجل الغرامات.",
        )
    cap_percent = max((t.cap_percent for t in terms), default=0.0)
    basis_amount = claim.contract_value or (contract.value_base if contract else 0.0)

    def _contract_basis(t: PenaltyTerm) -> bool:
        return not t.basis or "عقد" in t.basis or "contract" in t.basis.lower()

    # The clause to cite: a rate the check can actually COMPUTE with (per
    # day/week of the contract value) beats everything; otherwise the
    # headline clause — the highest rate, its own cap first. A line-scoped
    # "2.5% per week of the delayed works" must not displace "up to 10% of
    # the line value, total capped at 20%" just because it names a period.
    term = max(terms, key=lambda t: (bool(t.per) and _contract_basis(t), bool(t.cap_percent), t.rate_percent))
    penalties_total = round(sum(p.amount for p in claim.documents.penalties), 2)
    end, done, done_label = _completion_vs_end(claim)

    term_ar = f"غرامة تأخير {term.rate_percent:g}٪" + {"day": " عن كل يوم", "week": " عن كل أسبوع"}.get(term.per, "")
    term_en = f"delay penalty {term.rate_percent:g}%" + {"day": " per day", "week": " per week"}.get(term.per, "")
    if term.basis:
        term_ar += f" من {term.basis}"
        term_en += f" of {term.basis}"
    if cap_percent:
        term_ar += f" بحد أقصى {cap_percent:g}٪"
        term_en += f", capped at {cap_percent:g}%"
    # The expected amount is only computable when the rate applies to the
    # CONTRACT VALUE. A line-scoped basis (قيمة البند / الأعمال المتأخرة)
    # cannot be extrapolated to the whole contract — the clause is cited and
    # a penalty demanded, but no figure is invented.
    basis_is_contract = _contract_basis(term)
    evidence: dict = {
        "contract_penalty": {
            "rate_percent": term.rate_percent,
            "per": term.per or None,
            "basis": term.basis or None,
            "cap_percent": cap_percent or None,
            "clause_ref": term.ref or None,
        },
        "penalties_total": penalties_total,
    }

    cap_amount = round(cap_percent / 100.0 * basis_amount, 2) if cap_percent and basis_amount else 0.0
    if cap_amount and penalties_total > cap_amount + 0.01:
        evidence["cap_amount"] = cap_amount
        return CheckOutcome(
            ok=False,
            detail_en=(
                f"Recorded penalties {penalties_total:,.2f} exceed the contract's ceiling of {cap_percent:g}% "
                f"of the contract value ({cap_amount:,.2f})."
            ),
            detail_ar=(
                f"الغرامات المسجلة {penalties_total:,.2f} تتجاوز الحد الأقصى التعاقدي {cap_percent:g}٪ "
                f"من قيمة العقد ({cap_amount:,.2f})."
            ),
            evidence=evidence,
        )

    if end is None or done is None:
        return CheckOutcome(
            ok=None,
            detail_en=f"Contract sets {term_en}, but the delay cannot be inferred (missing contract end or acceptance date).",
            detail_ar=f"ينص العقد على {term_ar}، لكن يتعذر استنتاج التأخير (تاريخ نهاية العقد أو تاريخ الاستلام غير متوفر).",
            evidence=evidence,
        )
    delay = max((done - end).days, 0)
    evidence["delay_days"] = delay
    if delay == 0:
        return CheckOutcome(
            ok=True,
            detail_en=f"Contract sets {term_en}; {done_label} {done.isoformat()} is on time — no penalty due.",
            detail_ar=f"ينص العقد على {term_ar}؛ الاستلام في {done.isoformat()} ضمن المدة — لا غرامة مستحقة.",
            evidence=evidence,
        )

    # Delay exists. Compute the expected amount when the clause gives a time
    # rate over the contract value; otherwise the clause still demands an
    # assessment (bounded by cap).
    units = delay if term.per == "day" else (-(-delay // 7) if term.per == "week" else 0)
    expected = (
        round(min(term.rate_percent / 100.0 * units * basis_amount, cap_amount or float("inf")), 2)
        if units and basis_amount and basis_is_contract
        else 0.0
    )
    if expected:
        evidence["expected_penalty"] = expected
    if penalties_total <= 0:
        return CheckOutcome(
            ok=False,
            detail_en=(
                f"{delay} day(s) of delay and the contract sets {term_en}"
                + (f" — expected ≈ {expected:,.2f}" if expected else "")
                + ", but no penalty is on record."
            ),
            detail_ar=(
                f"تأخير قدره {_days_ar(delay)} وينص العقد على {term_ar}"
                + (f" — الغرامة المتوقعة ≈ {expected:,.2f}" if expected else "")
                + "، ولا توجد غرامة مسجلة."
            ),
            evidence=evidence,
        )
    if expected and penalties_total < expected - 0.01:
        return CheckOutcome(
            ok=False,
            severity_override=Severity.warn,
            detail_en=f"Recorded penalties {penalties_total:,.2f} are below the contract's expected {expected:,.2f} ({term_en} × {delay} day(s) of delay).",
            detail_ar=f"الغرامات المسجلة {penalties_total:,.2f} أقل من المتوقع تعاقدياً {expected:,.2f} ({term_ar} × {_days_ar(delay)}).",
            evidence=evidence,
        )
    return CheckOutcome(
        ok=True,
        detail_en=(
            f"Recorded penalties {penalties_total:,.2f} are consistent with the contract clause ({term_en})"
            + (f"; expected ≈ {expected:,.2f}" if expected else "")
            + "."
        ),
        detail_ar=(
            f"الغرامات المسجلة {penalties_total:,.2f} متسقة مع بند العقد ({term_ar})"
            + (f"؛ المتوقع ≈ {expected:,.2f}" if expected else "")
            + "."
        ),
        evidence=evidence,
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
# Pre-finance identity / validity cross-checks
# --------------------------------------------------------------------------
# The prequalification agent's lesson (pre-qualification-agent, services/
# comparison.py + screening): an extracted value is a CLAIM TO CHECK, and a
# mismatch is an accusation — so every comparison here is deterministic,
# digit-script-normalised, and skips (ok=None) rather than guessing when a
# document does not print the value.


def _att_label(doc_key: str) -> tuple[str, str]:
    """(label_en, label_ar) for a detected-attachment doc key."""
    from app.services.extraction.attachments import ATTACHMENT_TYPES

    en, ar, _ = ATTACHMENT_TYPES.get(doc_key, (doc_key or "document", doc_key or "مستند", []))
    return en, ar


_AR_NAME_MARKS = re.compile(r"[ـً-ْٰ]")  # tatweel, harakat
_AR_NAME_FOLDS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_NAME_NON_WORD = re.compile(r"[^0-9a-zء-ي]+")


def _name_key(s: str) -> str:
    """A vendor name reduced to what survives any correct reading of the same
    printed words (hamza/ta-marbuta folds, harakat dropped) — mirrors the BoQ
    description normalisation in extraction/reconcile.py."""
    s = unicodedata.normalize("NFKC", (s or "")).translate(_AR_DIGITS_RULES).lower()
    s = _AR_NAME_MARKS.sub("", s).translate(_AR_NAME_FOLDS)
    return " ".join(t for t in _NAME_NON_WORD.split(s) if t)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", (value or "").translate(_AR_DIGITS_RULES))


@check("attachment_identity_consistent")
def attachment_identity_consistent(claim: Claim, params: dict) -> CheckOutcome:
    """Every CR / VAT number printed across the vendor-file documents must be
    ONE number: a certificate carrying a different registration belongs to a
    different establishment — wrongly filed, or wrongly read."""
    dets = claim.documents.detected_attachments
    evidence: dict = {}
    problems_en: list[str] = []
    problems_ar: list[str] = []
    compared = False
    for fkey, lab_en, lab_ar in (
        ("cr_number", "CR number", "رقم السجل التجاري"),
        ("vat_number", "VAT number", "الرقم الضريبي"),
    ):
        vals = [(a.doc_key, a.fields.get(fkey, "")) for a in dets if a.fields.get(fkey)]
        if not vals:
            continue
        evidence[fkey] = {doc: v for doc, v in vals}
        if len(vals) < 2:
            continue
        compared = True
        if len({_ident_key(v) for _, v in vals}) > 1:
            listing_en = "; ".join(f"{_att_label(d)[0]}: {v}" for d, v in vals)
            listing_ar = "؛ ".join(f"{_att_label(d)[1]}: {v}" for d, v in vals)
            problems_en.append(f"{lab_en} differs across the filed documents ({listing_en})")
            problems_ar.append(f"{lab_ar} يختلف بين المستندات المرفقة ({listing_ar})")
    if not compared:
        return CheckOutcome(
            ok=None,
            detail_en="No identifier printed on two documents to cross-check.",
            detail_ar="لا يوجد رقم هوية مطبوع على مستندين لإجراء المطابقة.",
        )
    ok = not problems_en
    return CheckOutcome(
        ok=ok,
        detail_en=("; ".join(problems_en) + ".") if problems_en else "CR and VAT numbers agree across every document that prints them.",
        detail_ar=("؛ ".join(problems_ar) + ".") if problems_ar else "رقم السجل التجاري والرقم الضريبي متطابقان في كل المستندات التي تطبعهما.",
        evidence=evidence,
    )


@check("attachment_vat_matches_invoice")
def attachment_vat_matches_invoice(claim: Claim, params: dict) -> CheckOutcome:
    """The VAT number on the vendor's certificates vs the seller VAT number
    on the tax invoice — the biller must be the establishment in the vendor
    file (the invoice face itself is QR-cross-checked at intake)."""
    inv = claim.documents.invoice
    vals = [(a.doc_key, a.fields.get("vat_number", "")) for a in claim.documents.detected_attachments if a.fields.get("vat_number")]
    if inv is None or not inv.seller_vat_number or not vals:
        return CheckOutcome(
            ok=None,
            detail_en="No VAT number on both sides to compare.",
            detail_ar="لا يوجد رقم ضريبي في الطرفين للمقارنة.",
        )
    inv_key = _ident_key(inv.seller_vat_number)
    off = [(d, v) for d, v in vals if _ident_key(v) != inv_key]
    ok = not off
    listing_en = "; ".join(f"{_att_label(d)[0]}: {v}" for d, v in off)
    listing_ar = "؛ ".join(f"{_att_label(d)[1]}: {v}" for d, v in off)
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"Invoice seller VAT {inv.seller_vat_number} matches the vendor-file documents."
            if ok
            else f"Invoice seller VAT {inv.seller_vat_number} differs from the vendor file ({listing_en}) — the biller may not be the establishment on file."
        ),
        detail_ar=(
            f"الرقم الضريبي في الفاتورة {inv.seller_vat_number} يطابق مستندات ملف المورد."
            if ok
            else f"الرقم الضريبي في الفاتورة {inv.seller_vat_number} يختلف عن ملف المورد ({listing_ar}) — قد لا يكون مُصدر الفاتورة هو المنشأة المسجلة."
        ),
        evidence={"invoice": inv.seller_vat_number, "attachments": {d: v for d, v in vals}},
    )


@check("attachment_vendor_name_consistent")
def attachment_vendor_name_consistent(claim: Claim, params: dict) -> CheckOutcome:
    """The establishment name across the vendor file vs the claim header and
    the invoice. Arabic legal names legitimately print with and without
    suffixes (…المحدودة), so containment counts as agreement — and a
    disagreement routes to a human (warn), never an automatic accusation."""
    entries: list[tuple[str, str, str]] = []
    if claim.vendor_name_ar:
        entries.append(("claim form", "نموذج المطالبة", claim.vendor_name_ar))
    inv = claim.documents.invoice
    if inv is not None and inv.seller_name_ar:
        entries.append(("invoice", "الفاتورة", inv.seller_name_ar))
    for a in claim.documents.detected_attachments:
        name = a.fields.get("vendor_name_ar", "")
        if name:
            en, ar = _att_label(a.doc_key)
            entries.append((en, ar, name))
    keyed = [(en, ar, n, _name_key(n)) for en, ar, n in entries if _name_key(n)]
    if len(keyed) < 2:
        return CheckOutcome(
            ok=None,
            detail_en="Fewer than two vendor names to compare.",
            detail_ar="لا يوجد اسمان للمنشأة لإجراء المقارنة.",
        )
    base_en, base_ar, base_name, base_key = keyed[0]
    off = [(en, ar, n) for en, ar, n, k in keyed[1:] if not (k == base_key or k in base_key or base_key in k)]
    ok = not off
    listing_en = "; ".join(f"{en}: '{n}'" for en, _, n in off)
    listing_ar = "؛ ".join(f"{ar}: '{n}'" for _, ar, n in off)
    return CheckOutcome(
        ok=ok,
        detail_en=(
            f"Vendor name consistent across the file ('{base_name}')."
            if ok
            else f"The {base_en} names '{base_name}', but not every document agrees ({listing_en})."
        ),
        detail_ar=(
            f"اسم المنشأة متسق عبر الملف ('{base_name}')."
            if ok
            else f"الاسم في {base_ar} هو '{base_name}' ولا تتفق معه بعض المستندات ({listing_ar})."
        ),
        evidence={"names": {en: n for en, _, n, _k in keyed}},
    )


@check("attachment_id_formats")
def attachment_id_formats(claim: Claim, params: dict) -> CheckOutcome:
    """A CR number that is not 10 digits, or a VAT number that is not a valid
    15-digit ZATCA number, is most likely a misread — a digit dropped or
    invented somewhere between the page and the record. Warn and point at the
    document rather than let a falsely attributed number stand."""
    problems_en: list[str] = []
    problems_ar: list[str] = []
    evidence: dict = {}
    seen = False
    for a in claim.documents.detected_attachments:
        en, ar = _att_label(a.doc_key)
        cr = a.fields.get("cr_number", "")
        if cr:
            seen = True
            if len(_digits(cr)) != 10:
                problems_en.append(f"{en}: CR number '{cr}' is not a 10-digit registration number")
                problems_ar.append(f"{ar}: رقم السجل التجاري '{cr}' ليس رقم سجل من 10 أرقام")
                evidence.setdefault("cr_number", {})[a.doc_key] = cr
        vat = a.fields.get("vat_number", "")
        if vat:
            seen = True
            if not zatca_qr.vat_number_ok(_digits(vat)):
                problems_en.append(f"{en}: VAT number '{vat}' is not a valid 15-digit ZATCA number")
                problems_ar.append(f"{ar}: الرقم الضريبي '{vat}' ليس رقماً ضريبياً نظامياً من 15 رقماً")
                evidence.setdefault("vat_number", {})[a.doc_key] = vat
    if not seen:
        return CheckOutcome(ok=None, detail_en="No CR / VAT numbers read from the vendor file.", detail_ar="لم تُقرأ أرقام سجل أو أرقام ضريبية من ملف المورد.")
    ok = not problems_en
    return CheckOutcome(
        ok=ok,
        detail_en=("; ".join(problems_en) + ". Verify the read against the document.") if problems_en else "Every CR / VAT number read has the official format.",
        detail_ar=("؛ ".join(problems_ar) + ". تُراجع القراءة على المستند نفسه.") if problems_ar else "جميع أرقام السجل والأرقام الضريبية المقروءة بالصيغة النظامية.",
        evidence=evidence,
    )


@check("attachment_certificates_valid")
def attachment_certificates_valid(claim: Claim, params: dict) -> CheckOutcome:
    """Zakat / GOSI / CR validity at the claim date — Finance acts on this
    package later, and an expired certificate bounces it. The comparison is
    date maths in code (the reader only lifts what is printed); an expiry
    printed in Hijri is left to the human eye, never miscompared."""
    watch = [str(d) for d in params.get("documents", ["commercial registration", "zakat certificate", "gosi certificate"])]
    ref = _parse_date(claim.claim_date) or date.today()
    expired: list[tuple[str, str, str]] = []
    evidence: dict = {"as_of": ref.isoformat()}
    seen = False
    for a in claim.documents.detected_attachments:
        if a.doc_key not in watch:
            continue
        printed = a.fields.get("expiry_date", "")
        d = _parse_date(printed) if printed else None
        if d is None or d.year < 1900:  # not printed, unparseable, or Hijri-printed
            continue
        seen = True
        evidence[a.doc_key] = d.isoformat()
        if d < ref:
            en, ar = _att_label(a.doc_key)
            expired.append((en, ar, d.isoformat()))
    if not seen:
        return CheckOutcome(
            ok=None,
            detail_en="No comparable (Gregorian) expiry dates read from the certificates.",
            detail_ar="لا توجد تواريخ انتهاء ميلادية قابلة للمقارنة في الشهادات.",
        )
    ok = not expired
    listing_en = "; ".join(f"{en} expired {iso}" for en, _, iso in expired)
    listing_ar = "؛ ".join(f"{ar} منتهية منذ {iso}" for _, ar, iso in expired)
    return CheckOutcome(
        ok=ok,
        detail_en=f"All certificates valid as of {ref.isoformat()}." if ok else f"{listing_en} — as of the claim date {ref.isoformat()}.",
        detail_ar=f"جميع الشهادات سارية حتى {ref.isoformat()}." if ok else f"{listing_ar} — بتاريخ المطالبة {ref.isoformat()}.",
        evidence=evidence,
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
