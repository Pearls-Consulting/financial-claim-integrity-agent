import * as React from "react"

/**
 * Evidence value → embedded PDF reader (ported from the prequalification
 * agent). A finding's evidence chip calls `openDocument(...)` to dock the
 * reader on the end edge, land on the document, and highlight the value in
 * the page — the "the agent read THIS number HERE" moment.
 */
export interface PdfViewRequest {
  claimId: string
  /** Index into the claim's source_files (claimFileUrl uses this). */
  index: number
  /** Original filename, shown in the reader header. */
  fileName?: string
  /** 1-based page to land on (the page the value was read from, if known). */
  page?: number | null
  /** Text to locate + highlight (searched across the whole document). */
  highlight?: string
  /** Row context (the BoQ item code, the unit price next to a quantity):
   *  marked faintly alongside the value on the page it is shown on, and
   *  offered to the OCR fallback for the cited page only. Never used to
   *  search for a page — the value is. */
  highlightAlso?: string[]
  /** Weak anchor — the verbatim clause/excerpt the value came from. Tried
   *  LAST by the OCR-locate fallback (it usually leads with the label). */
  highlightExtra?: string
  /** Field label, shown under the filename for context. */
  fieldName?: string
  /** Bumped per request so re-opening the same document re-runs the jump. */
  nonce: number
}

interface PdfViewerValue {
  request: PdfViewRequest | null
  open: boolean
  openDocument: (req: Omit<PdfViewRequest, "nonce">) => void
  close: () => void
}

const Ctx = React.createContext<PdfViewerValue | null>(null)

export function PdfViewerProvider({ children }: { children: React.ReactNode }) {
  const [request, setRequest] = React.useState<PdfViewRequest | null>(null)
  const [open, setOpen] = React.useState(false)
  const nonce = React.useRef(0)

  const value = React.useMemo<PdfViewerValue>(
    () => ({
      request,
      open,
      openDocument: (req) => {
        nonce.current += 1
        setRequest({ ...req, nonce: nonce.current })
        setOpen(true)
      },
      close: () => setOpen(false),
    }),
    [request, open]
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function usePdfViewer(): PdfViewerValue {
  const v = React.useContext(Ctx)
  if (!v) return { request: null, open: false, openDocument: () => {}, close: () => {} }
  return v
}
