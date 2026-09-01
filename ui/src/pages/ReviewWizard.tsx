import * as React from "react"
import { Link, useParams } from "react-router-dom"
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  Check,
  CircleDashed,
  ClipboardCheck,
  Database,
  Download,
  FileSpreadsheet,
  GitCompareArrows,
  Loader2,
  PackageCheck,
  Pencil,
  Plus,
  ReceiptText,
  Sparkles,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react"

import { AttachmentCards, REQUIRED_ATTACHMENTS } from "@/components/AttachmentCards"
import { FindingCard } from "@/components/FindingCard"
import { LineItemsTable } from "@/components/LineItemsTable"
import { PenaltyTermsCard } from "@/components/PenaltyTermsCard"
import { QrPanel } from "@/components/QrPanel"
import { StatusPill } from "@/components/StatusPill"
import { Button } from "@/components/ui/button"
import { api, exportUrl } from "@/lib/api"
import { useLang } from "@/lib/i18n"
import { cn, formatMoney } from "@/lib/utils"
import type { Claim, DetectedAttachment, GateRun, RunResult, Stage } from "@/types/domain"

/**
 * Guided claim review — one step per review gate, mirroring the procedure
 * (SP-01-04-05-02). Serves both entry points:
 *   /submit      -> fresh intake (creates the claim at step 1)
 *   /claims/:id  -> resumes an existing claim at its persisted step
 * Progress is stored server-side (SQLite) after every analyze/continue, so a
 * closed tab reopens exactly where the review stood. ERP-sourced claims run
 * the same steps read-only (their data & documents come from D365/mock).
 */

const inputCls =
  "h-8 w-full rounded-lg border border-border bg-card px-2.5 text-sm outline-none transition-all focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60"

function Field({
  label,
  children,
  className,
}: {
  label: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <label className={cn("block", className)}>
      <span className="text-muted-foreground mb-1 block text-xs">{label}</span>
      {children}
    </label>
  )
}

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      {desc && <p className="text-muted-foreground mt-0.5 text-xs">{desc}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function UploadSlot({
  label,
  hint,
  file,
  onPick,
  required,
  busy,
  disabled,
  className,
}: {
  label: string
  hint: string
  file: File | null
  onPick: (f: File | null) => void
  required?: boolean
  busy?: boolean
  disabled?: boolean
  className?: string
}) {
  return (
    <label
      className={cn(
        "flex items-center gap-3 rounded-lg border border-dashed border-border p-3",
        disabled ? "opacity-70" : "hover:bg-muted/50 cursor-pointer",
        className
      )}
    >
      {busy ? (
        <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
      ) : (
        <Upload className="text-muted-foreground size-4 shrink-0" />
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">
          {label}
          {required && <span className="text-destructive"> *</span>}
        </span>
        <span className="text-muted-foreground block truncate text-xs">{file?.name ?? hint}</span>
      </span>
      {!disabled && (
        <input
          type="file"
          className="hidden"
          accept=".pdf,.docx,.png,.jpg,.jpeg"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
        />
      )}
    </label>
  )
}

interface PenaltyRow {
  reason_ar: string
  amount: string
  date: string
}

type StepNo = 1 | 2 | 3 | 4 | 5 | 6
type GateStepNo = 1 | 2 | 3 | 4 | 5
type Phase = "form" | "running" | "results"

/** Which gate each step runs, and the cumulative set re-validated with it. */
const GATE_BY_STEP: Record<GateStepNo, string> = {
  1: "intake",
  2: "boq_match",
  3: "three_way_match",
  4: "final_check",
  5: "prefinance",
}
const CUM_GATES: Record<GateStepNo, string[]> = {
  1: ["intake"],
  2: ["intake", "boq_match"],
  3: ["intake", "boq_match", "three_way_match"],
  4: ["intake", "boq_match", "three_way_match", "final_check"],
  5: ["intake", "boq_match", "three_way_match", "final_check", "prefinance"],
}

const STEP_LABELS: { no: StepNo; en: string; ar: string; icon: React.ElementType }[] = [
  { no: 1, en: "Tax invoice", ar: "الفاتورة الضريبية", icon: ReceiptText },
  { no: 2, en: "Contract & BoQ", ar: "العقد وجدول الكميات", icon: FileSpreadsheet },
  { no: 3, en: "Acceptance & three-way match", ar: "الاستلام والمطابقة الثلاثية", icon: GitCompareArrows },
  { no: 4, en: "Final check", ar: "الفحص النهائي", icon: ClipboardCheck },
  { no: 5, en: "Pre-finance package", ar: "الملف قبل المالية", icon: PackageCheck },
  { no: 6, en: "Recommendation", ar: "التوصية", icon: BadgeCheck },
]

/** Loader narration per step — what the pipeline is actually doing. */
const RUN_PHASES: Record<GateStepNo, { en: string; ar: string }[]> = {
  1: [
    { en: "Reading the tax invoice", ar: "قراءة الفاتورة الضريبية" },
    { en: "Structuring extracted fields", ar: "هيكلة الحقول المستخرجة" },
    { en: "Decoding & verifying the ZATCA QR", ar: "فك رمز QR والتحقق منه وفق متطلبات هيئة الزكاة والضريبة" },
    { en: "Intake & authenticity rules", ar: "قواعد الاستلام والتحقق من الصحة" },
  ],
  2: [
    { en: "Reading the contract / BoQ", ar: "قراءة العقد / جدول الكميات" },
    { en: "Structuring BoQ lines", ar: "هيكلة بنود جدول الكميات" },
    { en: "Matching invoice lines to the BoQ", ar: "مطابقة بنود الفاتورة مع الجدول" },
    { en: "Contract value & payment sequence rules", ar: "قواعد قيمة العقد وتسلسل الدفعات" },
  ],
  3: [
    { en: "Reading the acceptance document (receipt / COC)", ar: "قراءة مستند الاستلام (إيصال الاستلام / محضر الإنجاز)" },
    { en: "Reconciling contract ↔ acceptance ↔ invoice", ar: "مطابقة العقد ومستند الاستلام والفاتورة" },
    { en: "Three-way matching rules", ar: "قواعد المطابقة الثلاثية" },
  ],
  4: [
    { en: "Inferring delay from contract & acceptance dates", ar: "استنتاج التأخير من تواريخ العقد والاستلام" },
    { en: "Measuring the penalty record against the contract's clauses", ar: "مطابقة سجل الغرامات مع بنود الغرامات في العقد" },
    { en: "Cross-checking penalties & declared project events", ar: "مطابقة الغرامات مع وقائع المشروع المصرّح بها" },
    { en: "Final-check rules", ar: "قواعد الفحص النهائي" },
  ],
  5: [
    { en: "Checking attachment completeness", ar: "التحقق من اكتمال المرفقات" },
    { en: "Verifying VAT treatment", ar: "التحقق من معالجة الضريبة" },
    { en: "Compiling the recommendation", ar: "صياغة التوصية" },
  ],
}

const numStr = (v: number): string => (v ? String(v) : "")

export function ReviewWizard({ existing, initialRun }: { existing?: Claim; initialRun?: RunResult | null }) {
  const { t, pick } = useLang()

  // ERP-sourced records are read-only: fields & documents come from D365/mock;
  // the wizard still runs their gates step by step.
  const ro = existing?.origin === "erp"

  const initialStep: StepNo = existing
    ? ((Math.min(Math.max(existing.review_step, 1), initialRun ? 6 : 5) as StepNo))
    : 1

  const [step, setStep] = React.useState<StepNo>(initialStep)
  // Furthest step the reviewer has reached — revisiting an earlier step must
  // not "un-complete" the later ones or discard their cached gate results.
  const [maxStep, setMaxStep] = React.useState<StepNo>(initialStep)
  const [phase, setPhase] = React.useState<Phase>(() =>
    initialRun && initialStep <= 5 && initialRun.gates.some((g) => g.gate === GATE_BY_STEP[initialStep as GateStepNo])
      ? "results"
      : "form"
  )
  const [stages, setStages] = React.useState<Stage[]>([])
  const [error, setError] = React.useState("")
  const [d365Open, setD365Open] = React.useState(false)
  const [phaseIdx, setPhaseIdx] = React.useState(0)

  // -- step 1: claim header + tax invoice ------------------------------------
  const [fields, setFields] = React.useState(() => ({
    vendor_name_en: existing?.vendor_name_en ?? "",
    vendor_account: existing?.vendor_account ?? "",
    po_no: existing?.po_no ?? "",
    project_no: existing?.project_no ?? "",
    project_name_en: existing?.project_name_en ?? "",
    contract_value: numStr(existing?.contract_value ?? 0),
    contract_kind: existing?.contract_kind ?? "works",
    contract_end_date: existing?.contract_end_date ?? "",
    invoice_no: existing?.invoice_no ?? "",
    claim_date: existing?.claim_date ?? "",
    payment_no: existing ? String(existing.payment_no || 1) : "1",
    claim_type: existing?.claim_type ?? "periodic",
    claim_amount_base: numStr(existing?.claim_amount_base ?? 0),
    vat_amount: numStr(existing?.vat_amount ?? 0),
    claim_amount_total: numStr(existing?.claim_amount_total ?? 0),
  }))
  const [vendorNameAr, setVendorNameAr] = React.useState(existing?.vendor_name_ar ?? "")
  const [totalTouched, setTotalTouched] = React.useState(!!existing)
  const [invoiceFile, setInvoiceFile] = React.useState<File | null>(null)
  const [extracting, setExtracting] = React.useState(false)
  const [autofillNote, setAutofillNote] = React.useState<"filled" | "manual" | "">("")
  // "" = the picked file read as an invoice; otherwise what the read says the
  // document resembles ("contract" | "coc" | "receipt" | "unknown").
  const [notInvoice, setNotInvoice] = React.useState("")

  // -- step 2: contract / BoQ + payment history ------------------------------
  // The contract/BoQ slot takes SEVERAL files (contract, BoQ, appendices):
  // the agent reads them in parallel and fuses them into one contract view.
  const [boqFiles, setBoqFiles] = React.useState<File[]>([])
  const [boqFields, setBoqFields] = React.useState(() => ({
    cumulative_prior: existing ? String(existing.cumulative_prior || 0) : "0",
    prior_payment_count: existing ? String(existing.prior_payment_count || 0) : "0",
  }))
  const [boqExtracting, setBoqExtracting] = React.useState(false)
  const [boqNote, setBoqNote] = React.useState<"suggested" | "kept" | "">("")
  const [endDateNote, setEndDateNote] = React.useState(false)

  /** On BoQ pick: read it and SUGGEST the summed line total as the contract
   *  value when the reviewer hasn't provided one — a vendor document never
   *  silently becomes the ceiling that constrains the vendor's own billing,
   *  so the value lands in an editable field for the reviewer to confirm. */
  const onPickBoq = async (list: FileList | null) => {
    const fresh = [...(list ?? [])].filter((f) => !boqFiles.some((e) => e.name === f.name))
    if (!fresh.length) return
    const all = [...boqFiles, ...fresh]
    setBoqFiles(all)
    setBoqNote("")
    setBoqExtracting(true)
    try {
      const read = await api.extractBoq(all)
      // The contract's own printed pre-VAT value is the ceiling; the summed
      // BoQ lines only stand in when no header value was read (a bare BoQ
      // upload) — a long BoQ's sum carries recap/duplicate rows.
      const printed = read?.contract?.value_base ?? 0
      const total = printed > 0 ? printed : (read?.boq ?? []).reduce((sum, l) => sum + l.unit_price * l.quantity, 0)
      if (total > 0) {
        if (!(parseFloat(fields.contract_value) > 0)) {
          setFields((prev) => ({ ...prev, contract_value: total.toFixed(2) }))
          setBoqNote("suggested")
        } else {
          setBoqNote("kept")
        }
      }
      // Contract end date: suggest from the header, never overwrite a value
      // the reviewer already typed. The suggestion is flagged for explicit
      // confirmation — it feeds the step-4 delay inference, and a wrong date
      // fabricates delay (or hides real delay) downstream.
      const end = read?.contract?.end_date ?? ""
      if (/^\d{4}-\d{2}-\d{2}/.test(end) && !fields.contract_end_date) {
        setFields((prev) => (prev.contract_end_date ? prev : { ...prev, contract_end_date: end.slice(0, 10) }))
        setEndDateNote(true)
      }
    } catch {
      // Suggestion only — extraction failure leaves the form fully manual.
    } finally {
      setBoqExtracting(false)
    }
  }

  const removeBoqFile = (name: string) => setBoqFiles((prev) => prev.filter((f) => f.name !== name))

  // -- step 3: COC + penalties on record -------------------------------------
  const [cocFile, setCocFile] = React.useState<File | null>(null)
  const [penalties, setPenalties] = React.useState<PenaltyRow[]>(() =>
    (existing?.documents.penalties ?? []).map((p) => ({
      reason_ar: p.reason_ar,
      amount: String(p.amount),
      date: p.date,
    }))
  )

  // -- step 4/5: delivery note + vendor-file documents ------------------------
  const [deliveryFile, setDeliveryFile] = React.useState<File | null>(null)
  const [otherFiles, setOtherFiles] = React.useState<File[]>([])
  const [attachFiles, setAttachFiles] = React.useState<File[]>([])
  const [attachDetections, setAttachDetections] = React.useState<DetectedAttachment[]>(
    existing?.documents.detected_attachments ?? []
  )
  const [attachExtracting, setAttachExtracting] = React.useState(false)

  /** On vendor-file pick: identify each new document right away (GPT over the
   *  OCR text, filename heuristic as backstop) so the reviewer sees what the
   *  agent recognized before running the gate. */
  const onPickAttachments = async (list: FileList | null) => {
    const fresh = [...(list ?? [])].filter((f) => !attachFiles.some((e) => e.name === f.name))
    if (!fresh.length) return
    setAttachFiles((prev) => [...prev, ...fresh])
    setAttachExtracting(true)
    try {
      const det = await api.extractAttachments(fresh)
      setAttachDetections((prev) => [
        ...prev.filter((d) => !det.some((n) => n.file_name === d.file_name)),
        ...det,
      ])
    } catch {
      // Detection failure never blocks the flow — file in as unidentified.
      setAttachDetections((prev) => [
        ...prev,
        ...fresh.map((f) => ({ file_name: f.name, doc_key: "other", fields: {} })),
      ])
    } finally {
      setAttachExtracting(false)
    }
  }

  const removeAttachment = (fileName: string) => {
    setAttachFiles((prev) => prev.filter((f) => f.name !== fileName))
    setAttachDetections((prev) => prev.filter((d) => d.file_name !== fileName))
  }

  // -- pipeline state --------------------------------------------------------
  const [claim, setClaim] = React.useState<Claim | null>(existing ?? null)
  const [run, setRun] = React.useState<RunResult | null>(initialRun ?? null)

  React.useEffect(() => {
    api.stages().then(setStages).catch((e) => setError(String(e)))
  }, [])

  const stagedName = (docType: string): string | undefined =>
    claim?.source_files.find((f) => f.doc_type === docType)?.path.split("/").pop()
  const stagedNames = (docType: string): string[] =>
    (claim?.source_files ?? []).filter((f) => f.doc_type === docType).map((f) => f.path.split("/").pop() ?? "")

  const set = (key: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const value = e.target.value
    setFields((f) => {
      const next = { ...f, [key]: value }
      // Suggest total = base + VAT until the reviewer overrides it by hand.
      if ((key === "claim_amount_base" || key === "vat_amount") && !totalTouched) {
        const sum = (parseFloat(next.claim_amount_base) || 0) + (parseFloat(next.vat_amount) || 0)
        next.claim_amount_total = sum ? sum.toFixed(2) : ""
      }
      return next
    })
  }

  /** On invoice pick: read it immediately and prefill the claim fields the
   *  document itself carries. Header context (PO, project, contract value)
   *  is ERP-owned and stays manual until the D365 connector lands. */
  const onPickInvoice = async (f: File | null) => {
    setInvoiceFile(f)
    setAutofillNote("")
    setNotInvoice("")
    if (!f) return
    setExtracting(true)
    try {
      const read = await api.extractInvoice(f)
      if (read && !read.is_invoice) setNotInvoice(read.looks_like || "unknown")
      const doc = read?.invoice
      if (doc) {
        setFields((prev) => ({
          ...prev,
          vendor_name_en: doc.seller_name_ar || prev.vendor_name_en,
          invoice_no: doc.invoice_no || prev.invoice_no,
          claim_date: /^\d{4}-\d{2}-\d{2}/.test(doc.invoice_date)
            ? doc.invoice_date.slice(0, 10)
            : prev.claim_date,
          claim_amount_total: doc.total_with_vat ? doc.total_with_vat.toFixed(2) : prev.claim_amount_total,
          vat_amount: doc.vat_amount ? doc.vat_amount.toFixed(2) : prev.vat_amount,
          claim_amount_base: doc.total_with_vat
            ? (doc.total_with_vat - doc.vat_amount).toFixed(2)
            : prev.claim_amount_base,
        }))
        if (doc.seller_name_ar) setVendorNameAr(doc.seller_name_ar)
        setTotalTouched(true)
        setAutofillNote("filled")
      }
    } catch {
      setAutofillNote("manual")
    } finally {
      setExtracting(false)
    }
  }

  const headerFormData = (): FormData => {
    const fd = new FormData()
    for (const [k, v] of Object.entries(fields)) fd.append(k, v)
    fd.append("vendor_name_ar", vendorNameAr)
    return fd
  }

  /** Submit/update the claim with this step's inputs, then run its gates. */
  const analyze = async (stepNo: GateStepNo) => {
    setError("")
    setPhase("running")
    setPhaseIdx(0)
    const narration = RUN_PHASES[stepNo]
    const timer = window.setInterval(
      () => setPhaseIdx((i) => Math.min(i + 1, narration.length - 1)),
      1100
    )
    // Mock engines answer instantly — hold the loader long enough to narrate.
    const minWait = new Promise((r) => setTimeout(r, narration.length * 1100 + 300))
    try {
      let current = claim
      if (!current) {
        const fd = headerFormData()
        if (invoiceFile) fd.append("invoice", invoiceFile)
        current = await api.submit(fd)
      } else if (current.origin === "submitted") {
        const fd = stepNo === 1 ? headerFormData() : new FormData()
        if (stepNo === 1 && invoiceFile) fd.append("invoice", invoiceFile)
        if (stepNo === 2) {
          fd.append("cumulative_prior", boqFields.cumulative_prior)
          fd.append("prior_payment_count", boqFields.prior_payment_count)
          // The ceiling the gate checks against — editable on this step too
          // (BoQ-suggested or corrected), so it must travel with the run.
          fd.append("contract_value", fields.contract_value || "0")
          // Claim type is validated against the payment record at THIS gate,
          // so it stays editable here.
          fd.append("claim_type", fields.claim_type)
          // Contract kind decides the acceptance document step 3 asks for;
          // the end date feeds the step-4 delay inference.
          fd.append("contract_kind", fields.contract_kind)
          fd.append("contract_end_date", fields.contract_end_date)
          for (const f of boqFiles) fd.append("contract_boq", f)
        }
        if (stepNo === 3) {
          // One acceptance document per contract kind.
          if (fields.contract_kind === "goods") {
            if (deliveryFile) fd.append("delivery_note", deliveryFile)
          } else if (cocFile) {
            fd.append("coc", cocFile)
          }
        }
        if (stepNo === 4) {
          fd.append(
            "penalties",
            JSON.stringify(
              penalties
                .filter((p) => p.reason_ar || p.amount)
                .map((p) => ({ reason_ar: p.reason_ar, amount: parseFloat(p.amount) || 0, date: p.date }))
            )
          )
        }
        if (stepNo === 5) {
          // The attachment list is DERIVED from what the agent identified in
          // the uploads (plus the step-2 contract/BoQ) — not from checkboxes.
          if (attachFiles.length) {
            fd.append("detected_attachments", JSON.stringify(attachDetections))
            for (const f of attachFiles) fd.append("attachment_docs", f)
          }
          for (const f of otherFiles) fd.append("other", f)
        }
        current = await api.update(current.id, fd)
      }
      // ERP-sourced claims skip the update — their data is the ERP's.
      setClaim(current)
      const result = await api.run(current!.id, CUM_GATES[stepNo])
      await minWait
      setRun(result)
      setPhase("results")
      setMaxStep((m) => Math.max(m, stepNo) as StepNo)
      api.setProgress(current!.id, stepNo).catch(() => {})
    } catch (e) {
      setError(String(e))
      setPhase("form")
    } finally {
      window.clearInterval(timer)
    }
  }

  /** Show a step's cached results when its gate already ran; otherwise its form. */
  const phaseForStep = (s: StepNo): Phase =>
    s === 6 || gateFor(s as GateStepNo) ? "results" : "form"

  const continueToNext = () => {
    const next = Math.min(step + 1, 6) as StepNo
    setStep(next)
    setPhase(phaseForStep(next))
    setMaxStep((m) => Math.max(m, next) as StepNo)
    if (claim) api.setProgress(claim.id, next).catch(() => {})
  }

  const reset = () => window.location.assign("/submit")

  const gateFor = (stepNo: GateStepNo): GateRun | undefined =>
    run?.gates.find((g) => g.gate === GATE_BY_STEP[stepNo])
  const stageFor = (stepNo: GateStepNo): Stage | undefined =>
    stages.find((s) => s.id === GATE_BY_STEP[stepNo])

  /** One completed gate's result block: title + severity + findings. */
  const GateResults = ({ stepNo }: { stepNo: GateStepNo }) => {
    const gate = gateFor(stepNo)
    const stage = stageFor(stepNo)
    if (!gate || !stage) return null
    return (
      <section>
        <div className="flex items-center gap-3">
          <span className="bg-secondary text-secondary-foreground grid size-6 place-items-center rounded-full text-xs font-semibold">
            {stage.order}
          </span>
          <h2 className="font-medium">{pick(stage.title_en, stage.title_ar)}</h2>
          <StatusPill status={gate.severity} />
        </div>
        <p className="text-muted-foreground mt-1 text-sm">{pick(stage.desc_en, stage.desc_ar)}</p>
        <div className="mt-3 space-y-2">
          {gate.findings.map((f) => (
            <FindingCard key={f.rule_id} finding={f} claim={claim} extracted={run?.extracted} />
          ))}
        </div>
      </section>
    )
  }

  const ResultActions = ({ last }: { last?: boolean }) => (
    <div className="flex items-center justify-between">
      <Button variant="ghost" size="sm" onClick={() => setPhase("form")}>
        <Pencil />
        {t("Adjust & re-run", "تعديل وإعادة التحليل")}
      </Button>
      <Button onClick={continueToNext}>
        {last ? t("View recommendation", "عرض التوصية") : t("Continue", "متابعة")}
        <ArrowRight className="rtl:rotate-180" />
      </Button>
    </div>
  )

  const Runner = ({ stepNo }: { stepNo: GateStepNo }) => (
    <div className="mx-auto mt-8 max-w-md rounded-xl border border-border bg-card p-5">
      <h2 className="text-sm font-semibold">{claim?.id ?? t("Submitting claim…", "جارٍ إرسال المطالبة…")}</h2>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {t(
          "The model reads the pages, deterministic rules validate, the reviewer decides.",
          "القراءة بالتعرف الضوئي، والتنظيم بالنموذج، والتحقق بقواعد حتمية."
        )}
      </p>
      <ol className="mt-4 space-y-2.5">
        {RUN_PHASES[stepNo].map((p, i) => (
          <li key={i} className="flex items-center gap-2.5 text-sm">
            {i < phaseIdx ? (
              <Check className="text-ok size-4 shrink-0" />
            ) : i === phaseIdx ? (
              <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
            ) : (
              <CircleDashed className="text-muted-foreground/50 size-4 shrink-0" />
            )}
            <span className={cn(i > phaseIdx && "text-muted-foreground/60")}>{pick(p.en, p.ar)}</span>
          </li>
        ))}
      </ol>
    </div>
  )

  const Meta = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-0.5 truncate text-sm font-medium">{value}</div>
    </div>
  )

  // Pre-finance coverage: which required documents the agent has identified.
  // The step-2 contract/BoQ upload is one document covering two requirements.
  const hasContractBoq = !!(stagedName("contract_boq") || boqFiles.length)
  const hasExportDocs = !!claim?.source_files.some((f) =>
    ["invoice", "contract_boq", "coc", "delivery_note"].includes(f.doc_type)
  )
  const coveredKeys = new Set(attachDetections.filter((d) => d.doc_key !== "other").map((d) => d.doc_key))
  if (hasContractBoq) {
    coveredKeys.add("contract")
    coveredKeys.add("boq")
  }
  const missingDocs = REQUIRED_ATTACHMENTS.filter((a) => !coveredKeys.has(a.key))
  const unrecognized = attachDetections.filter((d) => d.doc_key === "other")
  const detectedForCards =
    (run?.extracted?.detected_attachments?.length
      ? run.extracted.detected_attachments
      : claim?.documents.detected_attachments) ?? []

  return (
    <div>
      <Link to="/" className="text-muted-foreground inline-flex items-center gap-1 text-sm hover:underline">
        <ArrowLeft className="size-4 rtl:rotate-180" />
        {t("All claims", "كل المطالبات")}
      </Link>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl">
            {existing ? existing.id : t("New claim review", "مراجعة مطالبة جديدة")}
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {existing
              ? pick(existing.project_name_en, existing.project_name_ar) ||
                t(
                  "Each step feeds one review gate of the procedure.",
                  "كل خطوة تقابل بوابة مراجعة في الإجراء."
                )
              : t(
                  "Each step feeds one review gate of the procedure — the agent checks as the file builds up.",
                  "كل خطوة تقابل بوابة مراجعة في الإجراء — ويدقق الوكيل المستندات أولاً بأول مع اكتمال الملف."
                )}
          </p>
        </div>
        {!existing && claim && <span className="text-muted-foreground text-sm font-medium">{claim.id}</span>}
      </div>

      {existing && (
        <div className="mt-4 grid grid-cols-2 gap-4 rounded-xl border border-border bg-card p-4 sm:grid-cols-3 lg:grid-cols-5">
          <Meta label={t("Vendor", "المورد")} value={pick(existing.vendor_name_en, existing.vendor_name_ar) || "—"} />
          <Meta label={t("Invoice no.", "رقم الفاتورة")} value={existing.invoice_no || "—"} />
          <Meta
            label={t("Claim type", "نوع المستخلص")}
            value={existing.claim_type === "final" ? t("Final", "نهائي") : existing.claim_type === "first" ? t("First payment", "دفعة أولى") : t("Periodic", "دوري")}
          />
          <Meta label={t("Contract value (base)", "قيمة العقد (قبل الضريبة)")} value={formatMoney(existing.contract_value)} />
          <Meta label={t("Claim (incl. VAT)", "المبلغ شامل الضريبة")} value={formatMoney(existing.claim_amount_total)} />
        </div>
      )}

      {/* Stepper — completed steps are clickable to revisit their findings.
          Mirrors the prequalification wizard's step banner (size-10 icon
          circles, mid-line connectors, labels beneath). */}
      <nav aria-label={t("Review steps", "خطوات المراجعة")} className="mt-6">
        <ol className="flex items-start overflow-x-auto pb-1">
          {STEP_LABELS.map((s, i) => {
            const isActive = step === s.no
            const done = maxStep > s.no || (s.no <= 5 && !!gateFor(s.no as GateStepNo))
            const Icon = s.icon
            const isLast = i === STEP_LABELS.length - 1
            const jump =
              done && !isActive && phase !== "running"
                ? () => {
                    setStep(s.no)
                    setPhase(phaseForStep(s.no))
                  }
                : undefined
            return (
              <li key={s.no} className="flex min-w-[96px] flex-1 flex-col items-center">
                <div className="flex w-full items-center">
                  <span
                    className={cn(
                      "h-px flex-1",
                      i === 0 ? "bg-transparent" : s.no <= step ? "bg-primary/50" : "bg-border"
                    )}
                  />
                  <button
                    type="button"
                    disabled={!jump && !isActive}
                    onClick={jump}
                    aria-current={isActive ? "step" : undefined}
                    className={cn(
                      "grid size-10 shrink-0 place-items-center rounded-full border-2 transition",
                      isActive && "border-primary bg-card text-primary ring-primary/20 ring-2",
                      !isActive && done && "border-primary bg-primary text-primary-foreground",
                      !isActive && !done && "border-border bg-card text-muted-foreground cursor-not-allowed",
                      jump && "hover:ring-primary/15 cursor-pointer hover:ring-2"
                    )}
                  >
                    {!isActive && done ? <Check className="size-4" /> : <Icon className="size-4" />}
                  </button>
                  <span
                    className={cn(
                      "h-px flex-1",
                      isLast ? "bg-transparent" : s.no < step ? "bg-primary/50" : "bg-border"
                    )}
                  />
                </div>
                <button
                  type="button"
                  disabled={!jump && !isActive}
                  onClick={jump}
                  className={cn(
                    "mt-2.5 px-1 text-center text-xs font-medium leading-tight transition",
                    isActive
                      ? "text-primary"
                      : jump
                        ? "text-foreground hover:text-primary cursor-pointer"
                        : "text-muted-foreground cursor-not-allowed"
                  )}
                >
                  <span className="text-muted-foreground block text-[10px]">
                    {t(`Step ${s.no}`, `الخطوة ${s.no}`)}
                  </span>
                  {pick(s.en, s.ar)}
                </button>
              </li>
            )
          })}
        </ol>
      </nav>

      {/* The invoice read's explicit verdict when the step-1 upload doesn't
          read as a tax invoice — pinned under the tracker so it can't be
          missed, and cleared the moment the file is replaced. */}
      {notInvoice && (
        <div
          role="alert"
          className="border-warn/40 bg-warn/10 mt-4 flex items-start gap-2.5 rounded-xl border p-3"
        >
          <TriangleAlert className="text-warn mt-0.5 size-4 shrink-0" />
          <div className="text-sm">
            <p className="font-medium">
              {t(
                "The uploaded document doesn't appear to be a tax invoice.",
                "المستند المرفوع لا يبدو فاتورة ضريبية."
              )}
            </p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {notInvoice === "contract"
                ? t("It reads like a contract / bill of quantities.", "يبدو أنه عقد / جدول كميات.")
                : notInvoice === "coc"
                  ? t("It reads like a certificate of completion.", "يبدو أنه محضر إنجاز.")
                  : notInvoice === "receipt"
                    ? t("It reads like a delivery note / receiving record.", "يبدو أنه إشعار تسليم / محضر استلام.")
                    : t(
                        "No invoice fields (number, totals, line items) were found in it.",
                        "لم يُعثر فيه على حقول الفاتورة (الرقم، المبالغ، البنود)."
                      )}{" "}
              {t(
                "Replace it with the vendor's tax invoice (فاتورة ضريبية) before analyzing.",
                "استبدله بالفاتورة الضريبية الصادرة من المورد قبل التحليل."
              )}
            </p>
          </div>
        </div>
      )}

      {error && <p className="text-destructive mt-4 text-sm">{error}</p>}

      {phase === "running" && step <= 5 && <Runner stepNo={step as GateStepNo} />}

      {/* ------------------------------------------ step 1: claim + invoice */}
      {step === 1 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <Section
            title={t("Tax invoice (فاتورة ضريبية)", "الفاتورة الضريبية")}
            desc={
              ro
                ? t("Staged from the ERP attachments.", "مرفقة من نظام تخطيط الموارد.")
                : t(
                    "Start here — the agent reads the invoice, prefills the claim fields below, and decodes its ZATCA QR to verify authenticity.",
                    "ابدأ من هنا — يقرأ الوكيل الفاتورة ويعبّئ حقول المطالبة أدناه، ويفك رمز الاستجابة السريعة (QR) للتحقق من صحتها."
                  )
            }
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <UploadSlot
                label={t("Upload the invoice PDF", "رفع ملف الفاتورة")}
                hint={
                  extracting
                    ? t("Reading the invoice…", "جارٍ قراءة الفاتورة…")
                    : stagedName("invoice") ?? t("PDF — click to choose", "PDF — انقر للاختيار")
                }
                file={extracting ? null : invoiceFile}
                onPick={onPickInvoice}
                busy={extracting}
                disabled={ro}
                required={!ro}
              />
              <button
                type="button"
                onClick={() => setD365Open(true)}
                className="hover:bg-muted/50 flex items-center gap-3 rounded-lg border border-dashed border-border p-3 text-start"
              >
                <Database className="text-muted-foreground size-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">
                    {t("Import from Microsoft Dynamics 365", "استيراد من Microsoft Dynamics 365")}
                  </span>
                  <span className="text-muted-foreground block text-xs">
                    {t("Pull the claim and its attachments from the ERP", "جلب المطالبة ومرفقاتها من النظام")}
                  </span>
                </span>
              </button>
            </div>
            {autofillNote === "filled" && (
              <p className="text-ok mt-2 flex items-center gap-1.5 text-xs">
                <Check className="size-3.5" />
                {t(
                  "Claim fields prefilled from the invoice — review them before analyzing.",
                  "تم تعبئة حقول المطالبة من الفاتورة — راجعها قبل التحليل."
                )}
              </p>
            )}
            {autofillNote === "manual" && (
              <p className="text-warn mt-2 text-xs">
                {t(
                  "Couldn't read the invoice automatically — fill the fields manually.",
                  "تعذرت قراءة الفاتورة تلقائياً — عبّئ الحقول يدوياً."
                )}
              </p>
            )}
          </Section>

          <Section
            title={t("Purchase order & project", "معلومات أمر الشراء والمشروع")}
            desc={t("As on the claim header in the ERP.", "كما في رأس المطالبة في نظام تخطيط الموارد.")}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label={t("Vendor name", "اسم المورد")}>
                <input className={inputCls} disabled={ro} value={fields.vendor_name_en} onChange={set("vendor_name_en")} />
              </Field>
              <Field label={t("Vendor account", "حساب المورد")}>
                <input className={inputCls} disabled={ro} value={fields.vendor_account} onChange={set("vendor_account")} placeholder="Vend00745" />
              </Field>
              <Field label={t("Purchase order", "أمر الشراء")}>
                <input className={inputCls} disabled={ro} value={fields.po_no} onChange={set("po_no")} placeholder="PO25-00078" />
              </Field>
              <Field label={t("Project / contract no.", "رقم المشروع / العقد")}>
                <input className={inputCls} disabled={ro} value={fields.project_no} onChange={set("project_no")} placeholder="PRJ0000641" />
              </Field>
              <Field label={t("Project name", "اسم المشروع")}>
                <input className={inputCls} disabled={ro} value={fields.project_name_en} onChange={set("project_name_en")} />
              </Field>
              <Field label={t("Contract value (base)", "قيمة العقد (قبل الضريبة)")}>
                <input className={inputCls} disabled={ro} type="number" step="any" min="0" value={fields.contract_value} onChange={set("contract_value")} />
              </Field>
            </div>
          </Section>

          <Section
            title={t("Claim financials", "معلومات المطالبة المالية")}
            desc={t(
              "The vendor-entered figures the intake gate validates.",
              "الأرقام المدخلة من المورد والتي تتحقق منها بوابة الاستلام."
            )}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Field label={t("Invoice no.", "رقم المطالبة المالية (الفاتورة)")}>
                <input className={inputCls} disabled={ro} value={fields.invoice_no} onChange={set("invoice_no")} placeholder="INV/2026/00070" />
              </Field>
              <Field label={t("Claim date", "تاريخ المطالبة المالية")}>
                <input className={inputCls} disabled={ro} type="date" value={fields.claim_date} onChange={set("claim_date")} />
              </Field>
              <Field label={t("Payment no.", "رقم الدفعة")}>
                <input className={inputCls} disabled={ro} type="number" min="1" value={fields.payment_no} onChange={set("payment_no")} />
              </Field>
              <Field label={t("Claim type", "نوع المستخلص")}>
                <select className={inputCls} disabled={ro} value={fields.claim_type} onChange={set("claim_type")}>
                  <option value="first">{t("First payment", "دفعة أولى")}</option>
                  <option value="periodic">{t("Periodic", "دوري")}</option>
                  <option value="final">{t("Final", "نهائي")}</option>
                </select>
              </Field>
              <Field label={t("Amount excl. VAT", "إجمالي المطالبة (قبل الضريبة)")}>
                <input className={inputCls} disabled={ro} type="number" step="any" min="0" value={fields.claim_amount_base} onChange={set("claim_amount_base")} />
              </Field>
              <Field label={t("VAT amount", "قيمة الضريبة")}>
                <input className={inputCls} disabled={ro} type="number" step="any" min="0" value={fields.vat_amount} onChange={set("vat_amount")} />
              </Field>
              <Field label={t("Total incl. VAT", "مبلغ المطالبة (شامل الضريبة)")}>
                <input
                  className={inputCls}
                  disabled={ro}
                  type="number"
                  step="any"
                  min="0"
                  value={fields.claim_amount_total}
                  onChange={(e) => {
                    setTotalTouched(true)
                    set("claim_amount_total")(e)
                  }}
                />
              </Field>
            </div>
          </Section>

          <div className="flex items-center justify-end gap-3">
            {!ro && !invoiceFile && !stagedName("invoice") && (
              <p className="text-muted-foreground text-xs">
                {t("Attach the tax invoice to start.", "أرفق الفاتورة الضريبية للبدء.")}
              </p>
            )}
            <Button
              size="lg"
              onClick={() => analyze(1)}
              disabled={extracting || (!invoiceFile && !stagedName("invoice"))}
            >
              <Sparkles />
              {t("Analyze invoice", "تحليل الفاتورة")}
            </Button>
          </div>
        </div>
      )}

      {step === 1 && phase === "results" && (
        <div className="mt-5 space-y-5">
          {run?.qr && <QrPanel qr={run.qr} claim={claim} />}
          <GateResults stepNo={1} />
          <ResultActions />
        </div>
      )}

      {/* -------------------------------------------- step 2: contract/BoQ */}
      {step === 2 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <Section
            title={t("Contract & Bill of Quantities", "العقد وجدول الكميات")}
            desc={t(
              "The bank's copy of the contract — the agent matches every invoice line against it and checks amounts against the contract value and payment schedule.",
              "نسخة البنك من العقد — يطابق الوكيل كل بند في الفاتورة معه ويتحقق من المبالغ مقابل قيمة العقد وجدول الدفعات."
            )}
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="sm:col-span-2 lg:col-span-3">
                <label
                  className={cn(
                    "flex items-center gap-3 rounded-lg border border-dashed border-border p-3",
                    ro || boqExtracting ? "opacity-70" : "hover:bg-muted/50 cursor-pointer"
                  )}
                >
                  {boqExtracting ? (
                    <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
                  ) : (
                    <Upload className="text-muted-foreground size-4 shrink-0" />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium">
                      {t("Contract and bill of quantities", "العقد وجدول الكميات")}
                    </span>
                    <span className="text-muted-foreground block text-xs">
                      {boqExtracting
                        ? t("Reading the documents…", "جارٍ قراءة المستندات…")
                        : t(
                            "One combined file, or the contract, the BoQ and appendices as separate files — the agent reads them together",
                            "ملف واحد مجمّع، أو العقد وجدول الكميات والملاحق كملفات منفصلة — يقرأها الوكيل معاً"
                          )}
                    </span>
                  </span>
                  {!ro && (
                    <input
                      type="file"
                      multiple
                      className="hidden"
                      accept=".pdf,.docx,.png,.jpg,.jpeg"
                      onChange={(e) => {
                        onPickBoq(e.target.files)
                        e.target.value = ""
                      }}
                    />
                  )}
                </label>
                {boqFiles.length > 0 ? (
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {boqFiles.map((f) => (
                      <li key={f.name} className="bg-muted/60 flex items-center gap-1.5 rounded-md px-2 py-1 text-xs">
                        <span className="max-w-[16rem] truncate">{f.name}</span>
                        {!ro && !boqExtracting && (
                          <button
                            type="button"
                            onClick={() => removeBoqFile(f.name)}
                            className="hover:text-destructive rounded"
                            aria-label={t("Remove", "إزالة")}
                          >
                            <X className="size-3" />
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  stagedNames("contract_boq").length > 0 && (
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {stagedNames("contract_boq").map((n) => (
                        <li key={n} className="bg-muted/60 rounded-md px-2 py-1 text-xs">
                          <span className="max-w-[16rem] truncate">{n}</span>
                        </li>
                      ))}
                      {!ro && (
                        <li className="text-muted-foreground self-center text-xs">
                          {t("on file — choosing new files replaces them", "مرفوعة — اختيار ملفات جديدة يستبدلها")}
                        </li>
                      )}
                    </ul>
                  )
                )}
              </div>
              <Field label={t("Contract value (base)", "قيمة العقد (قبل الضريبة)")}>
                <input
                  className={inputCls}
                  disabled={ro}
                  type="number"
                  step="any"
                  min="0"
                  value={fields.contract_value}
                  onChange={set("contract_value")}
                />
              </Field>
              <Field label={t("Disbursed before this claim (excl. VAT)", "سابق الصرف على العقد (بدون الضريبة)")}>
                <input
                  className={inputCls}
                  disabled={ro}
                  type="number"
                  step="any"
                  min="0"
                  value={boqFields.cumulative_prior}
                  onChange={(e) => setBoqFields((f) => ({ ...f, cumulative_prior: e.target.value }))}
                />
              </Field>
              <Field label={t("Prior payments count", "عدد الدفعات السابقة")}>
                <input
                  className={inputCls}
                  disabled={ro}
                  type="number"
                  min="0"
                  value={boqFields.prior_payment_count}
                  onChange={(e) => setBoqFields((f) => ({ ...f, prior_payment_count: e.target.value }))}
                />
              </Field>
              <Field label={t("Claim type", "نوع المستخلص")}>
                <select className={inputCls} disabled={ro} value={fields.claim_type} onChange={set("claim_type")}>
                  <option value="first">{t("First payment", "دفعة أولى")}</option>
                  <option value="periodic">{t("Periodic", "دوري")}</option>
                  <option value="final">{t("Final", "نهائي")}</option>
                </select>
              </Field>
              <Field label={t("Contract kind", "نوع العقد")}>
                <select className={inputCls} disabled={ro} value={fields.contract_kind} onChange={set("contract_kind")}>
                  <option value="works">{t("Works / project (COC)", "أعمال / مشروع (محضر إنجاز)")}</option>
                  <option value="goods">{t("Goods / supply (goods receipt)", "توريد (إيصال استلام)")}</option>
                </select>
              </Field>
              <Field label={t("Contract end date", "تاريخ نهاية العقد")}>
                <input
                  className={inputCls}
                  disabled={ro}
                  type="date"
                  value={fields.contract_end_date}
                  onChange={(e) => {
                    setEndDateNote(false)
                    set("contract_end_date")(e)
                  }}
                />
              </Field>
            </div>
            {boqNote === "suggested" && (
              <p className="text-ok mt-2 flex items-center gap-1.5 text-xs">
                <Check className="size-3.5" />
                {t(
                  "Contract value suggested from the contract / BoQ documents — confirm or correct it before matching.",
                  "قيمة العقد مقترحة من مستندات العقد وجدول الكميات — راجعها وأكدها قبل المطابقة."
                )}
              </p>
            )}
            {boqNote === "kept" && (
              <p className="text-muted-foreground mt-2 text-xs">
                {t(
                  "Kept the contract value you entered — the BoQ line totals were read but did not overwrite it.",
                  "تم الإبقاء على قيمة العقد المدخلة — قُرئ مجموع بنود الجدول دون استبدالها."
                )}
              </p>
            )}
            {endDateNote && fields.contract_end_date && (
              <p className="text-warn mt-2 flex items-center gap-1.5 text-xs">
                <CircleDashed className="size-3.5" />
                {t(
                  "Contract end date suggested from the contract document — it drives the final check's delay inference, so confirm it against the commencement minutes before matching.",
                  "تاريخ نهاية العقد مقترح من مستند العقد — وهو أساس استنتاج التأخير في الفحص النهائي، فراجعه مقابل محضر بدء المشروع قبل المطابقة."
                )}
              </p>
            )}
          </Section>
          <div className="flex justify-end">
            <Button size="lg" onClick={() => analyze(2)}>
              <Sparkles />
              {t("Match against contract", "المطابقة مع العقد")}
            </Button>
          </div>
        </div>
      )}

      {step === 2 && phase === "results" && (
        <div className="mt-5 space-y-5">
          <GateResults stepNo={2} />
          {run?.extracted && run.extracted.boq.length > 0 && (
            <LineItemsTable boq={run.extracted.boq} invoice={run.extracted.invoice} claim={claim} />
          )}
          <ResultActions />
        </div>
      )}

      {/* ------------------------------- step 3: acceptance & three-way match */}
      {step === 3 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <Section
            title={
              fields.contract_kind === "goods"
                ? t("Goods receipt (إيصال الاستلام)", "إيصال الاستلام")
                : t("Certificate of Completion (محضر الإنجاز)", "محضر الإنجاز")
            }
            desc={
              fields.contract_kind === "goods"
                ? t(
                    "Goods contract: the goods receipt / delivery note evidences acceptance. The agent matches contract/BoQ ↔ received quantities ↔ invoice — billed only what was received, within the contracted quantities.",
                    "عقد توريد: إيصال الاستلام / إشعار التسليم يُثبت الاستلام. يطابق الوكيل العقد وجدول الكميات مع الكميات المستلمة والفاتورة — لا فوترة إلا لما تم استلامه وضمن الكميات التعاقدية."
                  )
                : t(
                    "Works contract: the Certificate of Completion evidences acceptance — signed by the project manager and director. The agent matches contract/BoQ ↔ certified completion ↔ invoice.",
                    "عقد أعمال: محضر الإنجاز يُثبت الاستلام — موقّع من مدير المشروع ومدير الإدارة. يطابق الوكيل العقد وجدول الكميات مع الإنجاز المعتمد والفاتورة."
                  )
            }
          >
            {fields.contract_kind === "goods" ? (
              claim?.documents.receipt ? (
                <div>
                  <p className="text-sm font-medium">
                    {t("Goods receipt", "إيصال الاستلام")} {claim.documents.receipt.receipt_no}
                    <span className="text-muted-foreground font-normal">
                      {" — "}
                      {claim.documents.receipt.receipt_date}
                    </span>
                  </p>
                  <div className="border-border/70 mt-2 overflow-hidden rounded-md border">
                    {claim.documents.receipt.lines.map((l) => (
                      <div
                        key={l.item_code}
                        className="border-border/70 bg-muted/40 flex items-center gap-2 border-b px-2.5 py-1.5 text-xs last:border-0"
                      >
                        <span className="w-20 shrink-0 font-medium">{l.item_code}</span>
                        <span className="text-muted-foreground min-w-0 flex-1 truncate" dir="auto">
                          {l.description_ar}
                        </span>
                        <span className="tabular-nums">
                          {t("qty", "الكمية")} {l.quantity}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  <UploadSlot
                    label={t("Goods receipt / delivery note", "إيصال الاستلام / إشعار التسليم")}
                    hint={stagedName("delivery_note") ?? t("PDF — click to choose", "PDF — انقر للاختيار")}
                    file={deliveryFile}
                    onPick={setDeliveryFile}
                    disabled={ro}
                    required={!ro}
                  />
                  <p className="text-muted-foreground self-center text-xs leading-relaxed">
                    {t(
                      "In production the ERP receipt posting (procedure step 5) is pulled from D365 and cross-checked against this document. Without a receipt the agent flags that acceptance is not evidenced.",
                      "في بيئة الإنتاج يُجلب قيد الاستلام من داينمكس 365 (الخطوة ٥ من الإجراء) ويُطابق مع هذا المستند. وبدون إيصال يشير الوكيل إلى أن الاستلام غير مُثبت."
                    )}
                  </p>
                </div>
              )
            ) : (
              <UploadSlot
                label={t("Certificate of Completion", "محضر الإنجاز")}
                hint={stagedName("coc") ?? t("PDF — click to choose", "PDF — انقر للاختيار")}
                file={cocFile}
                onPick={setCocFile}
                disabled={ro}
                required={!ro}
              />
            )}
          </Section>

          <div className="flex justify-end">
            <Button size="lg" onClick={() => analyze(3)}>
              <Sparkles />
              {t("Run three-way match", "تشغيل المطابقة الثلاثية")}
            </Button>
          </div>
        </div>
      )}

      {step === 3 && phase === "results" && (
        <div className="mt-5 space-y-5">
          <GateResults stepNo={3} />
          <ResultActions />
        </div>
      )}

      {/* ------------------------------------ step 4: final check (penalties) */}
      {step === 4 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <PenaltyTermsCard claim={claim} extracted={run?.extracted} />
          <Section
            title={t("Penalties on record", "الغرامات المسجلة على المورد")}
            desc={t(
              "Project events the final check cross-checks against the acceptance document and the contract dates: declared delay vs. penalties, and delay inferred from the dates themselves.",
              "وقائع المشروع التي يطابقها الفحص النهائي مع مستند الاستلام وتواريخ العقد: التأخير المصرّح به مقابل الغرامات، والتأخير المستنتج من التواريخ نفسها."
            )}
          >
            <div className="space-y-2">
              {penalties.map((p, i) => (
                <div key={i} className="flex items-end gap-2">
                  <Field label={t("Reason", "السبب")} className="flex-1">
                    <input
                      className={inputCls}
                      disabled={ro}
                      value={p.reason_ar}
                      onChange={(e) =>
                        setPenalties((rows) => rows.map((r, j) => (j === i ? { ...r, reason_ar: e.target.value } : r)))
                      }
                    />
                  </Field>
                  <Field label={t("Amount", "المبلغ")} className="w-32">
                    <input
                      className={inputCls}
                      disabled={ro}
                      type="number"
                      step="any"
                      min="0"
                      value={p.amount}
                      onChange={(e) =>
                        setPenalties((rows) => rows.map((r, j) => (j === i ? { ...r, amount: e.target.value } : r)))
                      }
                    />
                  </Field>
                  <Field label={t("Date", "التاريخ")} className="w-40">
                    <input
                      className={inputCls}
                      disabled={ro}
                      type="date"
                      value={p.date}
                      onChange={(e) =>
                        setPenalties((rows) => rows.map((r, j) => (j === i ? { ...r, date: e.target.value } : r)))
                      }
                    />
                  </Field>
                  {!ro && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t("Remove penalty", "حذف الغرامة")}
                      onClick={() => setPenalties((rows) => rows.filter((_, j) => j !== i))}
                    >
                      <Trash2 />
                    </Button>
                  )}
                </div>
              ))}
              {penalties.length === 0 && (
                <p className="text-muted-foreground text-sm">
                  {ro
                    ? t("No penalties on record.", "لا توجد غرامات مسجلة.")
                    : t(
                        "No penalties entered — the agent still infers delay from the contract end date and the acceptance date.",
                        "لم تُدخل غرامات — يستنتج الوكيل التأخير من تاريخ نهاية العقد وتاريخ الاستلام على أي حال."
                      )}
                </p>
              )}
              {!ro && (
                <Button variant="outline" size="sm" onClick={() => setPenalties((rows) => [...rows, { reason_ar: "", amount: "", date: "" }])}>
                  <Plus />
                  {t("Add penalty", "إضافة غرامة")}
                </Button>
              )}
            </div>
          </Section>

          <div className="flex items-center justify-end gap-3">
            {!fields.contract_end_date && !ro && (
              <p className="text-muted-foreground text-xs">
                {t("No contract end date (step 2) — delay inference will be skipped.", "لا يوجد تاريخ نهاية للعقد (الخطوة ٢) — سيُتجاوز استنتاج التأخير.")}
              </p>
            )}
            <Button size="lg" onClick={() => analyze(4)}>
              <Sparkles />
              {t("Run final check", "تشغيل الفحص النهائي")}
            </Button>
          </div>
        </div>
      )}

      {step === 4 && phase === "results" && (
        <div className="mt-5 space-y-5">
          <GateResults stepNo={4} />
          <ResultActions />
        </div>
      )}

      {/* --------------------------------------- step 5: pre-finance package */}
      {step === 5 && phase === "form" && (
        <div className="mt-5 space-y-4">
          {ro ? (
            <Section
              title={t("Attachments filed in the ERP", "المرفقات المسجلة في النظام")}
              desc={t(
                "The pre-finance gate checks this list for completeness before referral to Finance (procedure step 6).",
                "تتحقق بوابة ما قبل المالية من اكتمال هذه القائمة قبل الإحالة للإدارة المالية (الخطوة ٦ من الإجراء)."
              )}
            >
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {REQUIRED_ATTACHMENTS.map((a) => {
                  const have = (existing?.documents.attachments ?? []).some(
                    (x) => x.trim().toLowerCase() === a.key
                  )
                  return (
                    <div key={a.key} className="flex items-center gap-2 text-sm">
                      {have ? (
                        <Check className="text-ok size-4 shrink-0" />
                      ) : (
                        <X className="text-destructive size-4 shrink-0" />
                      )}
                      {pick(a.en, a.ar)}
                    </div>
                  )
                })}
              </div>
            </Section>
          ) : (
            <Section
              title={t("Vendor file — required documents", "ملف المورد — المستندات المطلوبة")}
              desc={t(
                "Upload the vendor file; the agent identifies each document and reads its identity fields (CR number, VAT number, validity). The gate verifies what the agent actually saw — not a checkbox (procedure step 6).",
                "ارفع ملف المورد؛ يتعرف الوكيل على كل مستند ويقرأ حقول هويته (رقم السجل، الرقم الضريبي، الصلاحية). تتحقق البوابة مما رآه الوكيل فعلاً — لا من قائمة اختيارات (الخطوة ٦ من الإجراء)."
              )}
            >
              <label
                className={cn(
                  "flex items-center gap-3 rounded-lg border border-dashed border-border p-3",
                  attachExtracting ? "opacity-80" : "hover:bg-muted/50 cursor-pointer"
                )}
              >
                {attachExtracting ? (
                  <Loader2 className="text-primary size-4 shrink-0 animate-spin" />
                ) : (
                  <Upload className="text-muted-foreground size-4 shrink-0" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">
                    {t("Upload the vendor file documents", "رفع مستندات ملف المورد")}
                    <span className="text-destructive"> *</span>
                  </span>
                  <span className="text-muted-foreground block text-xs">
                    {attachExtracting
                      ? t("Identifying documents…", "جارٍ التعرف على المستندات…")
                      : t(
                          "Award letter, work commencement minutes, CR, zakat & GOSI certificates — multiple files",
                          "خطاب الترسية، محضر البدء، السجل التجاري، شهادتا الزكاة والتأمينات — عدة ملفات"
                        )}
                  </span>
                </span>
                <input
                  type="file"
                  multiple
                  className="hidden"
                  accept=".pdf,.docx,.png,.jpg,.jpeg"
                  onChange={(e) => {
                    onPickAttachments(e.target.files)
                    e.target.value = ""
                  }}
                />
              </label>

              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {REQUIRED_ATTACHMENTS.map((a) => {
                  const fromStep2 = (a.key === "contract" || a.key === "boq") && hasContractBoq
                  const det = attachDetections.find((d) => d.doc_key === a.key)
                  const covered = fromStep2 || !!det
                  const ident = det && (det.fields.reference_no || det.fields.cr_number || det.fields.vat_number)
                  return (
                    <div
                      key={a.key}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg border p-2.5 text-sm",
                        covered ? "border-ok/30 bg-ok/5" : "border-dashed border-border"
                      )}
                    >
                      {covered ? (
                        <Check className="text-ok size-4 shrink-0" />
                      ) : (
                        <CircleDashed className="text-muted-foreground/60 size-4 shrink-0" />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{pick(a.en, a.ar)}</span>
                        <span className="text-muted-foreground block truncate text-xs" dir="auto">
                          {fromStep2
                            ? t("Covered by the contract & BoQ document (step 2)", "مشمول بمستند العقد وجدول الكميات (الخطوة ٢)")
                            : det
                              ? det.file_name +
                                (ident ? ` — ${ident}` : "") +
                                (det.fields.expiry_date ? ` · ${t("valid until", "صالحة حتى")} ${det.fields.expiry_date}` : "")
                              : t("Awaiting upload", "بانتظار الرفع")}
                        </span>
                      </span>
                      {det && !fromStep2 && (
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t("Remove document", "إزالة المستند")}
                          onClick={() => removeAttachment(det.file_name)}
                        >
                          <Trash2 />
                        </Button>
                      )}
                    </div>
                  )
                })}
              </div>
              {unrecognized.length > 0 && (
                <p className="text-warn mt-2 text-xs">
                  {t(
                    `Couldn't identify: ${unrecognized.map((d) => d.file_name).join(", ")} — filed as "other".`,
                    `تعذر التعرف على: ${unrecognized.map((d) => d.file_name).join("، ")} — ستُسجل ضمن "أخرى".`
                  )}
                </p>
              )}
            </Section>
          )}

          <Section
            title={t("Remaining documents", "المستندات المتبقية")}
            desc={t("Optional supporting files for the record.", "ملفات داعمة اختيارية للسجل.")}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label
                className={cn(
                  "flex items-center gap-3 rounded-lg border border-dashed border-border p-3",
                  ro ? "opacity-70" : "hover:bg-muted/50 cursor-pointer"
                )}
              >
                <Upload className="text-muted-foreground size-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{t("Other attachments", "مرفقات أخرى")}</span>
                  <span className="text-muted-foreground block truncate text-xs">
                    {otherFiles.length
                      ? otherFiles.map((f) => f.name).join(", ")
                      : t("Optional, multiple files", "اختياري، عدة ملفات")}
                  </span>
                </span>
                {!ro && (
                  <input
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => setOtherFiles([...(e.target.files ?? [])])}
                  />
                )}
              </label>
            </div>
          </Section>

          <div className="flex items-center justify-end gap-3">
            {!ro && missingDocs.length > 0 && (
              <p className="text-muted-foreground text-xs">
                {t(
                  `${missingDocs.length} required document(s) still missing.`,
                  `${missingDocs.length} مستند/مستندات مطلوبة لم تُرفع بعد.`
                )}
              </p>
            )}
            <Button size="lg" onClick={() => analyze(5)} disabled={!ro && (attachExtracting || missingDocs.length > 0)}>
              <Sparkles />
              {t("Run final checks", "الفحوصات النهائية")}
            </Button>
          </div>
        </div>
      )}

      {step === 5 && phase === "results" && (
        <div className="mt-5 space-y-5">
          <GateResults stepNo={5} />
          {detectedForCards.length > 0 && <AttachmentCards detected={detectedForCards} claim={claim} />}
          <ResultActions last />
        </div>
      )}

      {/* -------------------------------------------- step 6: recommendation */}
      {step === 6 && run && claim && (
        <div className="mt-5 space-y-4">
          <div
            className={cn(
              "rounded-xl border p-4",
              run.verdict === "approve" && "border-ok/30 bg-ok/5",
              run.verdict === "needs_human" && "border-warn/40 bg-warn/5",
              run.verdict === "reject" && "border-destructive/30 bg-destructive/5"
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold">{t("Agent recommendation", "توصية الوكيل")}</h2>
              <StatusPill status={run.verdict} />
            </div>
            <p className="text-foreground/80 mt-2 text-sm leading-relaxed">
              {pick(run.rationale_en, run.rationale_ar)}
            </p>
          </div>

          <div className="overflow-hidden rounded-xl border border-border bg-card">
            {stages.map((stage) => {
              const gate = run.gates.find((g) => g.gate === stage.id)
              if (!gate) return null
              const issues = gate.findings.filter((f) => f.severity !== "ok").length
              return (
                <button
                  key={stage.id}
                  type="button"
                  onClick={() => {
                    setStep(stage.order as StepNo)
                    setPhase("results")
                  }}
                  className="border-border hover:bg-muted/40 flex w-full items-center gap-3 border-b px-4 py-2.5 text-start text-sm last:border-0"
                >
                  <span className="bg-secondary text-secondary-foreground grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold">
                    {stage.order}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {pick(stage.title_en, stage.title_ar)}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {issues
                      ? t(`${issues} finding${issues > 1 ? "s" : ""} to review`, `${issues} ملاحظة للمراجعة`)
                      : t("All checks passed", "اجتازت جميع الفحوصات")}
                  </span>
                  <StatusPill status={gate.severity} />
                </button>
              )
            })}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* Hand-off pack: the documents the gates matched (invoice, contract/
                BoQ, acceptance), named by claim id. Compliance attachments
                (CR, GOSI…) are deliberately left out. */}
            <Button variant="outline" asChild disabled={!hasExportDocs}>
              <a href={hasExportDocs ? exportUrl(claim.id) : undefined} download aria-disabled={!hasExportDocs}>
                <Download />
                {t("Export matching documents", "تصدير مستندات المطابقة")}
              </a>
            </Button>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" asChild>
                <Link to="/">{t("Back to claims", "العودة للمطالبات")}</Link>
              </Button>
              <Button variant="outline" onClick={reset}>
                {t("Review another claim", "مراجعة مطالبة أخرى")}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* --------------------------------------------- D365 integration stub */}
      {d365Open && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
          onClick={() => setD365Open(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                <Database className="text-muted-foreground size-4" />
                {t("Microsoft Dynamics 365 import", "الاستيراد من Microsoft Dynamics 365")}
              </h2>
              <button
                type="button"
                onClick={() => setD365Open(false)}
                className="text-muted-foreground hover:text-foreground rounded p-1"
                aria-label={t("Close", "إغلاق")}
              >
                <X className="size-4" />
              </button>
            </div>
            <p className="text-muted-foreground mt-3 text-sm leading-relaxed">
              {t(
                "In production, the connector pulls the claim header and its attachments directly from the استلام المطالبات form in D365 Finance & Operations — no re-entry. The integration is scoped for the pilot; for this demo, upload the documents directly.",
                "في بيئة الإنتاج، يجلب الموصّل بيانات المطالبة ومرفقاتها مباشرة من شاشة استلام المطالبات في داينمكس 365 دون إعادة إدخال. التكامل ضمن نطاق المرحلة التجريبية؛ ولهذا العرض، يتم رفع المستندات مباشرة."
              )}
            </p>
            <div className="mt-4 flex justify-end">
              <Button variant="outline" onClick={() => setD365Open(false)}>
                {t("Got it", "حسناً")}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** /submit — fresh guided intake. */
export function SubmitClaimPage() {
  return <ReviewWizard />
}

/** /claims/:id — the same wizard, resumed at the claim's persisted step. */
export function ClaimDetailPage() {
  const { id = "" } = useParams()
  const { t } = useLang()
  const [claim, setClaim] = React.useState<Claim | null>(null)
  const [run, setRun] = React.useState<RunResult | null | undefined>(undefined)
  const [error, setError] = React.useState("")

  React.useEffect(() => {
    setClaim(null)
    setRun(undefined)
    Promise.all([api.claim(id), api.latestRun(id)])
      .then(([c, r]) => {
        setClaim(c)
        setRun(r)
      })
      .catch((e) => setError(String(e)))
  }, [id])

  if (error) return <p className="text-destructive text-sm">{error}</p>
  if (!claim || run === undefined)
    return <p className="text-muted-foreground text-sm">{t("Loading…", "جارٍ التحميل…")}</p>
  return <ReviewWizard key={id} existing={claim} initialRun={run} />
}
