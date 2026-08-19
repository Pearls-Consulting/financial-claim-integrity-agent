import * as React from "react"
import { Link } from "react-router-dom"
import { FilePlus2 } from "lucide-react"

import { StatusPill } from "@/components/StatusPill"
import { Button } from "@/components/ui/button"
import { useLang } from "@/lib/i18n"
import { api } from "@/lib/api"
import { formatMoney } from "@/lib/utils"
import type { Claim } from "@/types/domain"

/** Where the guided review stands — from the persisted progress + latest run. */
function ReviewStatus({ claim }: { claim: Claim }) {
  const { t } = useLang()
  if (claim.review_step >= 6 && claim.latest_verdict) return <StatusPill status={claim.latest_verdict} />
  if (claim.review_step > 0)
    return (
      <span className="border-warn/30 bg-warn/10 text-warn inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium">
        <span className="bg-warn size-1.5 rounded-full" />
        {t(`In progress — step ${claim.review_step}/6`, `قيد الفحص — الخطوة ${claim.review_step}/6`)}
      </span>
    )
  return <span className="text-muted-foreground text-xs">{t("Not started", "لم يبدأ")}</span>
}

/** Mirrors the ERP's استلام المطالبات list — the entry point of the review. */
export function ClaimsListPage() {
  const { t, pick } = useLang()
  const [claims, setClaims] = React.useState<Claim[]>([])
  const [error, setError] = React.useState("")

  React.useEffect(() => {
    api.claims().then(setClaims).catch((e) => setError(String(e)))
  }, [])

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl">{t("Claims intake", "استلام المطالبات")}</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            {t(
              "Vendor claims pulled from the ERP, awaiting integrity review.",
              "مطالبات الموردين من نظام تخطيط الموارد، بانتظار فحص السلامة."
            )}
          </p>
        </div>
        <Button asChild>
          <Link to="/submit">
            <FilePlus2 />
            {t("New claim review", "فحص مطالبة جديدة")}
          </Link>
        </Button>
      </div>
      {error && <p className="text-destructive mt-4 text-sm">{error}</p>}
      <div className="mt-5 overflow-x-auto rounded-xl border border-border bg-card shadow-xs">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50 text-start text-xs text-muted-foreground">
              <th className="p-3 text-start font-medium">{t("Claim no.", "رقم الطلب")}</th>
              <th className="p-3 text-start font-medium">{t("Vendor", "اسم المورد")}</th>
              <th className="p-3 text-start font-medium">{t("Project", "اسم المشروع")}</th>
              <th className="p-3 text-start font-medium">{t("PO", "أمر الشراء")}</th>
              <th className="p-3 text-end font-medium">{t("Contract value", "قيمة العقد")}</th>
              <th className="p-3 text-end font-medium">{t("Claim (incl. VAT)", "مبلغ المطالبة (شامل الضريبة)")}</th>
              <th className="p-3 text-start font-medium">{t("Review status", "حالة الفحص")}</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.id} className="border-b border-border last:border-0 hover:bg-muted/60">
                <td className="p-3">
                  <Link to={`/claims/${c.id}`} className="text-primary font-medium hover:underline">
                    {c.id}
                  </Link>
                </td>
                <td className="p-3">{pick(c.vendor_name_en, c.vendor_name_ar)}</td>
                <td className="max-w-72 truncate p-3">{pick(c.project_name_en, c.project_name_ar)}</td>
                <td className="p-3">{c.po_no}</td>
                <td className="p-3 text-end tabular-nums">{formatMoney(c.contract_value)}</td>
                <td className="p-3 text-end tabular-nums">{formatMoney(c.claim_amount_total)}</td>
                <td className="p-3">
                  <ReviewStatus claim={c} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
