import * as React from "react"
import { Link } from "react-router-dom"
import { Database, FilePlus2, ReceiptText, Sparkles } from "lucide-react"

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
        {t(`In progress — step ${claim.review_step}/6`, `قيد المراجعة — الخطوة ${claim.review_step} من 6`)}
      </span>
    )
  return <span className="text-muted-foreground text-xs">{t("Not started", "لم تبدأ بعد")}</span>
}

/**
 * Front-page hero — what this workspace is for, in one calm sentence.
 * Mirrors the prequalification agent's IntroBanner (brand-tinted gradient,
 * icon tile, two CTAs). Arabic is native procurement register drawn from the
 * procedure's own verbiage (صرف مستحقات الموردين), not a literal translation.
 */
function IntroBanner() {
  const { t, lang } = useLang()
  return (
    <section className="border-primary/15 from-primary/[0.10] via-card to-gold/[0.10] relative overflow-hidden rounded-2xl border bg-gradient-to-br px-7 py-8 sm:px-9 sm:py-10">
      {/* Soft decorative glows that give the hero a little depth. */}
      <div aria-hidden className="bg-gold/10 pointer-events-none absolute -end-20 -top-24 size-64 rounded-full blur-3xl" />
      <div aria-hidden className="bg-primary/10 pointer-events-none absolute -bottom-24 -start-16 size-56 rounded-full blur-3xl" />
      <div className="relative flex items-start gap-5">
        <span className="from-primary to-primary/75 text-primary-foreground ring-primary/20 relative grid size-14 shrink-0 place-items-center rounded-2xl bg-gradient-to-br shadow-md ring-1">
          <ReceiptText className="size-7" />
          <span className="bg-gold ring-card absolute -end-1.5 -top-1.5 grid size-6 place-items-center rounded-full text-white shadow-sm ring-2">
            <Sparkles className="size-3.5" />
          </span>
        </span>
        <div className="min-w-0">
          <h2 className="font-display text-foreground text-2xl leading-tight sm:text-[28px]">
            {t("Vendor Claims, Reviewed End to End", "مطالبات الموردين، من الاستلام إلى الصرف")}
          </h2>
          <p className="text-muted-foreground mt-3 max-w-2xl text-[15px] leading-relaxed">
            {lang === "ar" ? (
              <>
                يدقق وكيلك الذكي مطالبات صرف مستحقات الموردين: يتحقق من صحة{" "}
                <span className="text-foreground font-medium">الفاتورة الضريبية</span>، ويجري{" "}
                <span className="text-foreground font-medium">المطابقة الثلاثية</span> بين العقد
                وإيصال الاستلام والفاتورة — ثم يحيل إليك ما يستدعي المراجعة لاعتماده. وكل ملاحظة
                مسندة إلى مرجعها النظامي.
              </>
            ) : (
              <>
                Your AI agent reads each vendor claim, verifies the{" "}
                <span className="text-foreground font-medium">tax invoice</span> against ZATCA, and
                runs the <span className="text-foreground font-medium">three-way match</span> across
                contract, receipt, and invoice — then routes anything uncertain to you for
                sign-off. Every finding cites its source.
              </>
            )}
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button size="lg" asChild>
              <Link to="/submit">
                <FilePlus2 />
                {t("New claim review", "مراجعة مطالبة جديدة")}
              </Link>
            </Button>
            <Button size="lg" variant="outline" asChild>
              <a href="#claims">
                <Database />
                {t("Claims from Dynamics 365", "المطالبات الواردة من Dynamics 365")}
              </a>
            </Button>
          </div>
        </div>
      </div>
    </section>
  )
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
      <IntroBanner />
      <div id="claims" className="mt-8 scroll-mt-6">
        <h1 className="font-display text-2xl">{t("Claims intake", "استلام المطالبات")}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {t(
            "Vendor claims pulled from the ERP, awaiting integrity review.",
            "المطالبات الواردة من نظام تخطيط الموارد، بانتظار المراجعة والاعتماد."
          )}
        </p>
      </div>
      {error && <p className="text-destructive mt-4 text-sm">{error}</p>}
      <div className="mt-5 overflow-x-auto rounded-xl border border-border bg-card shadow-xs">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50 text-start text-xs text-muted-foreground">
              <th className="p-3 text-start font-medium">{t("Claim no.", "رقم المطالبة")}</th>
              <th className="p-3 text-start font-medium">{t("Vendor", "اسم المورد")}</th>
              <th className="p-3 text-start font-medium">{t("Project", "اسم المشروع")}</th>
              <th className="p-3 text-start font-medium">{t("PO", "أمر الشراء")}</th>
              <th className="p-3 text-end font-medium">{t("Contract value (base)", "قيمة العقد (قبل الضريبة)")}</th>
              <th className="p-3 text-end font-medium">{t("Claim (incl. VAT)", "مبلغ المطالبة (شامل الضريبة)")}</th>
              <th className="p-3 text-start font-medium">{t("Review status", "حالة المراجعة")}</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((c) => (
              <tr key={c.id} className="border-b border-border last:border-0 hover:bg-muted/60">
                <td className="p-3 whitespace-nowrap">
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
