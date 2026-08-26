export type ClaimType = "periodic" | "first" | "final"
/** Decides the acceptance document: goods -> goods receipt, works -> COC. */
export type ContractKind = "goods" | "works"
export type Severity = "ok" | "warn" | "fail"
export type Verdict = "approve" | "reject" | "needs_human"

export interface Stage {
  id: string
  order: number
  title_en: string
  title_ar: string
  desc_en: string
  desc_ar: string
}

export interface ClaimFile {
  path: string
  doc_type: string // invoice | contract_boq | coc | delivery_note | other
}

export interface Claim {
  id: string
  po_no: string
  project_no: string
  project_name_ar: string
  project_name_en: string
  vendor_account: string
  vendor_name_ar: string
  vendor_name_en: string
  contract_value: number // base, excl. VAT — same basis as BoQ line prices
  contract_kind: ContractKind
  contract_end_date: string // contractual completion / delivery date
  claim_amount_base: number
  vat_amount: number
  claim_amount_total: number
  invoice_no: string
  payment_no: number
  claim_type: ClaimType
  claim_date: string
  cumulative_prior: number // disbursed before this claim (base, excl. VAT)
  prior_payment_count: number
  status_ar: string
  source_files: ClaimFile[]
  documents: ClaimDocuments
  origin: "erp" | "submitted"
  /** Guided-review annotations, filled by the API from the progress store. */
  review_step: number // 0 = not started, 1-5 = at that wizard step, 6 = completed
  latest_verdict: Verdict | null
}

export interface InvoiceLine {
  item_code: string
  description_ar: string
  unit_price: number
  quantity: number
  amount: number
}

export interface InvoiceDoc {
  invoice_no: string
  invoice_date: string
  seller_name_ar: string
  seller_vat_number: string
  total_with_vat: number
  vat_amount: number
  vat_exempt: boolean
  qr_payload: string
  lines: InvoiceLine[]
}

export interface CocDoc {
  coc_no: string
  coc_date: string
  claim_amount: number
  has_delay: boolean | null
  has_stoppage: boolean | null
  has_observations: boolean | null
  delay_days: number
}

export interface BoqLine {
  item_code: string
  description_ar: string
  description_en: string
  unit: string
  unit_price: number
  quantity: number
}

export interface ReceiptLine {
  item_code: string
  description_ar: string
  quantity: number
}

/** إيصال استلام المنتجات — the D365 product receipt (procedure step 5). */
export interface ReceiptDoc {
  receipt_no: string
  receipt_date: string
  lines: ReceiptLine[]
}

/** One penalty clause as PRINTED in the contract — the contract's TERMS, not
 *  the ERP record of penalties imposed. text_ar/page anchor the evidence
 *  viewer on the clause. */
export interface PenaltyTerm {
  kind: string // delay | other
  rate_percent: number
  per: string // day | week | "" (flat / cap-style)
  basis: string
  cap_percent: number
  text_ar: string
  ref: string
  page: number // 1-based page of the contract document; 0 = unknown
}

/** Contract/PO header as extracted from the contract document. */
export interface ContractDoc {
  contract_no: string
  start_date: string
  end_date: string
  value_base: number
  penalty_terms: PenaltyTerm[]
}

/** What one contract/BoQ read yields for the step-2 suggestions. */
export interface ContractExtract {
  boq: BoqLine[]
  contract: ContractDoc | null
}

export interface Penalty {
  reason_ar: string
  amount: number
  date: string
}

/** One vendor-file document as identified by the agent at the pre-finance
 *  gate, with the identity fields it printed (CR number, VAT number, ...). */
export interface DetectedAttachment {
  file_name: string
  doc_key: string // a required-attachment key, or "other"
  fields: Record<string, string>
}

export interface ClaimDocuments {
  invoice: InvoiceDoc | null
  coc: CocDoc | null
  receipt: ReceiptDoc | null
  boq: BoqLine[]
  contract: ContractDoc | null
  penalties: Penalty[]
  attachments: string[]
  detected_attachments: DetectedAttachment[]
}

export interface QrField {
  key: string
  label_en: string
  label_ar: string
  value: string
  expected: string
  match: boolean | null
}

export interface QrSummary {
  present: boolean
  valid_tlv: boolean
  error: string
  fields: QrField[]
  phase2_status: string
  phase2_problems: string[]
  phase2_notes: string[]
  has_stamp: boolean
}

export interface RuleSource {
  doc: string
  ref: string
}

export interface Finding {
  rule_id: string
  gate: string
  severity: Severity
  title_en: string
  title_ar: string
  detail_en: string
  detail_ar: string
  source: RuleSource
  evidence: Record<string, unknown>
}

export interface GateRun {
  gate: string
  severity: Severity
  findings: Finding[]
}

export interface RunResult {
  claim_id: string
  gates: GateRun[]
  verdict: Verdict
  rationale_en: string
  rationale_ar: string
  extracted: ClaimDocuments | null
  qr: QrSummary | null
}
