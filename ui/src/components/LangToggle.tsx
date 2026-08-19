import { Languages } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useLang } from "@/lib/i18n"

export function LangToggle() {
  const { lang, toggle } = useLang()
  return (
    <Button variant="outline" size="sm" onClick={toggle}>
      <Languages />
      {lang === "en" ? "العربية" : "English"}
    </Button>
  )
}
