import { FileSearch } from "lucide-react"

import { StatusPill } from "@/components/StatusPill"
import { usePdfViewer } from "@/components/PdfViewerContext"
import { docIndexFor } from "@/lib/evidence"
import { useLang } from "@/lib/i18n"
import type { Claim, ClaimDocuments, Finding } from "@/types/domain"

/** Rulepack refs are written in English shorthand ("step 1", "art. 53");
 *  render the common tokens in Arabic so the audit line reads natively. */
const REF_AR: [RegExp, string][] = [
  [/\bsteps\b/g, "الخطوات"],
  [/\bstep\b/g, "الخطوة"],
  [/\bart\.\s*/g, "المادة "],
  [/\bBoQ quantities\b/g, "كميات جدول الكميات"],
  [/\bBoQ\b/g, "جدول الكميات"],
  [/\bpayment schedule\b/g, "جدول الدفعات"],
  [/\bprogress payments\b/g, "الدفعات المرحلية"],
  [/\bfinal settlement\b/g, "التسوية النهائية"],
  [/\bpayment for executed work\b/g, "الدفع مقابل الأعمال المنفذة"],
  [/\bexemptions\b/g, "الإعفاءات"],
  [/\bTLV tags 1-5\b/g, "حقول TLV من 1 إلى 5"],
  [/\bphase-1 QR requirement\b/g, "متطلب رمز QR للمرحلة الأولى"],
  [/\bphase-2 security features, integration waves\b/g, "خصائص أمان المرحلة الثانية، موجات الربط"],
  [/\bclient-reported contradiction case\b/g, "حالة التناقض المبلغ عنها من البنك"],
  [/\bduration \/ delivery date; GTPL delay penalties\b/g, "المدة / تاريخ التسليم؛ غرامات التأخير في نظام المنافسات"],
  [/\bpenalty clauses \(delay rate \/ ceiling\)\b/g, "بنود الغرامات (نسبة التأخير / الحد الأقصى)"],
]

/** Normative source names as the reviewer knows them; codes stay as codes. */
const DOC_AR: Record<string, string> = {
  contract: "العقد",
  "Client rejection practice (D365)": "أسباب الرفض المعتمدة في النظام (D365)",
  "Procurement Law Exec. Regulations": "اللائحة التنفيذية لنظام المنافسات والمشتريات الحكومية",
  "VAT Implementing Regulations": "اللائحة التنفيذية لنظام ضريبة القيمة المضافة",
  "ZATCA e-invoicing": "لائحة الفوترة الإلكترونية — هيئة الزكاة والضريبة والجمارك",
  "internal audit": "المراجعة الداخلية",
}

function localizeDoc(doc: string): string {
  return DOC_AR[doc] ?? doc
}

function localizeRef(ref: string): string {
  return REF_AR.reduce((s, [re, ar]) => s.replace(re, ar), ref)
}

/** One rule result with its normative source — the audit-trail unit.
 *
 * Evidence renders as labelled rows instead of raw JSON; every value that
 * lives in a staged document gets a locate button that opens the embedded
 * reader and highlights the value on the page it was read from.
 */

type Leaf = { path: string[]; value: string | number | boolean | null; siblings: Record<string, unknown> }

function flatten(value: unknown, path: string[], siblings: Record<string, unknown>, out: Leaf[]): void {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    out.push({ path, value: value as Leaf["value"], siblings })
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => flatten(item, [...path, String(i)], siblings, out))
    return
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>
    for (const [k, v] of Object.entries(obj)) flatten(v, [...path, k], obj, out)
  }
}

function flattenEvidence(evidence: Record<string, unknown>): Leaf[] {
  const out: Leaf[] = []
  flatten(evidence, [], evidence, out)
  return out
}

/** Which staged document a leaf's value should be located in, if any. */
function targetDocType(gate: string, path: string[]): string | null {
  const p = path.join(".").toLowerCase()
  // Values typed into the claim form / ERP data / computed by the check —
  // nothing to open. Checked FIRST so computed figures never get a button.
  if (/(^|\.)(claim|expected|actual|missing|problems|issues|notes|cumulative|remaining|difference|payment_no|penalties|receipt|received|certified|cap_amount|delay_days)/.test(p))
    return null
  // The contract's penalty clauses — read from the contract document.
  if (p.includes("contract_penalty")) return "contract_boq"
  if (p.includes("contract_value") || p.includes("contract_end") || p.includes("boq")) return "contract_boq"
  if (p.includes("billed") || p.includes("invoice") || p.includes("qr")) return "invoice"
  if (p.includes("coc")) return "coc"
  if (p.includes("completion_date")) return "coc"
  const defaults: Record<string, string> = { intake: "invoice", final_check: "coc" }
  return defaults[gate] ?? null
}

/** Bilingual labels for evidence keys — the reviewer reads field names, not
 *  the checks' JSON. Unknown keys fall back to prettified snake_case. */
const EVIDENCE_LABELS: Record<string, { en: string; ar: string }> = {
  cumulative_base: { en: "Cumulative claimed (excl. VAT)", ar: "إجمالي المطالبات (بدون الضريبة)" },
  contract_value: { en: "Contract value (base)", ar: "قيمة العقد الأساسية" },
  remaining: { en: "Remaining contract value", ar: "المتبقي من قيمة العقد" },
  difference: { en: "Difference", ar: "الفارق" },
  expected: { en: "Expected", ar: "المتوقع" },
  actual: { en: "Actual", ar: "الفعلي" },
  claim: { en: "On the claim form", ar: "في نموذج المطالبة" },
  invoice: { en: "On the invoice", ar: "في الفاتورة" },
  boq: { en: "In the BoQ", ar: "في جدول الكميات" },
  missing: { en: "Missing", ar: "الناقص" },
  problems: { en: "Problems", ar: "الملاحظات" },
  issues: { en: "Issues", ar: "الملاحظات" },
  notes: { en: "Notes", ar: "ملاحظات" },
  error: { en: "Error", ar: "الخطأ" },
  qr_fields: { en: "QR code", ar: "رمز الاستجابة" },
  seller_name: { en: "Seller name", ar: "اسم البائع" },
  vat_number: { en: "VAT number", ar: "الرقم الضريبي" },
  timestamp: { en: "Timestamp", ar: "طابع الوقت" },
  total: { en: "Total (incl. VAT)", ar: "الإجمالي شامل الضريبة" },
  vat: { en: "VAT amount", ar: "قيمة الضريبة" },
  mismatches: { en: "BoQ mismatches", ar: "بنود مخالفة للجدول" },
  item: { en: "Item code", ar: "رقم البند" },
  issue: { en: "Issue", ar: "المخالفة" },
  payment_no: { en: "Payment no.", ar: "رقم الدفعة" },
  coc: { en: "COC amount", ar: "مبلغ محضر الإنجاز" },
  penalties_total: { en: "Penalties total", ar: "إجمالي الغرامات" },
  coc_has_delay: { en: "COC: delay declared?", ar: "المحضر: تأخير؟" },
  coc_has_stoppage: { en: "COC: stoppage declared?", ar: "المحضر: إيقاف؟" },
  coc_has_observations: { en: "COC: observations declared?", ar: "المحضر: ملاحظات؟" },
  receipt_no: { en: "Product receipt no.", ar: "رقم إيصال الاستلام" },
  receipt_date: { en: "Receipt date", ar: "تاريخ الإيصال" },
  lines: { en: "Lines", ar: "البنود" },
  billed: { en: "Billed qty", ar: "الكمية المفوترة" },
  received: { en: "Received qty", ar: "الكمية المستلمة" },
  boq_qty: { en: "BoQ qty", ar: "الكمية في الجدول" },
  claimed_base: { en: "Claimed (excl. VAT)", ar: "المطالبة (بدون الضريبة)" },
  contract_end: { en: "Contract end date", ar: "تاريخ نهاية العقد" },
  completion_date: { en: "Acceptance date", ar: "تاريخ الاستلام" },
  delay_days: { en: "Delay (days)", ar: "التأخير (أيام)" },
  coc_no: { en: "COC no.", ar: "رقم محضر الإنجاز" },
  coc_date: { en: "COC date", ar: "تاريخ محضر الإنجاز" },
  claim_type: { en: "Claim type", ar: "نوع المستخلص" },
  remaining_after_claim: { en: "Remaining after this claim", ar: "المتبقي بعد هذه المطالبة" },
  prior_payment_count: { en: "Prior payments", ar: "الدفعات السابقة" },
  certified_value: { en: "Value of received work", ar: "قيمة الأعمال المستلمة" },
  contract_penalty: { en: "Contract penalty clause", ar: "بند الغرامات في العقد" },
  rate_percent: { en: "Delay penalty rate", ar: "نسبة غرامة التأخير" },
  per: { en: "Per", ar: "لكل" },
  basis: { en: "Applied to", ar: "تُحتسب من" },
  cap_percent: { en: "Penalty ceiling", ar: "الحد الأقصى للغرامات" },
  clause_ref: { en: "Clause", ar: "البند" },
  cap_amount: { en: "Ceiling amount", ar: "مبلغ الحد الأقصى" },
  expected_penalty: { en: "Expected penalty (computed)", ar: "الغرامة المتوقعة (محسوبة)" },
}

const HIDDEN_LEAVES = /(^|\.)(bidi_normalized|has_zatca_stamp|status)$/

/** Where the extractor read a leaf's value: a line-level leaf (a BoQ /
 *  invoice / receipt row identified by its item code) cites the row's page
 *  and file; anything else cites the document header's. page 0 = unknown. */
function locationFor(
  docType: string | null,
  leaf: Leaf,
  extracted?: ClaimDocuments | null
): { page: number; source_file?: string } {
  if (!docType || !extracted) return { page: 0 }
  const item = leaf.siblings["item"] ?? leaf.siblings["item_code"]
  const code = typeof item === "string" || typeof item === "number" ? String(item) : ""
  type Row = { item_code: string; page?: number; source_file?: string }
  type Header = { page?: number; source_file?: string } | null | undefined
  const loc = (rows: Row[] | undefined, header: Header) => {
    const row = code ? rows?.find((r) => r.item_code === code) : undefined
    const src = row ?? header
    return { page: src?.page || 0, source_file: src?.source_file || undefined }
  }
  switch (docType) {
    case "invoice":
      return loc(extracted.invoice?.lines, extracted.invoice)
    case "contract_boq":
      return loc(extracted.boq, extracted.contract)
    case "coc":
      return loc(undefined, extracted.coc)
    case "delivery_note":
      return loc(extracted.receipt?.lines, extracted.receipt)
    default:
      return { page: 0 }
  }
}

export function FindingCard({
  finding,
  claim,
  extracted,
}: {
  finding: Finding
  claim?: Claim | null
  /** The run's extracted documents — the penalty-clause leaves use the term's
   *  page + verbatim text as OCR-locate anchors on the scanned contract. */
  extracted?: ClaimDocuments | null
}) {
  const { t, pick, lang } = useLang()
  const { openDocument } = usePdfViewer()

  const leaves = flattenEvidence(finding.evidence).filter(
    (l) => !HIDDEN_LEAVES.test(l.path.join("."))
  )

  /** "lines.0.billed" -> "Lines › #1 › Billed qty" (bilingual). */
  const humanPath = (path: string[]): string =>
    path
      .map((seg) => {
        if (/^\d+$/.test(seg)) return `#${Number(seg) + 1}`
        const label = EVIDENCE_LABELS[seg]
        return label ? pick(label.en, label.ar) : seg.replace(/_/g, " ")
      })
      .join(" › ")

  const locate = (leaf: Leaf) => {
    if (!claim) return
    const docType = targetDocType(finding.gate, leaf.path)
    const isPenalty = leaf.path.join(".").includes("contract_penalty")
    const penaltyTerm = isPenalty
      ? (extracted?.contract?.penalty_terms ?? []).find(
          (pt) => pt.ref === leaf.siblings["clause_ref"] || pt.rate_percent === leaf.siblings["rate_percent"]
        )
      : undefined
    const where = locationFor(docType, leaf, extracted)
    // Several files may be staged for a doc type — open the one the value was read from.
    const index = docIndexFor(claim, penaltyTerm?.source_file ?? where.source_file, docType ? [docType] : [])
    if (index === -1) return
    const file = claim.source_files[index]
    // The row's item/identifier sibling is marked too, so a unit price lands
    // next to its BoQ line rather than a bare number somewhere on the page.
    const also = ["item", "item_code", "invoice_no"]
      .map((k) => leaf.siblings[k])
      .filter((v): v is string | number => typeof v === "string" || typeof v === "number")
      .map(String)
    // Percent leaves highlight the printed form ("10%"); a penalty-clause leaf
    // also carries its page + verbatim clause so the scanned contract's
    // OCR-locate lands straight on the clause.
    const key = leaf.path[leaf.path.length - 1] ?? ""
    const highlight = key.endsWith("_percent") ? `${leaf.value}%` : String(leaf.value)
    const term = penaltyTerm
    openDocument({
      claimId: claim.id,
      index,
      fileName: file.path.split("/").pop(),
      page: term?.page || where.page || undefined,
      highlight,
      highlightAlso: also,
      highlightExtra: term?.text_ar || undefined,
      fieldName: `${pick(finding.title_en, finding.title_ar)} — ${humanPath(leaf.path)}`,
    })
  }

  const canLocate = (leaf: Leaf): boolean => {
    if (!claim || leaf.value === null || typeof leaf.value === "boolean") return false
    if (String(leaf.value).trim() === "") return false
    // Enum-ish clause fields ("per": day/week, "kind") are never page text.
    if (["per", "kind"].includes(leaf.path[leaf.path.length - 1] ?? "")) return false
    const docType = targetDocType(finding.gate, leaf.path)
    return docType !== null && claim.source_files.some((f) => f.doc_type === docType)
  }

  const display = (v: Leaf["value"]): string => {
    if (v === null) return "—"
    if (typeof v === "boolean") return v ? t("yes", "نعم") : t("no", "لا")
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { minimumFractionDigits: 2 })
    return String(v)
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{pick(finding.title_en, finding.title_ar)}</div>
          <p className="text-muted-foreground mt-1 text-sm">{pick(finding.detail_en, finding.detail_ar)}</p>
        </div>
        <StatusPill status={finding.severity} className="shrink-0" />
      </div>
      <div className="text-muted-foreground mt-2 text-xs">
        {t("Source", "المصدر")}: {lang === "ar" ? localizeDoc(finding.source.doc) : finding.source.doc}
        {finding.source.ref ? ` — ${lang === "ar" ? localizeRef(finding.source.ref) : finding.source.ref}` : ""}
      </div>
      {leaves.length > 0 && (
        <details className="mt-1.5">
          <summary className="text-muted-foreground cursor-pointer select-none text-xs hover:text-foreground">
            {t("Evidence — values compared", "الأدلة — القيم المقارنة")}
          </summary>
          <div className="border-border/70 mt-1.5 overflow-hidden rounded-md border">
            {leaves.map((leaf, i) => (
              <div
                key={i}
                className="border-border/70 bg-muted/40 flex items-center gap-2 border-b px-2.5 py-1.5 text-xs last:border-0"
              >
                <span className="text-muted-foreground min-w-0 flex-1 truncate" dir="auto">
                  {humanPath(leaf.path)}
                </span>
                <span className="max-w-[45%] truncate font-medium tabular-nums" dir="auto">
                  {display(leaf.value)}
                </span>
                {canLocate(leaf) && (
                  <button
                    type="button"
                    onClick={() => locate(leaf)}
                    className="text-primary hover:bg-secondary shrink-0 rounded p-1 transition"
                    aria-label={t("Locate in document", "تحديد الموضع في المستند")}
                    title={t("Locate in document", "تحديد الموضع في المستند")}
                  >
                    <FileSearch className="size-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
