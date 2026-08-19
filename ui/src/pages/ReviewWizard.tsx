import * as React from "react"
import { Link, useParams } from "react-router-dom"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleDashed,
  Database,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react"

import { FindingCard } from "@/components/FindingCard"
import { QrPanel } from "@/components/QrPanel"
import { StatusPill } from "@/components/StatusPill"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useLang } from "@/lib/i18n"
import { cn, formatMoney } from "@/lib/utils"
import type { Claim, GateRun, RunResult, Stage } from "@/types/domain"

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

/** ERP attachment names the pre-finance gate requires (prefinance.yaml). */
const ERP_ATTACHMENTS: { key: string; en: string; ar: string }[] = [
  { key: "contract", en: "Contract", ar: "العقد" },
  { key: "boq", en: "Bill of Quantities", ar: "جدول الكميات" },
  { key: "award letter", en: "Award letter", ar: "خطاب الترسية" },
  { key: "work commencement", en: "Work commencement minutes", ar: "محضر البدء بالأعمال" },
  { key: "commercial registration", en: "Commercial registration", ar: "السجل التجاري" },
  { key: "zakat certificate", en: "Zakat certificate", ar: "شهادة الزكاة" },
  { key: "gosi certificate", en: "GOSI certificate", ar: "شهادة التأمينات الاجتماعية" },
]

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
  3: "coc_consistency",
  4: "three_way_match",
  5: "prefinance",
}
const CUM_GATES: Record<GateStepNo, string[]> = {
  1: ["intake"],
  2: ["intake", "boq_match"],
  3: ["intake", "boq_match", "coc_consistency"],
  4: ["intake", "boq_match", "coc_consistency", "three_way_match"],
  5: ["intake", "boq_match", "coc_consistency", "three_way_match", "prefinance"],
}

const STEP_LABELS: { no: StepNo; en: string; ar: string }[] = [
  { no: 1, en: "Tax invoice", ar: "الفاتورة الضريبية" },
  { no: 2, en: "Contract & BoQ", ar: "العقد وجدول الكميات" },
  { no: 3, en: "Completion certificate", ar: "محضر الإنجاز" },
  { no: 4, en: "Three-way match", ar: "المطابقة الثلاثية" },
  { no: 5, en: "Pre-finance package", ar: "الملف قبل المالية" },
  { no: 6, en: "Recommendation", ar: "التوصية" },
]

/** Loader narration per step — what the pipeline is actually doing. */
const RUN_PHASES: Record<GateStepNo, { en: string; ar: string }[]> = {
  1: [
    { en: "Reading the tax invoice (Layout OCR)", ar: "قراءة الفاتورة الضريبية (تعرف ضوئي)" },
    { en: "Structuring extracted fields", ar: "هيكلة الحقول المستخرجة" },
    { en: "Decoding & verifying the ZATCA QR", ar: "فك رمز الاستجابة والتحقق منه" },
    { en: "Intake & authenticity rules", ar: "قواعد الاستلام والتحقق من الصحة" },
  ],
  2: [
    { en: "Reading the contract / BoQ", ar: "قراءة العقد / جدول الكميات" },
    { en: "Structuring BoQ lines", ar: "هيكلة بنود جدول الكميات" },
    { en: "Matching invoice lines to the BoQ", ar: "مطابقة بنود الفاتورة مع الجدول" },
    { en: "Contract value & payment sequence rules", ar: "قواعد قيمة العقد وتسلسل الدفعات" },
  ],
  3: [
    { en: "Reading the Certificate of Completion", ar: "قراءة محضر الإنجاز" },
    { en: "Cross-checking penalties & project events", ar: "مطابقة الغرامات ووقائع المشروع" },
    { en: "COC consistency rules", ar: "قواعد اتساق محضر الإنجاز" },
  ],
  4: [
    { en: "Loading the ERP product receipt (procedure step 5)", ar: "تحميل إيصال استلام المنتجات من النظام (الخطوة ٥ من الإجراء)" },
    { en: "Reconciling contract ↔ receipt ↔ invoice quantities", ar: "مطابقة الكميات بين العقد وإيصال الاستلام والفاتورة" },
    { en: "Three-way matching rules", ar: "قواعد المطابقة الثلاثية" },
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

  // -- step 2: contract / BoQ + payment history ------------------------------
  const [boqFile, setBoqFile] = React.useState<File | null>(null)
  const [boqFields, setBoqFields] = React.useState(() => ({
    cumulative_prior: existing ? String(existing.cumulative_prior || 0) : "0",
    prior_payment_count: existing ? String(existing.prior_payment_count || 0) : "0",
  }))

  // -- step 3: COC + penalties on record -------------------------------------
  const [cocFile, setCocFile] = React.useState<File | null>(null)
  const [penalties, setPenalties] = React.useState<PenaltyRow[]>(() =>
    (existing?.documents.penalties ?? []).map((p) => ({
      reason_ar: p.reason_ar,
      amount: String(p.amount),
      date: p.date,
    }))
  )

  // -- step 4: ERP attachments + remaining docs ------------------------------
  const [attachments, setAttachments] = React.useState<Set<string>>(() =>
    existing?.documents.attachments.length
      ? new Set(existing.documents.attachments)
      : new Set(ERP_ATTACHMENTS.map((a) => a.key))
  )
  const [deliveryFile, setDeliveryFile] = React.useState<File | null>(null)
  const [otherFiles, setOtherFiles] = React.useState<File[]>([])

  // -- pipeline state --------------------------------------------------------
  const [claim, setClaim] = React.useState<Claim | null>(existing ?? null)
  const [run, setRun] = React.useState<RunResult | null>(initialRun ?? null)

  React.useEffect(() => {
    api.stages().then(setStages).catch((e) => setError(String(e)))
  }, [])

  const stagedName = (docType: string): string | undefined =>
    claim?.source_files.find((f) => f.doc_type === docType)?.path.split("/").pop()

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
    if (!f) return
    setExtracting(true)
    try {
      const doc = await api.extractInvoice(f)
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
          if (boqFile) fd.append("contract_boq", boqFile)
        }
        if (stepNo === 3) {
          fd.append(
            "penalties",
            JSON.stringify(
              penalties
                .filter((p) => p.reason_ar || p.amount)
                .map((p) => ({ reason_ar: p.reason_ar, amount: parseFloat(p.amount) || 0, date: p.date }))
            )
          )
          if (cocFile) fd.append("coc", cocFile)
        }
        if (stepNo === 4 && deliveryFile) {
          fd.append("delivery_note", deliveryFile)
        }
        if (stepNo === 5) {
          fd.append("attachments", JSON.stringify([...attachments]))
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
      api.setProgress(current!.id, stepNo).catch(() => {})
    } catch (e) {
      setError(String(e))
      setPhase("form")
    } finally {
      window.clearInterval(timer)
    }
  }

  const continueToNext = () => {
    const next = Math.min(step + 1, 6) as StepNo
    setStep(next)
    setPhase(next === 6 ? "results" : "form")
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
            <FindingCard key={f.rule_id} finding={f} claim={claim} />
          ))}
        </div>
      </section>
    )
  }

  const ResultActions = ({ last }: { last?: boolean }) => (
    <div className="flex items-center justify-between">
      <Button variant="ghost" size="sm" onClick={() => setPhase("form")}>
        <Pencil />
        {t("Adjust & re-run", "تعديل وإعادة الفحص")}
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
          "Specialist OCR reads, the model organizes, deterministic rules validate.",
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

  return (
    <div>
      <Link to="/" className="text-muted-foreground inline-flex items-center gap-1 text-sm hover:underline">
        <ArrowLeft className="size-4 rtl:rotate-180" />
        {t("All claims", "كل المطالبات")}
      </Link>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl">
            {existing ? existing.id : t("New claim review", "فحص مطالبة جديدة")}
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {existing
              ? pick(existing.project_name_en, existing.project_name_ar) ||
                t(
                  "Each step feeds one review gate of the procedure.",
                  "كل خطوة تغذي بوابة فحص من الإجراء."
                )
              : t(
                  "Each step feeds one review gate of the procedure — the agent checks as the file builds up.",
                  "كل خطوة تغذي بوابة فحص من الإجراء — يدقق الوكيل مع اكتمال الملف تدريجياً."
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
            value={existing.claim_type === "final" ? t("Final", "نهائي") : t("Periodic", "دوري")}
          />
          <Meta label={t("Contract value", "قيمة العقد")} value={formatMoney(existing.contract_value)} />
          <Meta label={t("Claim (incl. VAT)", "المبلغ شامل الضريبة")} value={formatMoney(existing.claim_amount_total)} />
        </div>
      )}

      {/* Stepper — completed steps are clickable to revisit their findings. */}
      <ol className="mt-5 flex items-center gap-2">
        {STEP_LABELS.map((s, i) => {
          const state = step === s.no ? "active" : step > s.no ? "done" : "todo"
          const jump =
            state === "done" && phase !== "running"
              ? () => {
                  setStep(s.no)
                  setPhase(
                    s.no === 6 || (s.no <= 5 && gateFor(s.no as GateStepNo)) ? "results" : "form"
                  )
                }
              : undefined
          return (
            <React.Fragment key={s.no}>
              {i > 0 && <span className="bg-border h-px flex-1" />}
              <li className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={jump}
                  disabled={!jump}
                  className={cn("flex items-center gap-2", jump && "cursor-pointer")}
                >
                  <span
                    className={cn(
                      "grid size-6 place-items-center rounded-full text-xs font-semibold",
                      state === "active" && "bg-primary text-primary-foreground",
                      state === "done" && "bg-ok/15 text-ok",
                      state === "todo" && "bg-secondary text-secondary-foreground"
                    )}
                  >
                    {state === "done" ? <Check className="size-3.5" /> : s.no}
                  </span>
                  <span
                    className={cn(
                      "text-sm max-lg:hidden",
                      state === "active" ? "font-medium" : "text-muted-foreground",
                      jump && "hover:text-foreground hover:underline"
                    )}
                  >
                    {pick(s.en, s.ar)}
                  </span>
                </button>
              </li>
            </React.Fragment>
          )
        })}
      </ol>

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
                    "Start here — the agent OCR-reads the invoice, prefills the claim fields below, and decodes its ZATCA QR to verify authenticity.",
                    "ابدأ من هنا — يقرأ الوكيل الفاتورة ويعبّئ حقول المطالبة أدناه ويفك رمز الاستجابة للتحقق من صحتها."
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
                    {t("Import from Microsoft Dynamics 365", "استيراد من مايكروسوفت داينمكس 365")}
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
              <Field label={t("Contract value (base)", "قيمة العقد الأساسية")}>
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
                  <option value="periodic">{t("Periodic", "دوري")}</option>
                  <option value="final">{t("Final", "نهائي")}</option>
                </select>
              </Field>
              <Field label={t("Amount excl. VAT", "اجمالي المطالبة بدون الضريبة")}>
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
            <div className="grid gap-3 sm:grid-cols-2">
              <UploadSlot
                label={t("Contract / BoQ", "العقد / جدول الكميات")}
                hint={stagedName("contract_boq") ?? t("PDF — click to choose", "PDF — انقر للاختيار")}
                file={boqFile}
                onPick={setBoqFile}
                disabled={ro}
                className="sm:col-span-2"
              />
              <Field label={t("Disbursed before this claim", "سابق الصرف على العقد")}>
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
            </div>
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
          <ResultActions />
        </div>
      )}

      {/* --------------------------------------------------- step 3: COC */}
      {step === 3 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <Section
            title={t("Certificate of Completion (محضر الإنجاز)", "محضر الإنجاز")}
            desc={t(
              "Signed by the project manager and director. The agent cross-checks its answers against the claim and the penalty record.",
              "الموقّع من مدير المشروع ومدير الإدارة. يطابق الوكيل إجاباته مع المطالبة وسجل الغرامات."
            )}
          >
            <UploadSlot
              label={t("Certificate of Completion", "محضر الإنجاز")}
              hint={stagedName("coc") ?? t("PDF — click to choose", "PDF — انقر للاختيار")}
              file={cocFile}
              onPick={setCocFile}
              disabled={ro}
            />
          </Section>

          <Section
            title={t("Penalties on record", "الغرامات المسجلة على المورد")}
            desc={t(
              "Project events the COC-consistency gate cross-checks against the Certificate of Completion.",
              "وقائع المشروع التي تطابقها بوابة اتساق محضر الإنجاز مع المحضر."
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
              {penalties.length === 0 && ro && (
                <p className="text-muted-foreground text-sm">{t("No penalties on record.", "لا توجد غرامات مسجلة.")}</p>
              )}
              {!ro && (
                <Button variant="outline" size="sm" onClick={() => setPenalties((rows) => [...rows, { reason_ar: "", amount: "", date: "" }])}>
                  <Plus />
                  {t("Add penalty", "إضافة غرامة")}
                </Button>
              )}
            </div>
          </Section>

          <div className="flex justify-end">
            <Button size="lg" onClick={() => analyze(3)}>
              <Sparkles />
              {t("Check consistency", "فحص الاتساق")}
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

      {/* ------------------------------------------ step 4: three-way match */}
      {step === 4 && phase === "form" && (
        <div className="mt-5 space-y-4">
          <Section
            title={t("Three-way match (المطابقة الثلاثية)", "المطابقة الثلاثية")}
            desc={t(
              "Contract/BoQ ↔ ERP product receipt ↔ invoice: the agent verifies quantities are billed only for work actually received, within the contracted quantities — the receipt the procedure posts at step 5.",
              "مطابقة ثلاثية بين العقد وإيصال استلام المنتجات والفاتورة: يتحقق الوكيل من أن الفوترة لما تم استلامه فعلاً وضمن الكميات التعاقدية — الإيصال الذي يسجله الإجراء في الخطوة ٥."
            )}
          >
            {claim?.documents.receipt ? (
              <div>
                <p className="text-sm font-medium">
                  {t("Product receipt", "إيصال الاستلام")} {claim.documents.receipt.receipt_no}
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
                  label={t("Delivery note / receipt evidence", "إشعار التسليم / مستند الاستلام")}
                  hint={stagedName("delivery_note") ?? t("PDF — click to choose", "PDF — انقر للاختيار")}
                  file={deliveryFile}
                  onPick={setDeliveryFile}
                  disabled={ro}
                />
                <p className="text-muted-foreground self-center text-xs leading-relaxed">
                  {t(
                    "In production the product receipt is pulled from D365 (procedure step 5). No receipt on this claim — the agent will flag that the three-way match cannot be completed.",
                    "في بيئة الإنتاج يُجلب إيصال الاستلام من داينمكس 365 (الخطوة ٥ من الإجراء). لا يوجد إيصال لهذه المطالبة — سيشير الوكيل إلى تعذر إتمام المطابقة الثلاثية."
                  )}
                </p>
              </div>
            )}
          </Section>
          <div className="flex justify-end">
            <Button size="lg" onClick={() => analyze(4)}>
              <Sparkles />
              {t("Run three-way match", "تشغيل المطابقة الثلاثية")}
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
          <Section
            title={t("Attachments filed in the ERP", "المرفقات المسجلة في النظام")}
            desc={t(
              "The pre-finance gate checks this list for completeness before referral to Finance (procedure step 6).",
              "تتحقق بوابة ما قبل المالية من اكتمال هذه القائمة قبل الإحالة للإدارة المالية (الخطوة ٦ من الإجراء)."
            )}
          >
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {ERP_ATTACHMENTS.map((a) => (
                <label key={a.key} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="accent-primary size-4"
                    disabled={ro}
                    checked={attachments.has(a.key)}
                    onChange={(e) =>
                      setAttachments((prev) => {
                        const next = new Set(prev)
                        if (e.target.checked) next.add(a.key)
                        else next.delete(a.key)
                        return next
                      })
                    }
                  />
                  {pick(a.en, a.ar)}
                </label>
              ))}
            </div>
          </Section>

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

          <div className="flex justify-end">
            <Button size="lg" onClick={() => analyze(5)}>
              <Sparkles />
              {t("Run final checks", "الفحوصات النهائية")}
            </Button>
          </div>
        </div>
      )}

      {step === 5 && phase === "results" && (
        <div className="mt-5 space-y-5">
          <GateResults stepNo={5} />
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

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="outline" asChild>
              <Link to="/">{t("Back to claims", "العودة للمطالبات")}</Link>
            </Button>
            <Button variant="outline" onClick={reset}>
              {t("Review another claim", "فحص مطالبة أخرى")}
            </Button>
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
                {t("Microsoft Dynamics 365 import", "الاستيراد من مايكروسوفت داينمكس 365")}
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
