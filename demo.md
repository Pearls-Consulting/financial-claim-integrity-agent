# Claim Integrity Agent — Demo Runbook

Internal prep for the CEO review. Two scripted scenarios (one genuine claim,
one fraudulent claim), a justification for every field and element on every
page, the pain points lifted from the client's own procedure, and the
rationale for the three-way matching gate.

**The 30-second pitch:** SDB's procedure tells people *when* to review vendor
claims but never *what checking means*. Their own incident proves it — a
penalized vendor's completion certificate said "no delay" and passed every
gate. This agent supplies the missing judgment at each review gate: it reads
the documents (specialist OCR), verifies the tax invoice cryptographically
against ZATCA, three-way matches contract ↔ receipt ↔ invoice, catches
cross-document contradictions, and cites the law/regulation behind every
finding. The human keeps approve/reject authority.

---

## 1. Setup (do this before the meeting)

```bash
# backend (http://localhost:8000) — .env: EXTRACTOR_ENGINE=azure, JUDGE_ENGINE=gpt
cd backend && .venv/Scripts/python -m uvicorn app.main:app --port 8000

# ui (http://localhost:5173)
cd ui && npm run dev
```

- [ ] **Wipe `backend/data/claims.db`** (plus `-wal`/`-shm` and
      `backend/uploads/`) so the claims list is clean and old runs don't
      resurface.
- [ ] **Seed the display queue:** `.venv/Scripts/python scripts/seed_demo_claims.py`
      — 7 ERP-looking rows (approved / rejected / in-progress / not started)
      so استلام المطالبات reads like a live D365 queue. They're read-only set
      dressing; don't open them on purpose.
- [ ] `.env` has the Azure keys; OCR of the demo files is **disk-cached**
      (`backend/.cache/cu/`), so do one full dry run the day before — the
      live demo then re-reads the same files instantly and only the GPT
      structuring/judge calls hit the network.
- [ ] Fallback if the network dies: set `EXTRACTOR_ENGINE=mock` +
      `JUDGE_ENGINE=mock` and demo the two **seeded ERP claims** only
      (they carry pre-structured data; every deterministic check still runs).
- [ ] Files for Scenario B are in `supporting_docs/test_scenarios/modern-construction/`;
      the genuine ESNAD chain for Scenario A is in
      `supporting_docs/example_documents/` (real client docs — **exist only on
      this machine, never in git**).
- [ ] Language: demo in **Arabic** for the client audience; English is fine
      for the internal review. The EN | ع toggle flips the whole app RTL live —
      worth 5 seconds of the demo on its own.

---

## 2. Scenario A — the genuine claim (recommend approve path)

**Entry point:** claims list (`/`) → open seeded claim **VRM-002401**
(Pearls/ESNAD — a real, fully consistent document chain).

**Story to tell:** "This claim arrived through Dynamics 365 — the list you see
mirrors SDB's استلام المطالبات screen. The agent walks it through five gates."

Step through each gate with **تحليل / Analyze** and narrate:

| Gate | What passes | The one-liner |
| --- | --- | --- |
| 1 — Tax invoice | Invoice OCR'd, fields match the claim, VAT math correct, ZATCA QR decodes and matches the invoice face | "The QR isn't decoration — the agent decodes the TLV payload and compares seller, VAT number, and amounts against the printed invoice." |
| 2 — Contract & BoQ | All 3 invoice lines match BoQ item codes and unit prices; cumulative 155,000 within contract value 155,000; final claim closes the contract exactly | Open the evidence panel: every compared value has a **locate button** that opens the PDF and highlights the number on the page. |
| 3 — Acceptance & three-way match | Works contract: the COC is the acceptance document; ERP receipt PR-000000841 cross-checks it; billed = received = contracted on every line | "One acceptance document per contract kind — COC for works, goods receipt for goods. Contract says what was agreed, acceptance says what was delivered, the invoice says what's billed — all three reconcile." |
| 4 — Final check | No delay declared, no penalties on record, and the dates agree — consistent | "This is the gate their real incident slipped through: the agent cross-checks the COC's answers against the penalty record AND infers delay from the dates itself." |
| 5 — Pre-finance package | All seven required attachments filed, VAT treatment declared | Mirrors procedure step 6's checklist — except the procedure says "for example, not limited to"; we made it a real list. |

**Expected verdict: needs human review** — with exactly one amber finding:
the invoice QR is phase-1-style (no phase-2 cryptographic attestation).

**Do not apologize for this — it's the punchline:** phase-2 obligations roll
out per-vendor in ZATCA waves, which no receiver can determine offline. An
agent that green-lights what it cannot verify would be worthless in an audit.
It routes the claim to the reviewer with the exact next action ("confirm via
the Fatoora app") and cites the regulation. *The agent recommends; the human
decides.*

## 3. Scenario B — the fraudulent claim (recommend reject path)

**Entry point:** hero button **New claim review** (`/submit`) — this is the
integration-agnostic wizard; every document is uploaded by hand so no D365
connection is implied.

**Vendor:** Modern Construction Contracting Co. (fully synthetic).
Files: `supporting_docs/test_scenarios/modern-construction/`.

Form values (also in `supporting_docs/test_scenarios/README.md` §2):

| Field | Value |
| --- | --- |
| Tax invoice upload | `TEST-fake_face-INV-2026-0117.pdf` → **autofills** vendor, invoice no., date, amounts |
| Vendor account | `Vend00934` |
| PO / project | `PO25-00139` / `PRJ0000722` / Branch buildings rehabilitation & maintenance |
| Contract value (base) | `4200000` |
| Payment no. / type | `5` / Periodic |
| Base / VAT / total | `380000` / `57000` / `437000` (prefilled from the invoice) |
| Step 2 — BoQ upload | `BoQ-RFQ25-118.pdf`; disbursed before `1310000`, prior payments `3`; contract kind **Works** |
| Step 3 — acceptance | `COC-000000242.pdf` (works contract → the COC is the acceptance document) |
| Step 4 — final check | **add penalty**: غرامة تأخير في تسليم المرحلة الثالثة — 25,000 — 2026-06-20 |
| Step 5 — attachments | **Uncheck "Zakat certificate"** |

What fires, gate by gate:

- **Gate 1 — FAIL.** The invoice *looks* perfect. The QR decodes as valid
  ZATCA TLV — but its VAT number is a fake 5-digit value and its amounts
  don't match the invoice face. "A human sees a QR code and trusts it. The
  agent reads it."
- **Gate 2 — FAIL.** Item CIV-014 invoiced at 63,333.33 vs the BoQ's 55,000
  (locate button → highlights both numbers in both PDFs). Plus a warn:
  payment no. 5 after only 3 prior payments — a sequence gap.
- **Gate 3 — the COC is accepted as the acceptance document** (works
  contract); amount matches the claim. Quantity checks stand down without an
  ERP receipt instead of pretending to match.
- **Gate 4 — FAIL.** The COC answers "no delay, no stoppage, no observations"
  while a 25,000 delay penalty sits on the vendor's record. **This is the
  client's own reported incident, reproduced and caught.**
- **Gate 5 — FAIL.** Zakat certificate missing from the attachment set.
- **Verdict: recommend reject**, with a GPT-drafted bilingual rationale over
  the deterministic findings (the model can never override them).

**30-second coda — the three-way hard fail:** go back to the claims list and
open seeded **VRM-002402** (same vendor, as it would arrive *through D365,
where the product receipt exists*). Gate 3 now hard-fails twice: **3 HVAC
units billed vs 1 received**, and claimed base 380,000 vs 245,000 of received
work at BoQ prices. "This vendor's prices were right and the totals added up —
only the three-way match catches that the work simply wasn't delivered."

Matrix of four isolated QR states (valid / phase-1 / pseudo / tampered) in
`test_scenarios/alufuq-it/` if a quick authenticity-only demo is needed.

## 3b. Scenario C — the scripted "catch me twice" arc (Al-Waha, demo-vendor)

Fully synthetic vendor, files in `test_scenarios/demo-vendor/` (form values in
its README). A **periodic** claim billing 7 of 12 contracted BoQ lines —
200,000 base of a 620,000 contract; the contract & BoQ are one two-page
document (contract page 1, BoQ annex page 2), so step 2 takes a single upload.

The arc — the reviewer watches the agent catch, get fixed, catch again:

1. **Step 1:** upload `Invoice-Alwaha-INV-2026-0342_tampered.pdf` → intake
   **FAILS**: the QR's phase-2 material is well-formed but the ECDSA signature
   does not verify over the invoice hash. "You cannot photoshop this."
2. Adjust → re-upload `Invoice-Alwaha-INV-2026-0342_real.pdf` → intake passes
   (genuine secp256k1 signature verifies).
3. **Step 2:** flip claim type to **Final** (the type select sits on this step
   too, since this gate validates it; payment 1, prior payments 0), upload
   `Contract-BoQ-Alwaha-RFQ26-042_real.pdf` → gate 2 **FAILS**:
   "420,000.00 of the contract value would remain unclaimed — change the
   claim type to periodic." Narrate: *the agent checked the disbursement
   record, exactly like the rejection reasons in SDB's own استلام المطالبات
   screen* (`boq.claim_type_consistent` cites that verbatim practice).
4. Set it back to **Periodic** → gate 2 passes, and the **line-item table**
   shows all 12 contracted lines: 7 billed and matching BoQ prices (each qty
   has a locate button into the PDFs), 5 muted "not billed this period" —
   partial billing is normal for periodic claims, and the table says so.
5. **Step 3:** contract kind is **Goods** (furniture supply), so the
   acceptance document is the delivery note `Delivery-Alwaha-DN-26-0342_real.pdf`
   — no COC for a goods contract (the consultants' point, now built in). The
   three-way match passes. **Step 4:** no penalties; the agent infers delay
   from the dates itself (delivered 2026-06-14 vs contract end 2026-11-30 —
   on time) and passes.
6. **Step 5:** upload the five vendor-file documents (CR, zakat, GOSI, award
   letter, work commencement — all synthetic, all cross-tied: the award
   letter no. 26400871 matches the COC, the CR/VAT numbers match the vendor
   and the invoice QR). The agent **identifies each one and reads its
   identity fields** — no checkboxes; the analyze button stays disabled until
   all seven required documents are covered (contract + BoQ auto-covered by
   the step-2 upload). The results view shows "Documents reviewed" cards with
   the extracted CR number / VAT number / validity and a view button that
   opens each PDF with its identifier highlighted. Withhold one file
   beforehand if you want to show the completeness gate fail honestly.
   Verdict lands on the phase-2-verified happy path.

Defect variants for questions: `Contract-BoQ..._tampered.pdf` (OF-205 priced
290 vs invoiced 320 — price mismatch lights up in the table AND the finding),
`COC..._tampered.pdf` (220,000 vs 230,000), `Delivery..._short.pdf` (OF-205
delivered 80 vs billed 120 → three-way over-billing, claimed 200,000 vs
187,200 of received work).

---

## 4. Every field, justified

### Claims list (`/`)

| Element | Why it exists |
| --- | --- |
| Hero banner + description | Positioning in one sentence; mirrors the prequalification module so the two read as one suite. |
| **Claims from Dynamics 365** table | Mirrors the D365 استلام المطالبات screen from the client's own screenshots — same columns, same VRM- numbering. Message: "we meet your ERP where it is." |
| Claim no. (VRM-xxxxxx) | D365's claim id format. Wizard submissions get a VRM-9xxxxx range so demo data never collides with ERP data. |
| Contract value (base) | The ceiling every progress payment is checked against — **stated excl. VAT, same basis as BoQ line prices** (a deliberate convention; whether SDB contracts state it incl. or excl. VAT is a POC-P01 question we've logged). |
| Claim (incl. VAT) | What the vendor actually asks to be paid — the figure Finance disburses. |
| Review status | Persisted server-side; a closed tab resumes exactly where the review stood. |
| Header: role badge "Vendor Management Specialist / أخصائي إدارة الموردين" | The procedure's step-1 actor (SP-01-04-05-02). Demo build has no auth — the badge shows where roles attach later (deliberately deferred: see pain point 2). |

### Wizard step 1 — Tax invoice

| Field | Why it exists |
| --- | --- |
| Invoice upload → autofill | Division of labor proven in the prequalification agent: **specialist OCR reads, the LLM organizes, Python validates** — LLM-vision alone misreads high-stakes Arabic digits. The reviewer confirms the prefill before anything is checked. |
| Vendor name / account | Claim header fields from the D365 form. |
| PO / Project no. / Project name | ERP-owned context; stays manual until the D365 connector lands (the "Import from Dynamics 365" tile is the visual promise of that connector). |
| Contract value (base) | Feeds the gate-2 ceiling checks. Can be left empty — step 2 suggests it from the BoQ. |
| Invoice no. | Checked against the invoice document itself — with RTL reading-order normalization, because OCR reads `INV-2026-0117` inside an Arabic line as `0117-2026-INV`. |
| Claim date / Payment no. / Claim type (دوري/نهائي) | Payment no. feeds the sequence check; claim type decides whether the "final claim closes the contract" rule applies. |
| Base / VAT / Total | The VAT-math check (total = base + VAT, per the VAT Implementing Regulations) — and total auto-suggests as you type, because the form should help before the gate judges. |

### Wizard step 2 — Contract & BoQ

| Field | Why it exists |
| --- | --- |
| Contract/BoQ upload | The bank's copy — every invoice line is matched against it (item code + unit price). |
| Contract value (base), again | Editable here because this is where the number becomes checkable. Uploading the BoQ **suggests** the summed line total — suggests, never silently sets: a vendor document must not become the ceiling constraining that vendor's own billing without a human confirming it. |
| Disbursed before this claim (excl. VAT) | Prior payments on this contract — cumulative ceiling check. Comes from D365 payment history in production. |
| Prior payments count | Feeds the payment-sequence rule (payment no. must be prior + 1). |

### Wizard step 3 — Acceptance & three-way match

| Element | Why it exists |
| --- | --- |
| Contract kind (set at step 2) | **One acceptance document per contract kind** — goods receipt for goods, Certificate of Completion for works. The feedback from the first showing: "a goods receipt is for goods, a COC is for projects" — exactly right, and now the flow branches on it. |
| Acceptance upload (COC or goods receipt) | The middle document of the three-way match. See §6 below. |
| ERP receipt (when present) | The D365 posting of الاستلام (procedure step 5) is a **cross-check** on the acceptance document, never a second acceptance; when the ERP has one it is authoritative for quantities. |

### Wizard step 4 — Final check (penalties & delay)

| Element | Why it exists |
| --- | --- |
| Penalties on record | ERP-owned project events, entered manually for now (reading them from contract clauses slots in here later). The cross-check of COC answers vs the penalty record **is the client's reported incident** — the procedure has no control here at all. |
| Delay inferred from dates | Contract end date (step 2, suggested from the contract) vs the acceptance date. Independent of what the COC declares and of whether anyone logged a penalty — a third signal on the same incident. |

### Wizard step 5 — Pre-finance package

| Element | Why it exists |
| --- | --- |
| Attachment checklist (7 items) | Procedure step 6 lists attachments **"على سبيل المثال لا الحصر"** ("for example, not limited to") — an open-ended list no auditor can enforce. We fixed the list: contract, BoQ, award letter, work commencement, CR, zakat, GOSI. This mirrors the pre-referral review the VRM director described. |
| VAT treatment | An invoice must either charge VAT or declare exemption — neither is a defect the procedure mentions. |

### Wizard step 6 — Recommendation

| Element | Why it exists |
| --- | --- |
| Verdict + bilingual rationale | GPT writes the reviewer-facing wording **over** the deterministic findings — it can never upgrade a failure (verdict floor). Government context: the agent recommends with citations; it does not decide. |
| Gate summary rows | One-click back into any gate's evidence — the audit-walkthrough view. |

### Everywhere

| Element | Why it exists |
| --- | --- |
| **Source line on every finding** (المصدر) | The audit-defensibility story: each rule cites procedure step, ZATCA regulation, VAT article, procurement law, or the contract itself. Rules are YAML data, not code — adding a rule when POC-P01 arrives touches no pipeline. |
| **Evidence panel with locate buttons** | Every compared value that lives in a document opens the PDF reader and highlights it on the page. Computed values deliberately get no button — the UI never claims a source it doesn't have. |
| Bilingual + RTL throughout | Arabic is first-class (native procurement register, not translation) — the findings are written to be pasted into a rejection letter. |

---

## 5. Pain points taken from their procedure (SPM-PM01 / SP-01-04-05-02)

1. **Steps without criteria.** Step 1 says "verify document completeness" —
   no checklist. Step 2 says "verify the deliverables in the invoice were
   received" — no comparison rule. Step 7 says "review the invoice" — against
   nothing named. The procedure choreographs *who and when*, never *what
   checking means*. → Every gate in the agent is the missing criterion,
   written down and cited.
2. **Procedure vs. practice contradiction.** Their procedure and their own
   recorded walkthrough disagree on who creates/approves the COC. → The flow
   is modeled as ordered *gates*, not job titles; roles attach once the
   client clarifies. (If asked why there's no approval matrix: this is why.)
3. **The incident that motivates gate 3.** A penalized vendor's COC stating
   "no delay / no observations" passed every human gate. The COC is generated
   from vendor-fed data and nobody cross-checks it against the penalty
   record. → `coc.delay_vs_penalties`, cited to the client's own case.
4. **No authenticity control on the invoice.** The word ZATCA never appears;
   nothing in the procedure distinguishes a فاتورة ضريبية from a PDF that
   merely looks like one. → Gate 1's QR decoding + phase-2 signature
   verification.
5. **The open-ended attachment list** ("على سبيل المثال لا الحصر") — audit-weak
   by construction. → A fixed, checkable list at gate 5.
6. **The 11-working-day KPI** (claim registration → referral to payments) is
   defined but nothing in the flow helps meet it — review is the manual
   bottleneck. → The agent compresses the judgment work to minutes; the KPI
   becomes trackable per claim. (Roadmap slide, not built.)
7. **The truncated document itself is a finding:** the procedure manual
   defines choreography only; the actual rules live in POC-P01, contracts,
   and regulation — the client has shared none of these yet. Our rulepacks
   are architected as *data with source citations* precisely so those slot in
   without engineering. **This doubles as the ask list: POC-P01, one or two
   real contracts + BoQs, a D365 access path.**

## 6. Why we added three-way matching

- **Their procedure stages the documents but never compares them.** Step 2
  verifies deliverables (no criterion), step 5 posts the product receipt
  (إيصال استلام المنتجات) in the ERP, step 6 creates the invoice — three
  documents, zero comparison rules. The term "matching" (مطابقة) appears
  nowhere in the 10-page manual. We supply the criterion behind their own
  steps: **contract/BoQ (agreed) ↔ ERP product receipt (received) ↔ invoice
  (billed)** — the works-claims version of classic 3-way matching, where the
  "receipt" is the certified progress rather than a goods GRN.
- **It catches the fraud the other gates can't.** A vendor with correct unit
  prices, correct VAT math, a genuine QR, and internally consistent totals
  still fails if the *work wasn't delivered*: VRM-002402 bills 3 HVAC units
  against 1 received, and claims 380,000 base against 245,000 of received
  work at BoQ prices. No price or authenticity check sees that.
- **Every rule cites its source** like the rest: receipt-present cites the
  procedure itself (SPM-PM01 §9.3 step 5); quantities-vs-received cites the
  Procurement Law Executive Regulations (payment for executed work);
  quantities-vs-BoQ cites the contract.
- **Positioning guardrail:** if asked "doesn't D365 do 3-way matching?" —
  yes, for goods POs (PO ↔ product receipt ↔ vendor invoice). This gate is the
  claims-review version (BoQ prices, progress quantities, cumulative
  ceilings), and it's one gate of five: ZATCA authenticity, cross-document
  contradiction checks, and the citation trail are things no matching policy
  does. Never sell this as "3-way matching" alone — that invites the
  AP-automation comparison; sell the assembled judgment layer.

## 7. Honesty table (what's real vs. mocked — in case the CEO asks)

| Real today | Mocked/deferred |
| --- | --- |
| Azure CU Layout OCR + GPT structuring of the actual PDFs | D365 connector (mock ERP source behind the same interface; OData path is a client ask) |
| ZATCA QR: deterministic TLV decode + phase-2 ECDSA signature verification, offline | Fatoora-platform online verification (policy decision pending POC-P01) |
| All deterministic checks + rulepack citations | Roles/approval routing (blocked on the client's COC contradiction) |
| GPT bilingual recommendation with deterministic verdict floor | D365 product receipt for *wizard* submissions (the wizard reads an uploaded delivery note as the receiving record instead; the ERP receipt is authoritative when present) |
| SQLite persistence, resumable reviews, PDF evidence viewer | Vendor-portal notification loop, KPI tracking (roadmap) |

**Don't claim:** live D365 integration, Fatoora online checks, or POC-P01
compliance (we haven't seen POC-P01). Each is one honest sentence: "lands
with pilot access / the policy document."
