# UI test scenarios — grouped by vendor / entity

Each folder is one vendor; each row below is one wizard run (`/submit` → New
claim review). Attach the listed files in the named upload slots, type the
form values exactly, and compare the findings against the expected outcome.
Amounts are SAR. Fields not listed: leave at their defaults (all ERP
attachments checked, no penalties, prior disbursed 0 / prior payments 0).

The deterministic gate results are exact; the judge rationale is
GPT-written, so its wording varies between runs.

---

## 1. Pearls Consulting (ESNAD) — real document chain — the PASS scenario

Real client documents, in [`../example_documents/`](../example_documents/).
Mirrors seeded claim **VRM-002401**.

| Upload slot | File |
| --- | --- |
| Tax invoice | `Sales-Invoice-00196.pdf` |
| Contract / BoQ | `ESNAD PO4500001119 - Materials and supplier data cleansing.pdf` |
| Delivery note | `Delivery-Note-ESNAD.docx` |

| Field | Value |
| --- | --- |
| Vendor name / account | Pearls Consulting Services Branch / `210841` |
| PO / project no. / project name | `4500001119` / `PRJ0000701` / Materials and supplier data cleansing |
| Contract value (base) | `155000` — or leave empty: uploading the BoQ suggests it |
| Invoice no. / date | `00196` / `2026-03-01` |
| Payment no. / type | `1` / **Final** |
| Contract kind | **Goods** for the wizard run — the real chain has a delivery note but no COC file, so the delivery note is the acceptance document (the seeded twin is Works with ERP COC + receipt data) |
| Base / VAT / total | `155000` / `23250` / `178250` |

**Expected:** every gate passes except one **Review** finding on intake — the
invoice QR is phase-1-style (no valid phase-2 cryptographic attestation),
routing to a Fatoora check. Verdict is *needs human review*. BoQ lines match,
final claim closes the contract exactly. Three-way match: the uploaded
delivery note is read as the receiving record — the gate passes if its three
deliverables extract cleanly, and warns (receipt missing / no lines) if not;
the seeded twin `VRM-002401` carries ERP receipt `PR-000000841` and always
passes this gate.

**Variant — second real invoice:** `Sales-Invoice-00218.pdf` (project *Local
Content Operating Model and Manual*, contract `LC-RAM-P400-RFP001-25`,
customer RUA Al Madinah Holding). Invoice no. `00218`, date `2026-08-03`,
base `48000` / VAT `7200` / total `55200`. No BoQ/COC pair exists, so gates
2–4 mostly skip — use it for extraction/QR testing, or type a wrong invoice
no. / VAT split to trigger intake mismatches on a real document. A separate
run — never attach it together with the ESNAD chain (its invoice no. would
just contradict the form).

**Not part of any scenario:** `facture PEARLS CONSULTING SERVICES.pdf` is an
*incoming* Tunisian invoice (Pluxee Tunisie billing Pearls — TND, 19% TVA,
TTN e-signature, not ZATCA). Wrong direction and jurisdiction; kept only as
an optional negative test — attached as the tax invoice, intake should fail
it because its QR does not decode as ZATCA TLV.

---

## 2. Modern Construction Contracting Co. (شركة البناء الحديث للمقاولات) — ALL GATES FAIL

Synthetic chain in [`modern-construction/`](modern-construction/). Mirrors
seeded claim **VRM-002402** — the full defect showcase.

| Upload slot | File | Planted defect |
| --- | --- | --- |
| Tax invoice | `TEST-fake_face-INV-2026-0117.pdf` | QR lies about seller (fake 5-digit VAT no.) and amounts |
| Contract / BoQ | `BoQ-RFQ25-118.pdf` | CIV-014 unit price 55,000 vs invoice's 63,333.33 |
| Certificate of Completion | `COC-000000242.pdf` | Answers "no delay / no stoppage / no observations" |

| Field | Value |
| --- | --- |
| Vendor name / account | Modern Construction Contracting Co. (شركة البناء الحديث للمقاولات) / `Vend00934` |
| PO / project no. / project name | `PO25-00139` / `PRJ0000722` / Branch buildings rehabilitation & maintenance |
| Contract value (base) | `4200000` |
| Invoice no. / date | `INV-2026-0117` / `2026-07-02` |
| Payment no. / type | `5` / Periodic |
| Contract kind | Works — step 3 asks for the COC as the acceptance document |
| Base / VAT / total | `380000` / `57000` / `437000` |
| Disbursed before / prior payments | `1310000` / `3` |
| Step 4 — penalty row (add one) | غرامة تأخير في تسليم المرحلة الثالثة — `25000` — `2026-06-20` |
| Step 5 — vendor file | withhold the zakat certificate to see the completeness fail |

**Expected:** verdict *recommend reject*.
- Gate 1 **fail** — QR decodes but VAT number/amounts don't match the invoice face.
- Gate 2 **fail** — CIV-014 deviates from the BoQ; payment no. 5 ≠ expected 4 (sequence gap).
- Gate 3 (acceptance & three-way) — the COC is the acceptance document for a
  works contract; quantity checks stand down without an ERP receipt. The
  seeded twin `VRM-002402` carries receipt `PR-000001127` (2× CIV-010,
  1× CIV-014) and hard-**fails**: 3 HVAC units billed vs 1 received, and the
  claimed base 380,000 exceeds the 245,000 value of received work at BoQ prices.
- Gate 4 **fail** — COC states no delay while a 25,000 delay penalty is on record.
- Gate 5 **fail** — zakat certificate missing from the attachment set.

---

## 3. Al-Ufuq IT Company (شركة الأفق لتقنية المعلومات) — ZATCA QR authenticity matrix

Four invoices in [`alufuq-it/`](alufuq-it/), identical except for the QR's
phase-2 material — isolates the intake gate's tiered QR verdicts. Attach only
the invoice (gates 2–4 mostly skip without BoQ/COC/receipt; the three-way
gate only warns that no ERP receipt exists).

Shared form values: vendor **Al-Ufuq IT Company**, any PO/project, contract
value `500000`, payment no. `1`, Periodic, claim date = invoice date.

| Invoice file | Invoice no. / date | Base / VAT / total | Phase-2 QR | Expected intake outcome |
| --- | --- | --- | --- | --- |
| `TEST-valid_phase2-TINV-2026-1001.pdf` | `TINV-2026-1001` / `2026-08-10` | `120000` / `18000` / `138000` | genuine ECDSA signature | all checks **pass** — the clean-invoice baseline |
| `TEST-phase1_only-TINV-2026-1002.pdf` | `TINV-2026-1002` / `2026-08-11` | `40000` / `6000` / `46000` | absent (tags 1–5 only) | **Review** — phase-1 only, route to Fatoora app |
| `TEST-pseudo_phase2-TINV-2026-1003.pdf` | `TINV-2026-1003` / `2026-08-12` | `80000` / `12000` / `92000` | imitation tags (hex-text hash, UUID as key) | **Review** — pseudo material flagged, route to Fatoora app |
| `TEST-tampered-TINV-2026-1004.pdf` | `TINV-2026-1004` / `2026-08-13` | `65000` / `9750` / `74750` | real crypto, signature over a different hash | **Fail** — tampering indicator |

---

## 4. Al-Waha Office Supplies (شركة الواحة لتجهيزات المكاتب) — the scripted demo arc

Fully synthetic chain in [`demo-vendor/`](demo-vendor/) (details + form values
in its own README). A **periodic** progress claim billing 7 of the 12
contracted BoQ lines — 200,000.00 base of the 620,000.00 contract; partial
billing is expected for periodic claims. The contract and BoQ are ONE
document: contract terms on page 1, the BoQ annex on page 2.

The scripted arc (each beat is one wizard interaction):

1. **Tax invoice** → upload `Invoice-Alwaha-INV-2026-0342_tampered.pdf` →
   intake **fails**: the QR's phase-2 ECDSA signature does not verify —
   tampering indicator.
2. Re-upload `Invoice-Alwaha-INV-2026-0342_real.pdf` → intake **passes**
   (signature verifies over the invoice hash).
3. Set claim type to **Final** (payment no. `1`, prior payments `0`,
   contract value `620000` or BoQ-suggested), upload
   `Contract-BoQ-Alwaha-RFQ26-042_real.pdf` at step 2 → the gate **fails**:
   420,000.00 of the contract value would remain unclaimed — "change the
   claim type to periodic" (the agent reads the disbursement record the way
   the client's own reviewers do).
4. Set claim type back to **Periodic**, re-run → gate passes; the line-item
   table shows the 7 billed lines matching the BoQ and 5 lines as "not billed
   this period".
5. Contract kind **Goods** → step 3 asks for the goods receipt: upload
   `Delivery-Alwaha-DN-26-0342_real.pdf` → three-way match passes. Step 4:
   no penalties; delay inferred from dates (2026-06-14 vs contract end
   2026-11-30) → on time.
6. **Pre-finance** → upload the five vendor-file documents (`CR-…`,
   `Zakat-…`, `GOSI-…`, `AwardLetter-…`, `WorkCommencement-…`): the agent
   identifies each and reads its identity fields; contract + BoQ are covered
   by the step-2 document. All seven required docs must be present before
   the gate can run — withhold one to show the completeness fail.

Defect variants: `Contract-BoQ..._tampered.pdf` (OF-205 priced 290.00 vs
invoiced 320.00), `COC..._tampered.pdf` (totals 220,000.00 vs claim
230,000.00), `Delivery..._short.pdf` (OF-205 delivered 80 vs billed 120 —
three-way over-billing, claimed 200,000.00 vs 187,200.00 of received work).

---

## 5. HHC HQ fit-out (شركة الصحة القابضة / HHC00050) — the COMPLEX-CONTRACT showcase

Built around a REAL 76-page scanned works contract in
[`hhc-fitout/`](hhc-fitout/) (details, timeline and the full scripted arc in
its own README). The star beats:

- The agent reads the scanned contract: 140+ BoQ lines from ROTATED landscape
  tables, the contract value, the 5-month duration, and the PENALTY CLAUSES
  on p.37 (delay penalty ≤10% of the BoQ line value, total cap 20% of the
  contract value).
- Step 4 shows "Penalty terms read from the contract" — click a clause and
  the embedded reader opens the contract AT the clause's page and highlights
  it with OCR polygons (scanned pages have no text layer).
- The `_late` COC (20 days over the contractual end) makes
  `final.penalties_vs_contract` FAIL until a penalty consistent with the
  clause is recorded; an amount above the 20% cap fails the other way.

First OCR pass over the 76 pages takes a few minutes and is disk-cached —
upload the contract once before the demo to pre-warm.

---

Fixtures are regenerated byte-stable by `backend/scripts/generate_test_invoices.py`
(invoices + `manifest.json`), `backend/scripts/generate_claim_docs.py`
(Modern Construction BoQ + COC), `backend/scripts/generate_demo_scenario.py`
(the whole Al-Waha chain) and `backend/scripts/generate_hhc_scenario.py`
(the HHC fit-out chain around the real contract). `manifest.json` records each file's expected
phase-2 classification and is consumed by `backend/tests/test_generated_invoices.py`.
