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


def _completion_vs_end(claim: Claim) -> tuple[date | None, date | None, str]:
    """(contract end, acceptance date, acceptance label) — the date pair the
    delay inference runs on. Contract end comes from the claim header, else the
    contract document; the acceptance date is the COC date for works and the
    receipt date for goods. Either side may be None (unknown)."""
    contract = claim.documents.contract
    end = _parse_date(claim.contract_end_date) or (_parse_date(contract.end_date) if contract else None)
    coc, rec = claim.documents.coc, claim.documents.receipt
    if claim.contract_kind is ContractKind.goods:
        return end, (_parse_date(rec.receipt_date) if rec else None), "delivery date"
    return end, (_parse_date(coc.coc_date) if coc else None), "COC date"


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
