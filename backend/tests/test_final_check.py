"""Gate 3 (acceptance & three-way) and gate 4 (final check) — kind-aware
acceptance, date-inferred delay, and the contract's own penalty clauses."""

from app.domain.models import CocDoc, ContractDoc, ContractKind, Penalty, PenaltyTerm, ReceiptDoc, Severity
from app.services.datasource import get_source
from datetime import date

from app.services.rules.engine import _parse_date


def test_rule_date_parser_accepts_printed_day_month_year():
    assert _parse_date("2026-07-12") == date(2026, 7, 12)
    assert _parse_date("12-07-2026") == date(2026, 7, 12)
    assert _parse_date("12/07/2026") == date(2026, 7, 12)
    assert _parse_date("خمسة أشهر من تاريخ محضر بدء المشروع") is None
    assert _parse_date("") is None


from app.services.pipeline import run_claim


def _gate(claim, gate_id):
    result = run_claim(claim)
    gate = next(g for g in result.gates if g.gate == gate_id)
    return {f.rule_id: f for f in gate.findings}


def _claim(cid="VRM-002401"):
    return get_source().get_claim(cid).model_copy(deep=True)


def test_works_claim_accepts_via_coc_even_without_receipt():
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.documents.receipt = None
    by_rule = _gate(claim, "three_way_match")
    assert by_rule["three_way.acceptance_present"].severity is Severity.ok
    assert "coc" in by_rule["three_way.acceptance_present"].detail_en.lower()


def test_goods_claim_without_receipt_warns_on_acceptance():
    claim = _claim()
    claim.contract_kind = ContractKind.goods
    claim.documents.receipt = None
    by_rule = _gate(claim, "three_way_match")
    assert by_rule["three_way.acceptance_present"].severity is Severity.warn


def test_delay_inferred_from_dates_contradicting_coc_fails():
    """COC dated after the contract end but declaring 'no delay' — the dates
    alone expose the contradiction, no penalty record needed."""
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.contract_end_date = "2026-02-15"
    claim.documents.coc = CocDoc(coc_no="COC-1", coc_date="2026-03-05", claim_amount=claim.claim_amount_total, has_delay=False, has_stoppage=False, has_observations=False)
    by_rule = _gate(claim, "final_check")
    finding = by_rule["final.delay_from_dates"]
    assert finding.severity is Severity.fail
    assert finding.evidence["delay_days"] == 18


def test_late_goods_delivery_without_penalty_warns():
    claim = _claim()
    claim.contract_kind = ContractKind.goods
    claim.contract_end_date = "2026-02-20"
    claim.documents.receipt = ReceiptDoc(receipt_no="PR-1", receipt_date="2026-03-03", lines=[])
    claim.documents.penalties = []
    by_rule = _gate(claim, "final_check")
    assert by_rule["final.delay_from_dates"].severity is Severity.warn


def test_late_delivery_with_penalty_on_record_is_consistent():
    claim = _claim()
    claim.contract_kind = ContractKind.goods
    claim.contract_end_date = "2026-02-20"
    claim.documents.receipt = ReceiptDoc(receipt_no="PR-1", receipt_date="2026-03-03", lines=[])
    claim.documents.penalties = [Penalty(reason_ar="غرامة تأخير", amount=5000.0, date="2026-03-04")]
    by_rule = _gate(claim, "final_check")
    assert by_rule["final.delay_from_dates"].severity is Severity.ok


def test_on_time_acceptance_passes_and_missing_dates_skip():
    claim = _claim()
    claim.contract_end_date = "2026-12-31"
    assert _gate(claim, "final_check")["final.delay_from_dates"].severity is Severity.ok
    claim.contract_end_date = ""
    assert "final.delay_from_dates" not in _gate(claim, "final_check")


# ---------------------------------------------------------------- contract terms


def _flat_terms():
    """HHC-style clause: delay penalty up to 10% of the BoQ line value,
    total penalties capped at 20% of the contract value."""
    return [
        PenaltyTerm(kind="delay", rate_percent=10.0, basis="قيمة البند حسب جدول الكميات", cap_percent=20.0, ref="٣.٣.١", page=37),
        PenaltyTerm(kind="delay", cap_percent=20.0, basis="القيمة الإجمالية للعقد", ref="٣.٣.٢", page=37),
    ]


def _late_goods_claim(penalties):
    claim = _claim()
    claim.contract_kind = ContractKind.goods
    claim.contract_end_date = "2026-02-20"
    claim.documents.receipt = ReceiptDoc(receipt_no="PR-1", receipt_date="2026-03-03", lines=[])
    claim.documents.penalties = penalties
    return claim


def test_no_penalty_terms_skips_contract_terms_rule():
    claim = _late_goods_claim([])
    claim.documents.contract = None
    assert "final.penalties_vs_contract" not in _gate(claim, "final_check")


def test_delay_with_terms_and_no_penalty_fails_citing_contract():
    claim = _late_goods_claim([])
    claim.documents.contract = ContractDoc(contract_no="HHC00050", penalty_terms=_flat_terms())
    finding = _gate(claim, "final_check")["final.penalties_vs_contract"]
    assert finding.severity is Severity.fail
    assert finding.evidence["contract_penalty"]["rate_percent"] == 10.0
    assert finding.evidence["contract_penalty"]["cap_percent"] == 20.0
    assert finding.evidence["delay_days"] == 11


def test_recorded_penalty_within_cap_passes():
    claim = _late_goods_claim([Penalty(reason_ar="غرامة تأخير", amount=5000.0, date="2026-03-04")])
    claim.documents.contract = ContractDoc(contract_no="HHC00050", penalty_terms=_flat_terms())
    assert _gate(claim, "final_check")["final.penalties_vs_contract"].severity is Severity.ok


def test_recorded_penalty_over_cap_fails():
    claim = _late_goods_claim([])
    claim.documents.contract = ContractDoc(contract_no="HHC00050", penalty_terms=_flat_terms())
    cap = 0.20 * claim.contract_value
    claim.documents.penalties = [Penalty(reason_ar="غرامة", amount=cap + 1000.0, date="2026-03-04")]
    finding = _gate(claim, "final_check")["final.penalties_vs_contract"]
    assert finding.severity is Severity.fail
    assert finding.evidence["cap_amount"] == round(cap, 2)


def test_weekly_rate_computes_expected_and_flags_shortfall():
    """RCU-style clause: 1% of contract value per week of delay, cap 6%.
    11 days late = 2 started weeks -> expected 2% of the contract value."""
    claim = _late_goods_claim([Penalty(reason_ar="غرامة تأخير", amount=100.0, date="2026-03-04")])
    claim.documents.contract = ContractDoc(
        contract_no="CW13593",
        penalty_terms=[PenaltyTerm(kind="delay", rate_percent=1.0, per="week", basis="قيمة العقد", cap_percent=6.0)],
    )
    finding = _gate(claim, "final_check")["final.penalties_vs_contract"]
    assert finding.severity is Severity.warn  # recorded 100 « expected
    assert finding.evidence["expected_penalty"] == round(0.02 * claim.contract_value, 2)
    claim.documents.penalties = [Penalty(reason_ar="غرامة تأخير", amount=finding.evidence["expected_penalty"], date="2026-03-04")]
    assert _gate(claim, "final_check")["final.penalties_vs_contract"].severity is Severity.ok


def test_on_time_with_terms_passes_and_shows_terms():
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.contract_end_date = "2026-12-31"
    claim.documents.contract = ContractDoc(contract_no="HHC00050", penalty_terms=_flat_terms())
    finding = _gate(claim, "final_check")["final.penalties_vs_contract"]
    assert finding.severity is Severity.ok
    assert finding.evidence["contract_penalty"]["clause_ref"] == "٣.٣.١"
