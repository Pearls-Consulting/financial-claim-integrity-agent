import * as React from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Loader2, LogIn } from "lucide-react"

import sdbLogo from "@/assets/sdb-logo.svg"
import { LangToggle } from "@/components/LangToggle"
import { useAuth } from "@/lib/auth-context"
import { useLang } from "@/lib/i18n"

/**
 * Sign-in screen — the same composition as the prequalification agent's
 * LoginPage: a single centered, borderless panel with the form on the start
 * side, the SDB wordmark on the end side, and a soft fading divider between.
 * Honours a `?next=` destination from ProtectedRoute, falling back to the
 * claims list. One role, so no role-home routing.
 */
export function LoginPage() {
  const { t } = useLang()
  const { signIn, status, user } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get("next"))

  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  // Already signed in (e.g. navigated here manually) → skip the form.
  React.useEffect(() => {
    if (status === "authed" && user) navigate(next, { replace: true })
  }, [status, user, next, navigate])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(email.trim(), password)
      navigate(next, { replace: true })
    } catch (err) {
      // The server's detail is English; show the house phrasing per language.
      setError(
        err instanceof Error && err.message !== "Invalid email or password"
          ? err.message
          : t("Invalid email or password.", "البريد الإلكتروني أو كلمة المرور غير صحيحة.")
      )
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    "border-input bg-card focus:border-primary focus-visible:ring-ring/40 mt-1.5 w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus-visible:ring-2"

  return (
    <div className="bg-background text-foreground relative grid min-h-svh place-items-center px-6">
      <LangToggle className="absolute end-6 top-6" />

      <div className="flex w-full max-w-3xl flex-col items-stretch gap-10 md:flex-row md:gap-0">
        {/* Start side — credentials */}
        <div className="flex-1 md:pe-12">
          <h1 className="font-display text-2xl">{t("Sign in", "تسجيل الدخول")}</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            {t(
              "For the Vendor Management Specialist reviewing vendor claims.",
              "لأخصائي إدارة الموردين المكلّف بتدقيق مطالبات الموردين."
            )}
          </p>

          <form onSubmit={onSubmit} className="mt-6 space-y-4">
            <label className="block">
              <span className="text-sm font-medium">{t("Email", "البريد الإلكتروني")}</span>
              <input
                type="email"
                autoComplete="username"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                dir="ltr"
                className={inputClass}
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium">{t("Password", "كلمة المرور")}</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                dir="ltr"
                className={inputClass}
              />
            </label>

            {error && (
              <p role="alert" className="bg-destructive/10 text-destructive rounded-lg px-3 py-2 text-sm">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition disabled:opacity-60"
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <LogIn className="size-4 rtl:rotate-180" />
              )}
              {t("Sign in", "تسجيل الدخول")}
            </button>
          </form>

          <p className="border-border/60 text-muted-foreground mt-5 border-t pt-4 text-xs">
            {t(
              "Access is by invitation. Credentials for this evaluation environment are issued by Pearls Consulting.",
              "الدخول بالدعوة فقط. تُصدر بيرلز للاستشارات بيانات الدخول لبيئة التقييم هذه."
            )}
          </p>
        </div>

        {/* Soft fading divider — horizontal when stacked, vertical side-by-side */}
        <div
          aria-hidden
          className="via-border h-px w-full shrink-0 bg-gradient-to-r from-transparent to-transparent md:h-auto md:w-px md:self-stretch md:bg-gradient-to-b"
        />

        {/* End side — brand */}
        <div className="flex flex-1 flex-col items-center justify-center gap-4 md:ps-12">
          <img src={sdbLogo} alt={t("Social Development Bank", "بنك التنمية الاجتماعية")} className="h-16 w-auto select-none" draggable={false} />
          <div className="text-center">
            <div className="font-display text-base">
              {t("Claim Integrity Agent", "وكيل تدقيق المطالبات")}
            </div>
            <div className="text-muted-foreground mt-1 text-xs">
              {t(
                "Reads, cross-checks, and clears vendor claims before disbursement",
                "يقرأ ويطابق ويدقق مطالبات الموردين قبل الصرف"
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Only follow in-app paths — never an absolute URL smuggled into ?next=. */
function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/login")) return "/"
  return raw
}

export default LoginPage
