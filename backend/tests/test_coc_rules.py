"""The COC as the client's D365 VRM module prints it — restating the claim
(invoice ref, payment ordinal, net + VAT), the contract (number, dates,
value) and the award letter — cross-checked field by field, since every one
of those is generated from vendor input."""

from app.domain.models import ClaimType, CocDoc, ContractDoc, ContractKind, DetectedAttachment, Severity
from app.services.datasource import get_source
from app.services.pipeline import run_claim


def _gate(claim, gate_id):
    result = run_claim(claim)
    gate = next(g for g in result.gates if g.gate == gate_id)
    return {f.rule_id: f for f in gate.findings}


def _claim(cid="VRM-002401"):
    return get_source().get_claim(cid).model_copy(deep=True)


def _coc(claim, **over) -> CocDoc:
    """A COC consistent with the seeded claim, then overridden."""
    inv = claim.documents.invoice
    base = dict(
        coc_no="COC-000000197", coc_date="2026-03-05", claim_amount=claim.claim_amount_total,
        claim_net=claim.claim_amount_base, vat_amount=claim.vat_amount, invoice_ref=inv.invoice_no,
        payment_no=claim.payment_no, claim_type=claim.claim_type.value, contract_no="RFQ24/224",
        contract_start_date="2025-11-01", contract_end_date=claim.contract_end_date,
        contract_value_with_vat=round(claim.contract_value * 1.15, 2), award_letter_no="25238997",
        site_handover_date="2025-11-01", has_delay=False, has_stoppage=False, has_observations=False,
    )
    base.update(over)
    return CocDoc(**base)


def test_seeded_claims_stay_clean_with_the_richer_coc():
    for cid in ("VRM-002401", "VRM-002402"):
        claim = _claim(cid)
        claim.contract_kind = ContractKind.works
        for gate in ("three_way_match", "final_check"):
            bad = [f.rule_id for f in _gate(claim, gate).values() if f.rule_id.startswith(("three_way.coc", "final.coc")) and f.severity is not Severity.ok]
            assert not bad, (cid, gate, bad)


def test_coc_issued_for_another_invoice_fails():
    claim = _claim()
    claim.documents.coc = _coc(claim, invoice_ref="00195")
    f = _gate(claim, "three_way_match")["three_way.coc_invoice_ref"]
    assert f.severity is Severity.fail and f.evidence == {"coc_ref": "00195", "invoice": "00196"}
    # punctuation / case / RTL reading order never count as a mismatch
    claim.documents.coc = _coc(claim, invoice_ref="inv/2026/00070")
    claim.documents.invoice.invoice_no = "INV-2026-00070"
    assert _gate(claim, "three_way_match")["three_way.coc_invoice_ref"].severity is Severity.ok


def test_coc_net_or_vat_off_the_invoice_fails_even_when_the_total_matches():
    claim = _claim()
    claim.documents.coc = _coc(claim, claim_net=150000.0, vat_amount=28250.0)  # still sums to 178,250
    by_rule = _gate(claim, "three_way_match")
    assert by_rule["three_way.coc_amount_matches_claim"].severity is Severity.ok
    f = by_rule["three_way.coc_amounts_match_invoice"]
    assert f.severity is Severity.fail and "net" in f.detail_en and "VAT" in f.detail_en
    assert f.evidence["invoice_net"] == 155000.0


def test_coc_payment_ordinal_or_type_off_the_claim_fails():
    claim = _claim()
    claim.documents.coc = _coc(claim, payment_no=5, claim_type="periodic")  # claim is payment 1, final
    f = _gate(claim, "three_way_match")["three_way.coc_payment_matches_claim"]
    assert f.severity is Severity.fail
    assert f.evidence == {"coc_payment": 5, "claim_payment_no": 1, "coc_claim_type": "periodic", "claim_type": "final"}
    # a COC that prints neither is not applicable, never a failure
    claim.documents.coc = _coc(claim, payment_no=0, claim_type="")
    assert "three_way.coc_payment_matches_claim" not in _gate(claim, "three_way_match")


def test_coc_contract_value_vs_contract_document_then_claim_header():
    claim = _claim()
    claim.documents.coc = _coc(claim, contract_value_with_vat=200000.0)
    f = _gate(claim, "three_way_match")["three_way.coc_contract_value"]
    assert f.severity is Severity.fail and f.evidence["contract_value_with_vat"] == 178250.0  # header base + 15%
    claim.documents.contract = ContractDoc(contract_no="RFQ24/224", value_base=173913.04, value_with_vat=200000.0)
    assert _gate(claim, "three_way_match")["three_way.coc_contract_value"].severity is Severity.ok  # the document wins


def test_coc_contract_end_date_off_the_contract_fails_and_feeds_delay_inference():
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.documents.coc = _coc(claim, contract_end_date="2026-04-30")  # contract says 2026-03-31
    f = _gate(claim, "final_check")["final.coc_contract_end"]
    assert f.severity is Severity.fail and "30 day" in f.detail_en
    claim.documents.coc = _coc(claim, contract_end_date="2026-04-02")  # two days: a derived-duration rounding, not another deadline
    assert _gate(claim, "final_check")["final.coc_contract_end"].severity is Severity.ok
    # no end date anywhere but on the COC: the delay inference runs on the COC's
    claim.contract_end_date = ""
    claim.documents.contract = None
    claim.documents.coc = _coc(claim, contract_end_date="2026-02-15", coc_date="2026-03-05")
    by_rule = _gate(claim, "final_check")
    assert "final.coc_contract_end" not in by_rule  # nothing to compare against
    assert by_rule["final.delay_from_dates"].evidence["delay_days"] == 18


def test_coc_award_letter_vs_filed_letter():
    claim = _claim()
    claim.documents.coc = _coc(claim, award_letter_no="25238997")
    assert "three_way.coc_award_letter" not in _gate(claim, "three_way_match")  # no letter filed
    claim.documents.detected_attachments = [DetectedAttachment(file_name="award.pdf", doc_key="award letter", fields={"reference_no": "26400871"})]
    f = _gate(claim, "three_way_match")["three_way.coc_award_letter"]
    assert f.severity is Severity.warn and f.evidence == {"coc_award_letter": "25238997", "award_letter": "26400871"}
    claim.documents.detected_attachments[0].fields["reference_no"] = "2523 8997"
    assert _gate(claim, "three_way_match")["three_way.coc_award_letter"].severity is Severity.ok


def test_coc_without_the_extra_fields_is_not_penalised():
    """A COC read from a print that carries only the classic fields (or a
    scan the reader could not fully read) triggers none of the new rules."""
    claim = _claim()
    claim.documents.coc = CocDoc(coc_no="COC-1", coc_date="2026-03-05", claim_amount=claim.claim_amount_total, has_delay=False)
    for gate in ("three_way_match", "final_check"):
        ids = set(_gate(claim, gate))
        assert not ids & {"three_way.coc_invoice_ref", "three_way.coc_amounts_match_invoice", "three_way.coc_payment_matches_claim",
                          "three_way.coc_contract_value", "three_way.coc_award_letter", "final.coc_contract_end"}
    assert claim.claim_type is ClaimType.final  # untouched


def test_coc_month_first_dates_recognised_not_flagged_and_delay_uses_the_swap():
    """Fielded 2026-09-02 (VRM-900005): the D365 COC prints US month/day
    dates while the contract prints day/month. "07/01/2026" is 1 July; read
    day-first it became 7 January — 175 phantom days on the contract-end
    check. The swap-equality anchor recognises the convention and the delay
    inference reads the COC date in the same order."""
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.contract_end_date = "2026-07-01"
    # day-first misreads of the COC's month-first prints: contract end
    # "07/01/2026" (1 July) stored as 2026-01-07; COC date "06/01/2026"
    # (1 June) stored as 2026-01-06.
    claim.documents.coc = _coc(claim, contract_end_date="2026-01-07", coc_date="2026-01-06")
    by_rule = _gate(claim, "final_check")
    f = by_rule["final.coc_contract_end"]
    assert f.severity is Severity.ok
    assert f.evidence == {"coc_end_date": "2026-07-01", "contract_end": "2026-07-01", "date_order": "month-first"}
    # the delay check must cite the swapped acceptance date (1 June), not
    # the misread (6 January).
    d = by_rule["final.delay_from_dates"]
    assert d.severity is Severity.ok
    assert d.evidence["completion_date"] == "2026-06-01"


def test_coc_genuinely_different_end_date_still_fails():
    """The rescue fires only on swap-EQUALITY — a really different deadline
    (whose swap is not the contract's date, or not a date at all) keeps
    failing exactly as before."""
    claim = _claim()
    claim.contract_kind = ContractKind.works
    claim.contract_end_date = "2026-07-01"
    claim.documents.coc = _coc(claim, contract_end_date="2026-09-15")  # swap invalid (month 15)
    assert _gate(claim, "final_check")["final.coc_contract_end"].severity is Severity.fail
    claim.documents.coc = _coc(claim, contract_end_date="2026-03-04")  # swap = 2026-04-03, still not the deadline
    assert _gate(claim, "final_check")["final.coc_contract_end"].severity is Severity.fail
