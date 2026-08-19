import { BrowserRouter, Link, Route, Routes } from "react-router-dom"

import sdbLogo from "@/assets/sdb-logo.svg"
import { LangToggle } from "@/components/LangToggle"
import { PdfViewerProvider } from "@/components/PdfViewerContext"
import { PdfViewerPanel } from "@/components/PdfViewerPanel"
import { LanguageProvider, useLang } from "@/lib/i18n"
import { ClaimsListPage } from "@/pages/ClaimsListPage"
import { ClaimDetailPage, SubmitClaimPage } from "@/pages/ReviewWizard"

function AppHeader() {
  const { t } = useLang()
  return (
    <header className="border-border/70 bg-card border-b">
      <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-4">
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
              {t("Claim Integrity Agent", "وكيل سلامة المطالبات")}
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
        </div>
      </div>
    </header>
  )
}

function Shell() {
  return (
    <div className="flex min-h-svh flex-col">
      <AppHeader />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-16 pt-6">
        <Routes>
          <Route path="/" element={<ClaimsListPage />} />
          <Route path="/claims/:id" element={<ClaimDetailPage />} />
          <Route path="/submit" element={<SubmitClaimPage />} />
        </Routes>
      </main>
      <PdfViewerPanel />
    </div>
  )
}

export default function App() {
  return (
    <LanguageProvider>
      <PdfViewerProvider>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </PdfViewerProvider>
    </LanguageProvider>
  )
}
