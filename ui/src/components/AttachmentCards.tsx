import { FileCheck2, FileQuestion, FileSearch } from "lucide-react"

import { usePdfViewer } from "@/components/PdfViewerContext"
import { useLang } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import type { Claim, DetectedAttachment } from "@/types/domain"

/**
 * "Documents reviewed" panel for the pre-finance gate: one card per uploaded
 * vendor-file document, showing what the agent identified it as and the
 * identity fields it read (CR number, VAT number, reference no., validity).
 * The view button opens the embedded PDF reader on that document with its
 * key identifier highlighted — the "the agent actually looked at this" moment.
 */

/** The pre-finance gate's required attachments (mirrors prefinance.yaml). */
export const REQUIRED_ATTACHMENTS: { key: string; en: string; ar: string }[] = [
  { key: "contract", en: "Contract", ar: "العقد" },
  { key: "boq", en: "Bill of Quantities", ar: "جدول الكميات" },
  { key: "award letter", en: "Award letter", ar: "خطاب الترسية" },
  { key: "work commencement", en: "Work commencement minutes", ar: "محضر البدء بالأعمال" },
  { key: "commercial registration", en: "Commercial registration", ar: "السجل التجاري" },
  { key: "zakat certificate", en: "Zakat certificate", ar: "شهادة الزكاة" },
  { key: "gosi certificate", en: "GOSI certificate", ar: "شهادة التأمينات الاجتماعية" },
]

export function attachmentLabel(key: string): { en: string; ar: string } {
  return (
    REQUIRED_ATTACHMENTS.find((a) => a.key === key) ?? {
      key,
      en: "Unidentified document",
      ar: "مستند غير محدد",
    }
  )
}

const FIELD_LABELS: Record<string, { en: string; ar: string }> = {
  vendor_name_ar: { en: "Vendor name", ar: "اسم المنشأة" },
  cr_number: { en: "CR number", ar: "رقم السجل التجاري" },
  vat_number: { en: "VAT number", ar: "الرقم الضريبي" },
  reference_no: { en: "Document no.", ar: "رقم المستند" },
  issue_date: { en: "Issued", ar: "تاريخ الإصدار" },
  expiry_date: { en: "Valid until", ar: "صالحة حتى" },
}

export function AttachmentCards({ detected, claim }: { detected: DetectedAttachment[]; claim: Claim | null }) {
  const { t, pick } = useLang()
  const { openDocument } = usePdfViewer()
  if (detected.length === 0) return null

  const fileIndex = (d: DetectedAttachment): number => {
    if (!claim) return -1
    const byName = claim.source_files.findIndex((f) => f.path.split("/").pop() === d.file_name)
    if (byName !== -1) return byName
    return claim.source_files.findIndex((f) => f.doc_type === `attachment:${d.doc_key}`)
  }

  const view = (d: DetectedAttachment) => {
    if (!claim) return
    const index = fileIndex(d)
    if (index === -1) return
    const label = attachmentLabel(d.doc_key)
    const key = d.fields.reference_no || d.fields.cr_number || d.fields.vat_number
    openDocument({
      claimId: claim.id,
      index,
      fileName: d.file_name,
      page: d.page || undefined,
      highlight: key || undefined,
      highlightAlso: [d.fields.cr_number, d.fields.vat_number, d.fields.expiry_date].filter(
        (v): v is string => !!v && v !== key
      ),
      fieldName: pick(label.en, label.ar),
    })
  }

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">{t("Documents reviewed", "المستندات المفحوصة")}</h2>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {t(
          "What the agent identified in the vendor file, with the identity fields it read from each document.",
          "ما تعرّف عليه الوكيل في ملف المورد، مع حقول الهوية التي قرأها من كل مستند."
        )}
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {detected.map((d) => {
          const known = d.doc_key !== "other"
          const label = attachmentLabel(d.doc_key)
          const rows = Object.entries(FIELD_LABELS)
            .filter(([k]) => d.fields[k])
            .map(([k, l]) => ({ label: pick(l.en, l.ar), value: d.fields[k] }))
          return (
            <div key={d.file_name} className="rounded-lg border border-border p-3">
              <div className="flex items-start gap-2">
                {known ? (
                  <FileCheck2 className="text-ok mt-0.5 size-4 shrink-0" />
                ) : (
                  <FileQuestion className="text-warn mt-0.5 size-4 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <div className={cn("text-sm font-medium", !known && "text-warn")}>{pick(label.en, label.ar)}</div>
                  <div className="text-muted-foreground truncate text-xs" dir="ltr">
                    {d.file_name}
                  </div>
                </div>
                {claim && fileIndex(d) !== -1 && (
                  <button
                    type="button"
                    onClick={() => view(d)}
                    className="text-primary hover:bg-secondary shrink-0 rounded p-1 transition"
                    aria-label={t("View document", "عرض المستند")}
                    title={t("View document", "عرض المستند")}
                  >
                    <FileSearch className="size-3.5" />
                  </button>
                )}
              </div>
              {rows.length > 0 && (
                <div className="border-border/70 mt-2 overflow-hidden rounded-md border">
                  {rows.map((r) => (
                    <div
                      key={r.label}
                      className="border-border/70 bg-muted/40 flex items-center gap-2 border-b px-2 py-1 text-xs last:border-0"
                    >
                      <span className="text-muted-foreground min-w-0 flex-1 truncate">{r.label}</span>
                      <span className="max-w-[55%] truncate font-medium tabular-nums" dir="auto">
                        {r.value}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
