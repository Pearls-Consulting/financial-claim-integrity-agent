import { useLang } from "@/lib/i18n"
import { cn } from "@/lib/utils"

/** Segmented EN / ع switch, matching the prequalification agent's header.
 *  Switching is global (context + persisted) and flips the app to RTL. */
export function LangToggle({ className }: { className?: string }) {
  const { lang, toggle } = useLang()
  return (
    <div
      role="group"
      aria-label="Language / اللغة"
      className={cn("border-border bg-card inline-flex items-center rounded-full border p-0.5", className)}
    >
      <Segment active={lang === "en"} onClick={() => lang !== "en" && toggle()}>
        EN
      </Segment>
      <Segment active={lang === "ar"} onClick={() => lang !== "ar" && toggle()} arabic>
        ع
      </Segment>
    </div>
  )
}

function Segment({
  active,
  onClick,
  arabic = false,
  children,
}: {
  active: boolean
  onClick: () => void
  arabic?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "min-w-7 rounded-full px-2 py-0.5 text-xs font-medium transition",
        arabic && "font-arabic text-sm leading-none",
        active
          ? "bg-primary text-primary-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  )
}
