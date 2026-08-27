import { FileSearch } from "lucide-react"

import { usePdfViewer } from "@/components/PdfViewerContext"
import { docIndexFor } from "@/lib/evidence"
import { useLang } from "@/lib/i18n"
import { cn, formatMoney } from "@/lib/utils"
import type { BoqLine, Claim, InvoiceDoc, InvoiceLine } from "@/types/domain"

/**
 * Comparative line-item view: contracted (BoQ) vs billed (this invoice).
 *
 * Periodic claims legitimately bill a SUBSET of the BoQ — unbilled lines are
 * shown muted as "not billed this period", never as a defect (only a final
 * claim leaving lines unbilled deserves a second look; the rules gate that).
 * Each side carries a locate button that opens the source PDF and highlights
 * the line's quantity next to its item code — same affordance as the
 * findings' evidence rows.
 */

interface Row {
  item_code: string
  description: string
  unit: string
  boq: BoqLine | null
  inv: InvoiceLine | null
}

export function LineItemsTable({
  boq,
  invoice,
  claim,
}: {
  boq: BoqLine[]
  invoice: InvoiceDoc | null
  claim: Claim | null
}) {
  const { t, pick } = useLang()
  const { openDocument } = usePdfViewer()

  const invLines = invoice?.lines ?? []
  const invByCode = new Map(invLines.map((l) => [l.item_code, l]))
  const boqCodes = new Set(boq.map((l) => l.item_code))
  const rows: Row[] = [
    // BoQ order first, then any invoice lines the contract doesn't know.
    ...boq.map((b) => ({
      item_code: b.item_code,
      description: pick(b.description_en || b.description_ar, b.description_ar),
      unit: b.unit,
      boq: b,
      inv: invByCode.get(b.item_code) ?? null,
    })),
    ...invLines
      .filter((l) => !boqCodes.has(l.item_code))
      .map((l) => ({ item_code: l.item_code, description: l.description_ar, unit: "", boq: null, inv: l })),
  ]
  if (rows.length === 0) return null

  const billed = rows.filter((r) => r.inv).length
  const unbilled = rows.filter((r) => r.boq && !r.inv).length

  const docIndex = (docType: "contract_boq" | "invoice", sourceFile?: string): number =>
    docIndexFor(claim, sourceFile, [docType])

  const locate = (side: "boq" | "inv", r: Row) => {
    if (!claim) return
    const docType = side === "boq" ? "contract_boq" : "invoice"
    // Several contract/BoQ files may be staged — open the one this row was read from.
    const index = docIndex(docType, side === "boq" ? r.boq!.source_file : r.inv!.source_file)
    if (index === -1) return
    const qty = side === "boq" ? r.boq!.quantity : r.inv!.quantity
    const price = side === "boq" ? r.boq!.unit_price : r.inv!.unit_price
    // The reader cites the page each row was read from — the viewer lands
    // there directly, so the OCR fallback never has to search the document.
    const page = side === "boq" ? r.boq!.page : r.inv!.page
    openDocument({
      claimId: claim.id,
      index,
      fileName: claim.source_files[index].path.split("/").pop(),
      page: page || undefined,
      highlight: String(qty),
      highlightAlso: [r.item_code, String(price)],
      fieldName:
        side === "boq"
          ? `${r.item_code} — ${t("contracted qty in the BoQ", "الكمية التعاقدية في جدول الكميات")}`
          : `${r.item_code} — ${t("billed qty on the invoice", "الكمية المفوترة في الفاتورة")}`,
    })
  }

  const canLocate = (docType: "contract_boq" | "invoice"): boolean => docIndex(docType) !== -1

  const LocateBtn = ({ side, row }: { side: "boq" | "inv"; row: Row }) => (
    <button
      type="button"
      onClick={() => locate(side, row)}
      className="text-primary hover:bg-secondary shrink-0 rounded p-0.5 align-middle transition"
      aria-label={t("Locate in document", "تحديد الموضع في المستند")}
      title={t("Locate in document", "تحديد الموضع في المستند")}
    >
      <FileSearch className="size-3.5" />
    </button>
  )

  const status = (r: Row): { cls: string; label: string } => {
    if (r.boq && r.inv)
      return Math.abs(r.boq.unit_price - r.inv.unit_price) <= 0.01
        ? { cls: "text-ok", label: t("Price matches BoQ", "السعر مطابق للجدول") }
        : { cls: "text-destructive", label: t("Unit price ≠ BoQ", "سعر الوحدة مخالف للجدول") }
    if (r.inv) return { cls: "text-destructive", label: t("Not in BoQ", "غير موجود في الجدول") }
    return { cls: "text-muted-foreground", label: t("Not billed this period", "لم يُفوتر في هذه الدفعة") }
  }

  const num = "px-2.5 py-1.5 text-end tabular-nums"

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold">
        {t("Line items — contract/BoQ vs. this invoice", "البنود — جدول الكميات مقابل هذه الفاتورة")}
      </h2>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {t(
          `${billed} of ${rows.length} contracted line(s) billed in this claim.`,
          `${billed} من ${rows.length} بنداً تعاقدياً مفوتر في هذه المطالبة.`
        )}
      </p>
      <div className="border-border/70 mt-3 overflow-x-auto rounded-md border">
        <table className="w-full min-w-[640px] border-collapse text-xs">
          <thead>
            <tr className="bg-muted/60 text-muted-foreground">
              <th rowSpan={2} className="px-2.5 py-1.5 text-start font-medium">{t("Item", "البند")}</th>
              <th rowSpan={2} className="px-2.5 py-1.5 text-start font-medium">{t("Description", "الوصف")}</th>
              <th rowSpan={2} className="px-2.5 py-1.5 text-start font-medium">{t("Unit", "الوحدة")}</th>
              <th colSpan={2} className="border-border/70 border-b border-s px-2.5 py-1 text-center font-medium">
                {t("Contract / BoQ", "العقد / جدول الكميات")}
              </th>
              <th colSpan={3} className="border-border/70 border-b border-s px-2.5 py-1 text-center font-medium">
                {t("This invoice", "هذه الفاتورة")}
              </th>
              <th rowSpan={2} className="border-border/70 border-s px-2.5 py-1.5 text-start font-medium">{t("Status", "الحالة")}</th>
            </tr>
            <tr className="bg-muted/60 text-muted-foreground">
              <th className="border-border/70 border-s px-2.5 py-1 text-end font-medium">{t("Qty", "الكمية")}</th>
              <th className="px-2.5 py-1 text-end font-medium">{t("Unit price", "سعر الوحدة")}</th>
              <th className="border-border/70 border-s px-2.5 py-1 text-end font-medium">{t("Qty", "الكمية")}</th>
              <th className="px-2.5 py-1 text-end font-medium">{t("Unit price", "سعر الوحدة")}</th>
              <th className="px-2.5 py-1 text-end font-medium">{t("Amount", "المجموع")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const s = status(r)
              return (
                <tr
                  key={r.item_code}
                  className={cn("border-border/70 border-t", !r.inv && "text-muted-foreground bg-muted/20")}
                >
                  <td className="px-2.5 py-1.5 font-medium" dir="ltr">{r.item_code}</td>
                  <td className="max-w-[220px] truncate px-2.5 py-1.5" dir="auto">{r.description}</td>
                  <td className="px-2.5 py-1.5" dir="auto">{r.unit || "—"}</td>
                  <td className={cn(num, "border-border/70 border-s")}>
                    {r.boq ? (
                      <span className="inline-flex items-center gap-1">
                        {r.boq.quantity.toLocaleString("en-US")}
                        {canLocate("contract_boq") && <LocateBtn side="boq" row={r} />}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={num}>{r.boq ? formatMoney(r.boq.unit_price) : "—"}</td>
                  <td className={cn(num, "border-border/70 border-s")}>
                    {r.inv ? (
                      <span className="inline-flex items-center gap-1">
                        {r.inv.quantity.toLocaleString("en-US")}
                        {canLocate("invoice") && <LocateBtn side="inv" row={r} />}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={cn(num, r.boq && r.inv && Math.abs(r.boq.unit_price - r.inv.unit_price) > 0.01 && "text-destructive font-semibold")}>
                    {r.inv ? formatMoney(r.inv.unit_price) : "—"}
                  </td>
                  <td className={num}>{r.inv ? formatMoney(r.inv.amount) : "—"}</td>
                  <td className={cn("border-border/70 border-s px-2.5 py-1.5 font-medium", s.cls)}>{s.label}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {unbilled > 0 && (
        <p className="text-muted-foreground mt-2 text-xs">
          {claim?.claim_type === "final"
            ? t(
                `${unbilled} contracted line(s) remain unbilled — unexpected for a FINAL claim.`,
                `${unbilled === 1 ? "بند تعاقدي واحد لم يُفوتر" : unbilled === 2 ? "بندان تعاقديان لم يُفوترا" : `${unbilled} بنود تعاقدية لم تُفوتر`} — غير متوقع في مستخلص نهائي.`
              )
            : t(
                `${unbilled} contracted line(s) not billed in this claim — normal for periodic claims; they remain claimable in later payments.`,
                `${unbilled === 1 ? "بند تعاقدي واحد لم يُفوتر" : unbilled === 2 ? "بندان تعاقديان لم يُفوترا" : `${unbilled} بنود تعاقدية لم تُفوتر`} في هذه المطالبة — أمر طبيعي في المستخلصات الدورية ويمكن المطالبة بها في دفعات لاحقة.`
              )}
        </p>
      )}
    </section>
  )
}
