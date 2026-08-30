import { startTransition } from "react"
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom"
import { LogOut } from "lucide-react"

import sdbLogo from "@/assets/sdb-logo.svg"
import { LangToggle } from "@/components/LangToggle"
import { PdfViewerProvider } from "@/components/PdfViewerContext"
import { PdfViewerPanel } from "@/components/PdfViewerPanel"
import { ProtectedRoute } from "@/components/ProtectedRoute"
import { AuthProvider, useAuth } from "@/lib/auth-context"
import { LanguageProvider, useLang } from "@/lib/i18n"
import { ClaimsListPage } from "@/pages/ClaimsListPage"
import { LoginPage } from "@/pages/LoginPage"
import { ClaimDetailPage, SubmitClaimPage } from "@/pages/ReviewWizard"

/** Signed-in identity + sign-out, mirroring the prequalification header.
 *  Single role: the procedure's step-1 actor (SP-01-04-05-02: أخصائي إدارة
 *  الموردين) — the name comes from the session, the unit is fixed. */
function UserBadge() {
  const { t } = useLang()
  const { user, signOut } = useAuth()
  const navigate = useNavigate()

  if (!user) return null

  function onSignOut() {
    // React Router runs navigate() as a transition; the auth-state clear must
    // sit in the same transition or it renders first — anon with the guard
    // still mounted on this page — and the guard's /login?next=… wins over
    // our /login. One transition = one render with both applied.
    startTransition(() => {
      navigate("/login", { replace: true })
      void signOut()
    })
  }

  return (
    <div className="flex shrink-0 items-center gap-3">
      <div className="hidden max-w-[14rem] text-end leading-tight lg:block">
        <div className="text-foreground truncate text-sm font-medium">
          {t(user.displayName, user.displayNameAr || user.displayName)}
        </div>
        <div className="text-muted-foreground truncate text-xs">
          {t("Planning & Vendor Management", "إدارة التخطيط وإدارة الموردين")}
        </div>
      </div>
      <button
        type="button"
        onClick={onSignOut}
        title={t("Sign out", "تسجيل الخروج")}
        aria-label={t("Sign out", "تسجيل الخروج")}
        className="border-border bg-card text-muted-foreground hover:text-foreground grid size-9 shrink-0 place-items-center rounded-full border transition"
      >
        <LogOut className="size-4 rtl:rotate-180" />
      </button>
    </div>
  )
}

function AppHeader() {
  const { t } = useLang()
  return (
    <header className="border-border/70 border-b">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-6 py-5">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            to="/"
            aria-label={t("Social Development Bank", "بنك التنمية الاجتماعية")}
            className="focus-visible:ring-ring/40 inline-flex shrink-0 items-center rounded-sm transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2"
          >
            <img src={sdbLogo} alt="" className="h-9 w-auto select-none" draggable={false} />
          </Link>
          <div className="bg-border/70 h-10 w-px" />
          <div className="min-w-0 leading-tight">
            <span className="font-display block truncate text-xl">
              {t("Claim Integrity Agent", "وكيل تدقيق المطالبات")}
            </span>
            <div className="text-muted-foreground mt-0.5 truncate text-xs">
              {t(
                "Reads, cross-checks, and clears vendor claims before disbursement",
                "يقرأ ويطابق ويدقق مطالبات الموردين قبل الصرف"
              )}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <LangToggle />
          <div className="bg-border/70 h-8 w-px" />
          <UserBadge />
        </div>
      </div>
    </header>
  )
}

/** The signed-in layout: header, then the page outlet and the document
 *  reader side by side. The reader is a flex sibling (not an overlay), so
 *  opening it narrows the page instead of covering it; `items-start` lets
 *  the reader stay sticky while the page scrolls. */
function Shell() {
  return (
    <div className="flex min-h-svh flex-col">
      <AppHeader />
      <div className="flex flex-1 items-start">
        <main className="mx-auto w-full min-w-0 max-w-6xl flex-1 px-6 pb-16 pt-6">
          <Outlet />
        </main>
        <PdfViewerPanel />
      </div>
    </div>
  )
}

export default function App() {
  return (
    <LanguageProvider>
      <AuthProvider>
        <PdfViewerProvider>
          <BrowserRouter>
            <Routes>
              {/* Public */}
              <Route path="/login" element={<LoginPage />} />

              {/* Everything else needs a session */}
              <Route element={<ProtectedRoute />}>
                <Route element={<Shell />}>
                  <Route path="/" element={<ClaimsListPage />} />
                  <Route path="/claims/:id" element={<ClaimDetailPage />} />
                  <Route path="/submit" element={<SubmitClaimPage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Route>
              </Route>
            </Routes>
          </BrowserRouter>
        </PdfViewerProvider>
      </AuthProvider>
    </LanguageProvider>
  )
}
