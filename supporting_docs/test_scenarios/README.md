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
| Contract value | `178250` |
| Invoice no. / date | `00196` / `2026-03-01` |
| Payment no. / type | `1` / **Final** |
| Base / VAT / total | `155000` / `23250` / `178250` |

**Expected:** every gate passes except two **Review** findings — intake: the
invoice QR is phase-1-style (no valid phase-2 cryptographic attestation),
routing to a Fatoora check; three-way match: no ERP product receipt exists
for a claim submitted outside the ERP, so the receipt-present check warns
(the seeded twin `VRM-002401`, which carries receipt `PR-000000841`, passes
this gate fully). Verdict is *needs human review*. BoQ lines match, final
claim closes the contract exactly.

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
| Contract value | `4200000` |
| Invoice no. / date | `INV-2026-0117` / `2026-07-02` |
| Payment no. / type | `5` / Periodic |
| Base / VAT / total | `380000` / `57000` / `437000` |
| Disbursed before / prior payments | `1310000` / `3` |
| ERP attachments | **Uncheck "Zakat certificate"** |
| Penalty row (add one) | غرامة تأخير في تسليم المرحلة الثالثة — `25000` — `2026-06-20` |

**Expected:** verdict *recommend reject*.
- Gate 1 **fail** — QR decodes but VAT number/amounts don't match the invoice face.
- Gate 2 **fail** — CIV-014 deviates from the BoQ; payment no. 5 ≠ expected 4 (sequence gap).
- Gate 3 **fail** — COC states no delay while a 25,000 delay penalty is on record.
- Gate 4 (three-way match) — for a wizard submission there is no ERP product
  receipt, so the gate **warns** on the missing receipt. The seeded twin
  `VRM-002402` carries receipt `PR-000001127` (2× CIV-010, 1× CIV-014) and
  hard-**fails**: 3 HVAC units billed vs 1 received, and the claimed base
  380,000 exceeds the 245,000 value of received work at BoQ prices.
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

Fixtures are regenerated byte-stable by `backend/scripts/generate_test_invoices.py`
(invoices + `manifest.json`) and `backend/scripts/generate_claim_docs.py`
(Modern Construction BoQ + COC). `manifest.json` records each file's expected
phase-2 classification and is consumed by `backend/tests/test_generated_invoices.py`.
