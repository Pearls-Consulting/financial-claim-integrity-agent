from app.domain.models import ClaimType, Severity, Verdict
from app.services.datasource import get_source
from app.services.pipeline import run_claim


def test_clean_claim_has_no_failures():
    """VRM-002401 is a genuine claim whose real invoice QR carries pseudo
    phase-2 tags — so the honest outcome is needs_human on exactly that one
    warn, with zero hard failures anywhere."""
    claim = get_source().get_claim("VRM-002401")
    assert claim is not None
    result = run_claim(claim)
    assert result.verdict is Verdict.needs_human
    non_ok = [f for g in result.gates for f in g.findings if f.severity is not Severity.ok]
    assert [f.rule_id for f in non_ok] == ["intake.qr_phase2"]
    assert non_ok[0].severity is Severity.warn


def test_defective_claim_rejects_on_every_gate():
    claim = get_source().get_claim("VRM-002402")
    assert claim is not None
    result = run_claim(claim)
    assert result.verdict is Verdict.reject
    failed_rules = {f.rule_id for g in result.gates for f in g.findings if f.severity is Severity.fail}
    assert "intake.qr_authentic" in failed_rules
    assert "boq.lines_match" in failed_rules
    assert "three_way.billed_vs_received" in failed_rules  # billed 3 HVAC units, receipt shows 1
    assert "three_way.claimed_within_certified_value" in failed_rules
    assert "final.delay_vs_penalties" in failed_rules
    assert "prefinance.attachments_complete" in failed_rules


def test_missing_contract_value_warns_instead_of_failing():
    """A zero contract value means 'not provided' (wizard submission without
    the header field) — the ceiling checks must route to a human, not declare
    a violation against a fabricated ceiling of 0."""
    claim = get_source().get_claim("VRM-002401")
    assert claim is not None
    claim = claim.model_copy(deep=True)
    claim.contract_value = 0.0
    result = run_claim(claim)
    boq_gate = next(g for g in result.gates if g.gate == "boq_match")
    by_rule = {f.rule_id: f for f in boq_gate.findings}
    assert by_rule["boq.cumulative_within_contract"].severity is Severity.warn
    assert by_rule["boq.final_claim_closes_contract"].severity is Severity.warn


def _boq_findings(claim):
    result = run_claim(claim)
    boq_gate = next(g for g in result.gates if g.gate == "boq_match")
    return {f.rule_id: f for f in boq_gate.findings}


def test_reused_payment_number_fails_as_duplicate_disbursement():
    """Reusing a number in 1..prior_payment_count is 'رقم الدفعة تم صرفها
    مسبقاً' — a hard duplicate-disbursement fail, and the sequence check
    stands down so the slip is not double-reported."""
    claim = get_source().get_claim("VRM-002402").model_copy(deep=True)
    claim.payment_no = 2  # payments 1-3 already disbursed
    by_rule = _boq_findings(claim)
    assert by_rule["boq.payment_not_already_disbursed"].severity is Severity.fail
    assert "boq.payment_sequence" not in by_rule


def test_skipped_payment_number_still_warns_on_sequence():
    claim = get_source().get_claim("VRM-002402").model_copy(deep=True)
    assert claim.payment_no == 5 and claim.prior_payment_count == 3  # skip, not reuse
    by_rule = _boq_findings(claim)
    assert by_rule["boq.payment_not_already_disbursed"].severity is Severity.ok
    assert by_rule["boq.payment_sequence"].severity is Severity.warn


def test_first_payment_type_with_prior_payments_fails():
    """'تعديل نوع المستخلص لدوري' — a first-payment claim on a contract that
    already has disbursements is mistyped."""
    claim = get_source().get_claim("VRM-002402").model_copy(deep=True)
    claim.claim_type = ClaimType.first
    by_rule = _boq_findings(claim)
    assert by_rule["boq.claim_type_consistent"].severity is Severity.fail


def test_final_type_on_non_closing_claim_fails_with_retype_advice():
    """The demo beat: reviewer flips a periodic claim to 'final' — the agent
    rejects it against the disbursement record and says to set it back to
    periodic, without final_claim_closes_contract double-reporting."""
    claim = get_source().get_claim("VRM-002402").model_copy(deep=True)
    claim.claim_type = ClaimType.final
    by_rule = _boq_findings(claim)
    finding = by_rule["boq.claim_type_consistent"]
    assert finding.severity is Severity.fail
    assert "periodic" in finding.detail_en
    assert "boq.final_claim_closes_contract" not in by_rule


def test_contract_consuming_claim_warns_it_should_be_final():
    claim = get_source().get_claim("VRM-002402").model_copy(deep=True)
    claim.claim_amount_base = round(claim.contract_value - claim.cumulative_prior, 2)
    by_rule = _boq_findings(claim)
    assert by_rule["boq.claim_type_consistent"].severity is Severity.warn


def test_every_finding_cites_a_source():
    claim = get_source().get_claim("VRM-002402")
    assert claim is not None
    result = run_claim(claim)
    for gate in result.gates:
        for finding in gate.findings:
            assert finding.source.doc
