import * as React from "react"

/**
 * Lightweight bilingual (English / Arabic) layer, carried over from the
 * prequalification agent: every English string is paired with its Arabic
 * equivalent inline via `t("English", "العربية")` so translations live next
 * to the markup. The provider drives document direction (`dir="rtl"`) so the
 * whole layout mirrors natively, and persists the choice across reloads.
 */

export type Lang = "en" | "ar"

interface LangContextValue {
  lang: Lang
  dir: "ltr" | "rtl"
  setLang: (l: Lang) => void
  toggle: () => void
  t: (en: string, ar: string) => string
  pick: <T>(en: T, ar: T | null | undefined) => T
}

const LangContext = React.createContext<LangContextValue | null>(null)
const STORAGE_KEY = "cia.lang"

function initialLang(): Lang {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "ar" ? "ar" : "en"
  } catch {
    return "en"
  }
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = React.useState<Lang>(initialLang)
  const dir: "ltr" | "rtl" = lang === "ar" ? "rtl" : "ltr"

  React.useEffect(() => {
    const root = document.documentElement
    root.lang = lang
    root.dir = dir
    try {
      window.localStorage.setItem(STORAGE_KEY, lang)
    } catch {
      /* storage unavailable — keep the in-memory choice */
    }
  }, [lang, dir])

  const value = React.useMemo<LangContextValue>(
    () => ({
      lang,
      dir,
      setLang,
      toggle: () => setLang((l) => (l === "en" ? "ar" : "en")),
      t: (en, ar) => (lang === "ar" ? ar : en),
      // Fall back across languages when the preferred value is MISSING —
      // and a blank string is missing: claim headers arrive with one name
      // slot filled and the other "", whichever language the clerk typed.
      pick: <T,>(en: T, ar: T | null | undefined): T => {
        const blank = (v: T | null | undefined) => v == null || (typeof v === "string" && v.trim() === "")
        return lang === "ar" ? ((blank(ar) ? en : ar) as T) : ((blank(en) ? (ar ?? en) : en) as T)
      },
    }),
    [lang, dir]
  )

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>
}

export function useLang(): LangContextValue {
  const ctx = React.useContext(LangContext)
  if (!ctx) throw new Error("useLang must be used within a LanguageProvider")
  return ctx
}
