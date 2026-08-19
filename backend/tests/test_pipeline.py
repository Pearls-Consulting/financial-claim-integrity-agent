from app.domain.models import Severity, Verdict
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
    assert "coc.delay_vs_penalties" in failed_rules
    assert "prefinance.attachments_complete" in failed_rules


def test_every_finding_cites_a_source():
    claim = get_source().get_claim("VRM-002402")
    assert claim is not None
    result = run_claim(claim)
    for gate in result.gates:
        for finding in gate.findings:
            assert finding.source.doc
