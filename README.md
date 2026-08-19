# Claim Integrity Agent

AI-assisted integrity review for vendor claims (مطالبات) at a Saudi government
entity running Microsoft Dynamics 365 F&O. The ERP workflow already exists —
this agent supplies the *judgment* at the review gates: matching the invoice
against the BoQ/contract, three-way matching it against the ERP product
receipt (contract ↔ استلام ↔ فاتورة), verifying the فاتورة ضريبية is genuine
(ZATCA QR), and catching cross-document contradictions (e.g. penalized vendor
but a COC stating "no delay"). The human keeps approve/reject authority; every finding
cites its normative source, which is the audit-defensibility story.

## Design decisions

- **Role-agnostic.** The client's procedure (SP-01-04-05-02) and their
  recorded walkthrough contradict each other on who creates/approves the COC,
  so the flow is modeled as ordered **gates** (`backend/app/domain/stages.py`),
  not job titles. Attach roles later once the client clarifies.
- **Rules-as-data.** The procedure defines choreography only; the actual rules
  live in ZATCA regulations, the procurement law, each contract/BoQ, and the
  client policy POC-P01 (not yet provided). Rules are YAML
  (`backend/app/services/rules/rulepacks/`) with per-rule source citations;
  checks are deterministic Python (`rules/engine.py`).
- **ERP is the data source.** Documents live in D365; the demo runs on a
  seeded mock behind the same interface (`services/datasource.py`).
- **Division of labor** (proven in the prequalification agent): specialist OCR
  reads, LLM organizes, **Python validates**, human signs off. Two engine sets:
  `EXTRACTOR_ENGINE=mock` / `JUDGE_ENGINE=mock` run entirely on deterministic
  checks (no AI calls, reproducible — tests are pinned to these);
  `EXTRACTOR_ENGINE=azure` runs CU Layout OCR (disk-cached by content hash
  under `backend/.cache/cu/`) + GPT structuring over the claim's
  `source_files`, with the ZATCA QR decoded deterministically off the invoice
  PDF, and `JUDGE_ENGINE=gpt` writes the bilingual recommendation over the
  deterministic findings (never overriding them). Claim `VRM-002401` is wired
  to the real ESNAD document chain in `supporting_docs/example_documents/`.

## Run

```bash
# backend (http://localhost:8000)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# ui (http://localhost:5173, proxies /api to :8000)
cd ui
npm install
npm run dev
```

Seed data: `VRM-002401` passes everything; `VRM-002402` fails all five gates
(fake ZATCA QR, BoQ unit-price deviation, payment-sequence gap, over-billing
vs the ERP product receipt in the three-way match, COC/penalty contradiction,
missing zakat certificate).

UI-testing the wizard by hand: `supporting_docs/test_scenarios/README.md`
groups the fixture documents by vendor entity (Pearls/ESNAD pass chain,
Modern Construction all-gates-fail chain, Al-Ufuq QR matrix) with the exact
form values and expected outcome per run.

## Demo flows

Two ways to show the same pipeline (client demo):

1. **Integration-agnostic wizard** (`/submit`, built): the reviewer submits
   the claim header fields (mirroring the D365 استلام المطالبات form from the
   client screenshots) plus the document package, watches the analysis run,
   and reads the findings with evidence — no ERP integration implied.
   Submitted claims get `VRM-9xxxxx` ids, files land in `backend/uploads/`
   (git-ignored, in-memory registry — restarts clean), and flow through the
   identical pipeline via `POST /api/submissions`.
2. **"Integrates with your Dynamics" route** (not built yet): a mock D365
   portal where a vendor submits and the claim appears in the ERP claims
   table — the current `/` claims list is already the second half of this.

## Reuse from `../pre-qualification-agent`

| Need | Where it lives there |
| --- | --- |
| CU Layout OCR + GPT structuring engines | `backend/app/services/analyzer/` (factory, cu_client, *_analyzer) |
| QR bitmap extraction from PDFs | `backend/app/services/qr_extraction.py` (pypdfium2 + zxing-cpp) |
| LLM judge over precomputed findings | `backend/app/services/judge/` |
| Background jobs + progress polling | `backend/app/jobs/queue.py`, `services/progress.py` |
| Auth/roles, audit log | `backend/app/services/auth`, `services/audit.py` |
| PDF evidence viewer, upload progress | `ui/src/components/PdfViewerPanel.tsx`, `UploadProgressBar.tsx` |
| Bilingual/RTL layer, theme, buttons | `ui/src/lib/i18n.tsx`, `components/theme-provider.tsx`, `components/ui/` |

## Open items (client asks)

- **POC-P01** (contracts & procurement policy) — slots into the rulepacks.
- One or two real/anonymized **contracts + BoQs** (drives the matching format).
- D365 access path (OData / custom services) for the `d365` ERP adapter.
- Clarify COC creator/approver (procedure vs. practice) before wiring roles.
- ZATCA verification depth: offline TLV checks (current) vs. Fatoora platform.
