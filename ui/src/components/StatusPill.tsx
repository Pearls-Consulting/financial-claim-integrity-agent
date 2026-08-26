import { useLang } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import type { Severity, Verdict } from "@/types/domain"

const META: Record<Severity | Verdict, { en: string; ar: string; pill: string; dot: string }> = {
  ok: { en: "Pass", ar: "مطابق", pill: "border-ok/30 bg-ok/10 text-ok", dot: "bg-ok" },
  warn: { en: "Review", ar: "ملاحظة", pill: "border-warn/30 bg-warn/10 text-warn", dot: "bg-warn" },
  fail: { en: "Fail", ar: "مخالفة", pill: "border-destructive/30 bg-destructive/10 text-destructive", dot: "bg-destructive" },
  approve: { en: "Recommend approve", ar: "توصية بالاعتماد", pill: "border-ok/30 bg-ok/10 text-ok", dot: "bg-ok" },
  needs_human: { en: "Needs human review", ar: "تتطلب مراجعة المختص", pill: "border-warn/30 bg-warn/10 text-warn", dot: "bg-warn" },
  reject: { en: "Recommend reject", ar: "توصية بالرفض", pill: "border-destructive/30 bg-destructive/10 text-destructive", dot: "bg-destructive" },
}

export function StatusPill({ status, className }: { status: Severity | Verdict; className?: string }) {
  const { pick } = useLang()
  const meta = META[status]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        meta.pill,
        className
      )}
    >
      <span className={cn("size-1.5 rounded-full", meta.dot)} />
      {pick(meta.en, meta.ar)}
    </span>
  )
}
