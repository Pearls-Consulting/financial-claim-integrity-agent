import { FileSearch, ScrollText } from "lucide-react"

import { usePdfViewer } from "@/components/PdfViewerContext"
import { useLang } from "@/lib/i18n"
import type { Claim, ClaimDocuments, PenaltyTerm } from "@/types/domain"

/**
 * The penalty clauses the agent READ from the contract document, shown at the
 * final-check step above the manual penalty entry — each with a locate button
 * that opens the embedded reader on the clause's page and highlights it
 * (text layer for digital contracts, OCR polygons for scanned ones).
 */
export function PenaltyTermsCard({
  claim,
  extracted,
}: {
  claim?: Claim | null
  extracted?: ClaimDocuments | null
}) {
  const { t, pick } = useLang()
  const { openDocument } = usePdfViewer()

  const terms = extracted?.contract?.penalty_terms ?? []
  if (!terms.length) return null
  const index = claim?.source_files.findIndex((f) => f.doc_type === "contract_boq") ?? -1

  const perLabel = (per: string) =>
    per === "day" ? t(" per day of delay", " عن كل يوم تأخير") : per === "week" ? t(" per week of delay", " عن كل أسبوع تأخير") : ""

  const describe = (term: PenaltyTerm): string => {
    const parts: string[] = []
    if (term.rate_percent) parts.push(`${term.rate_percent}%${perLabel(term.per)}`)
    if (term.basis) parts.push(pick(`of ${term.basis}`, `من ${term.basis}`))
    if (term.cap_percent) parts.push(t(`capped at ${term.cap_percent}%`, `بحد أقصى ${term.cap_percent}٪`))
    return parts.join(t(", ", "، "))
  }

  const show = (term: PenaltyTerm) => {
    if (!claim || index === -1) return
    const file = claim.source_files[index]
    openDocument({
      claimId: claim.id,
      index,
      fileName: file.path.split("/").pop(),
      page: term.page || undefined,
      highlight: term.rate_percent ? `${term.rate_percent}%` : `${term.cap_percent}%`,
      highlightExtra: term.text_ar || undefined,
      fieldName:
        t("Penalty clause read from the contract", "بند الغرامات كما قُرئ من العقد") +
        (term.ref ? ` — ${term.ref}` : ""),
    })
  }

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <ScrollText className="text-primary size-4" />
        <h2 className="text-sm font-semibold">
          {t("Penalty terms read from the contract", "بنود الغرامات كما قُرئت من العقد")}
        </h2>
      </div>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {t(
          "Extracted from the contract document at step 2 — the final check measures the penalty record against these clauses. Click a clause to see it in the contract.",
          "استُخرجت من مستند العقد في الخطوة ٢ — يطابق الفحص النهائي سجل الغرامات مع هذه البنود. انقر على البند لعرضه في العقد."
        )}
      </p>
      <div className="border-border/70 mt-3 overflow-hidden rounded-md border">
        {terms.map((term, i) => (
          <div
            key={i}
            className="border-border/70 bg-muted/40 flex items-start gap-2 border-b px-2.5 py-2 text-sm last:border-0"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">
                  {term.kind === "delay" ? t("Delay penalty", "غرامة تأخير") : t("Penalty", "غرامة")}
                  {term.ref ? ` — ${term.ref}` : ""}
                </span>
                <span className="text-muted-foreground text-xs">{describe(term)}</span>
              </div>
              {term.text_ar && (
                <p dir="rtl" className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                  {term.text_ar}
                </p>
              )}
            </div>
            {claim && index !== -1 && (
              <button
                type="button"
                onClick={() => show(term)}
                className="text-primary hover:bg-secondary shrink-0 rounded p-1 transition"
                aria-label={t("Show in contract", "عرض في العقد")}
                title={t("Show in contract", "عرض في العقد")}
              >
                <FileSearch className="size-4" />
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
