import { Check, FileSearch, QrCode, X } from "lucide-react"

import { StatusPill } from "@/components/StatusPill"
import { usePdfViewer } from "@/components/PdfViewerContext"
import { useLang } from "@/lib/i18n"
import type { Claim, QrSummary } from "@/types/domain"

/**
 * The decoded ZATCA QR next to the invoice face — the "the QR says X, the
 * invoice prints Y" table. Values are decoded deterministically server-side;
 * each row's face value can be located + highlighted on the invoice PDF.
 */

const PHASE2_META: Record<string, { en: string; ar: string; status: "ok" | "warn" | "fail" }> = {
  valid: { en: "Phase-2 signature verifies", ar: "التوقيع الرقمي للمرحلة الثانية صحيح", status: "ok" },
  absent: { en: "Phase-1 only (no attestation)", ar: "المرحلة الأولى فقط (بدون توثيق)", status: "warn" },
  pseudo: { en: "Imitation phase-2 tags", ar: "وسوم مرحلة ثانية مقلّدة", status: "warn" },
  invalid_signature: { en: "Signature does NOT verify", ar: "التوقيع الرقمي غير صحيح", status: "fail" },
}

export function QrPanel({ qr, claim }: { qr: QrSummary; claim?: Claim | null }) {
  const { t, pick, lang } = useLang()
  const { openDocument } = usePdfViewer()

  if (!qr.present) {
    return (
      <section className="rounded-xl border border-border bg-card p-4">
        <Header />
        <p className="text-muted-foreground mt-2 text-sm">
          {t("No QR code found on the invoice.", "لا يوجد رمز استجابة على الفاتورة.")}
        </p>
      </section>
    )
  }
  if (!qr.valid_tlv) {
    return (
      <section className="rounded-xl border border-border bg-card p-4">
        <Header />
        <p className="text-destructive mt-2 text-sm">
          {t("QR payload is not valid ZATCA TLV", "محتوى الرمز ليس بصيغة هيئة الزكاة النظامية")}
          {qr.error ? ` — ${qr.error}` : ""}.
        </p>
      </section>
    )
  }

  const invoiceIndex = claim?.source_files.findIndex((f) => f.doc_type === "invoice") ?? -1
  const locate = (value: string, label: string) => {
    if (!claim || invoiceIndex === -1) return
    openDocument({
      claimId: claim.id,
      index: invoiceIndex,
      fileName: claim.source_files[invoiceIndex].path.split("/").pop(),
      highlight: value,
      fieldName: `${t("ZATCA QR", "رمز الاستجابة")} — ${label}`,
    })
  }

  const p2 = PHASE2_META[qr.phase2_status]

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Header />
        {p2 && (
          <span className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">{pick(p2.en, p2.ar)}</span>
            <StatusPill status={p2.status} />
          </span>
        )}
      </div>

      <div className="border-border/70 mt-3 overflow-hidden rounded-lg border">
        <div className="text-muted-foreground bg-muted/60 grid grid-cols-[1fr_1.2fr_1.2fr_auto] gap-2 px-3 py-1.5 text-xs font-medium">
          <span>{t("Field", "الحقل")}</span>
          <span>{t("Decoded from QR", "المستخرج من الرمز")}</span>
          <span>{t("Invoice face", "بيانات الفاتورة")}</span>
          <span className="w-12 text-center">{t("Match", "تطابق")}</span>
        </div>
        {qr.fields.map((f) => (
          <div
            key={f.key}
            className="border-border/70 grid grid-cols-[1fr_1.2fr_1.2fr_auto] items-center gap-2 border-t px-3 py-1.5 text-xs"
          >
            <span className="text-muted-foreground truncate">{pick(f.label_en, f.label_ar)}</span>
            <span className="truncate font-medium tabular-nums" dir="auto" title={f.value}>
              {f.value || "—"}
            </span>
            <span className="flex min-w-0 items-center gap-1">
              <span className="truncate tabular-nums" dir="auto" title={f.expected}>
                {f.expected || "—"}
              </span>
              {f.expected && claim && invoiceIndex !== -1 && (
                <button
                  type="button"
                  onClick={() => locate(f.expected, pick(f.label_en, f.label_ar))}
                  className="text-primary hover:bg-secondary shrink-0 rounded p-0.5 transition"
                  aria-label={t("Locate in invoice", "تحديد الموضع في الفاتورة")}
                  title={t("Locate in invoice", "تحديد الموضع في الفاتورة")}
                >
                  <FileSearch className="size-3.5" />
                </button>
              )}
            </span>
            <span className="grid w-12 place-items-center">
              {f.match === null ? (
                <span className="text-muted-foreground">—</span>
              ) : f.match ? (
                <Check className="text-ok size-4" />
              ) : (
                <X className="text-destructive size-4" />
              )}
            </span>
          </div>
        ))}
      </div>

      {(qr.phase2_problems.length > 0 || (lang === "en" && qr.phase2_notes.length > 0)) && (
        <ul className="text-muted-foreground mt-2 space-y-0.5 text-xs">
          {qr.phase2_problems.map((p, i) => (
            <li key={`p${i}`} className="text-warn">
              • {p}
            </li>
          ))}
          {lang === "en" && qr.phase2_notes.map((n, i) => (
            <li key={`n${i}`}>• {n}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Header() {
  const { t } = useLang()
  return (
    <h2 className="flex items-center gap-2 text-sm font-semibold">
      <QrCode className="text-muted-foreground size-4" />
      {t("ZATCA QR — extracted fields", "رمز الاستجابة السريعة (QR) — الحقول المستخرجة")}
    </h2>
  )
}
