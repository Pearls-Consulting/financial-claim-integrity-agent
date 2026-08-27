import * as React from "react"
import { Document, Page, pdfjs } from "react-pdf"
import {
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Loader2,
  Minus,
  Plus,
  X,
} from "lucide-react"
import "react-pdf/dist/Page/TextLayer.css"
import "react-pdf/dist/Page/AnnotationLayer.css"

import { useLang } from "@/lib/i18n"
import { claimFileUrl, locateInDocument, type LocatePoint } from "@/lib/api"
import { usePdfViewer, type PdfViewRequest } from "@/components/PdfViewerContext"

/**
 * Embedded document reader with value highlighting, ported from the
 * prequalification agent's PdfViewerPanel (its proven normalization +
 * text-layer matching), INCLUDING the Azure-CU polygon fallback: when the
 * text layer can't be matched — scanned contracts, rotated BoQ tables — the
 * server OCR-locates the value and the viewer draws its word polygons over
 * the rendered page.
 */

// pdf.js parses/renders off the main thread; Vite bundles the shipped worker.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString()

// Fold Arabic-Indic / Persian digits to ASCII so an extracted "178250" matches
// the "١٧٨٢٥٠" actually printed. Char-for-char, so match indexes map back.
const AR_DIGITS: Record<string, string> = {
  "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
  "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
  "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
  "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
}

function foldChar(c: string | undefined): string {
  return c == null ? "" : (AR_DIGITS[c] ?? c)
}

function isDigit(c: string): boolean {
  return c >= "0" && c <= "9"
}

// Tatweel/kashida + harakat + superscript alef: decorative for matching.
function isArabicMark(c: string): boolean {
  const cp = c.codePointAt(0) ?? 0
  return cp === 0x0640 || (cp >= 0x064b && cp <= 0x0652) || cp === 0x0670
}

// A thousands separator is a comma/space between a digit and EXACTLY three
// digits ("63,333.33" → "63333.33"); a decimal comma ("2,50") stays, so the
// printed 2,50% can match the 2.50% candidate. Mirrors the server's _norm.
function isThousandsSep(s: string, i: number): boolean {
  const folded = foldChar(s[i])
  if (folded !== "," && folded !== " " && folded !== "٬") return false
  if (!isDigit(foldChar(s[i - 1]))) return false
  for (let k = 1; k <= 3; k++) if (!isDigit(foldChar(s[i + k]))) return false
  return !isDigit(foldChar(s[i + 4]))
}

// Normalise for matching while keeping a map back to the ORIGINAL string:
// Arabic-Indic digits → ASCII, the Arabic decimal separator → ".",
// presentation forms → base letters (NFKC), tatweel/harakat dropped,
// thousands separators dropped. map[k] = source index of norm char k.
function normalizeWithMap(s: string): { norm: string; map: number[] } {
  let norm = ""
  const map: number[] = []
  for (let i = 0; i < s.length; i++) {
    if (isThousandsSep(s, i)) continue
    const folded = foldChar(s[i]) === "٫" ? "." : foldChar(s[i])
    for (const c of folded.normalize("NFKC")) {
      if (isArabicMark(c)) continue
      norm += c
      map.push(i)
    }
  }
  return { norm: norm.toLowerCase(), map }
}

function normalize(s: string): string {
  return normalizeWithMap(s).norm
}

// Parse "YYYY-MM-DD" / "D/M/Y" style dates into components, else null.
function parseDate(value: string): { y: number; m: number; d: number } | null {
  const s = value.trim()
  let mt = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/)
  if (mt) return { y: +mt[1], m: +mt[2], d: +mt[3] }
  mt = s.match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/)
  if (mt) return { y: +mt[3], m: +mt[2], d: +mt[1] }
  return null
}

// Candidate printed forms of a date — documents render the same date many ways.
function dateVariants(value: string): string[] {
  const p = parseDate(value)
  if (!p) return []
  const { y, m, d } = p
  if (m < 1 || m > 12 || d < 1 || d > 31) return []
  const dd = String(d).padStart(2, "0")
  const mm = String(m).padStart(2, "0")
  const out: string[] = []
  for (const s of ["/", "-", ".", " "]) {
    out.push(`${dd}${s}${mm}${s}${y}`, `${d}${s}${m}${s}${y}`)
    out.push(`${y}${s}${mm}${s}${dd}`, `${y}${s}${m}${s}${d}`, `${mm}${s}${dd}${s}${y}`)
  }
  return out
}

// Candidate printed forms of ONE value: itself, date variants, and — for a
// numeric value — the 2-decimal money rendering ("55000" also tries
// "55000.00", matching the printed "55,000.00" after normalization).
function rawCandidates(raw: string): string[] {
  const v = (raw ?? "").trim()
  if (!v) return []
  const out = [v, ...dateVariants(v)]
  const n = Number(v.replace(/,/g, ""))
  if (Number.isFinite(n) && /^[\d.,]+$/.test(v)) {
    out.push(n.toFixed(2), String(n))
  }
  // Decimal-comma printing ("2.50" → "2,50"); not for a 3-digit fraction,
  // which would read as a thousand.
  for (const c of [...out]) {
    if (/\d\.\d/.test(c) && !/\d\.\d{3}(?!\d)/.test(c)) out.push(c.replace(/(\d)\.(?=\d)/g, "$1,"))
  }
  return out
}

// Boolean / derived values are never literal page text — don't highlight them.
const NON_LITERAL = new Set(["yes", "no", "true", "false", "n/a", "na", "none", "unknown", "null"])

function buildRawList(value?: string, also?: string[]): string[] {
  const v = (value ?? "").trim().toLowerCase()
  const items = NON_LITERAL.has(v) ? [] : rawCandidates(value ?? "")
  for (const term of also ?? []) items.push(...rawCandidates(term))
  return Array.from(new Set(items.filter(Boolean)))
}

const NUMERIC_CAND = /^[\d.,:/%\-()]+$/

// A number must be the WHOLE printed number: "89.9" is not inside "189.9" or
// "89.95", and item "2.14" is not inside clause "3.2.14" or "2.14.1" (a
// separator + digit on either side continues the number). Short text
// candidates ("na") need a word boundary.
function boundaryOk(norm: string, idx: number, cand: string): boolean {
  const end = idx + cand.length
  const before = idx > 0 ? norm[idx - 1] : ""
  const after = end < norm.length ? norm[end] : ""
  if (NUMERIC_CAND.test(cand) && /\d/.test(cand)) {
    if (isDigit(before) || isDigit(after)) return false
    if (/[./]/.test(before) && idx > 1 && isDigit(norm[idx - 2])) return false
    if (/[./]/.test(after) && end + 1 < norm.length && isDigit(norm[end + 1])) return false
    return true
  }
  if (cand.length >= 4) return true
  const alnum = (c: string) => /[a-z0-9؀-ۿ]/i.test(c)
  return !alnum(before) && !alnum(after)
}

// First occurrence of `cand` in `norm` that sits on a proper boundary, else -1.
function findBounded(norm: string, cand: string): number {
  let idx = norm.indexOf(cand)
  while (idx !== -1 && !boundaryOk(norm, idx, cand)) idx = norm.indexOf(cand, idx + 1)
  return idx
}

function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string
  )
}

const PANEL_CLASS =
  "fixed inset-y-0 end-0 z-40 flex w-full max-w-[600px] flex-col border-s border-border bg-card shadow-2xl"

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "tif", "tiff"])

function fileKind(name: string | undefined): "pdf" | "image" | "other" {
  const ext = (name ?? "").split(".").pop()?.toLowerCase() ?? ""
  if (ext === "pdf") return "pdf"
  if (IMAGE_EXTS.has(ext)) return "image"
  return "other"
}

type PdfProxy = {
  numPages: number
  getPage: (n: number) => Promise<{ getTextContent: () => Promise<{ items: Array<{ str?: string }> }> }>
}

// Scan every page's text for the first candidate and return its 1-based page.
async function findCandidatePage(
  pdf: PdfProxy,
  candidates: string[],
  skipPage: number
): Promise<number | null> {
  const terms = candidates.filter((c) => c.length >= 4)
  if (!terms.length) return null
  for (let i = 1; i <= pdf.numPages; i++) {
    if (i === skipPage) continue
    try {
      const page = await pdf.getPage(i)
      const tc = await page.getTextContent()
      const text = normalize(tc.items.map((it) => it.str ?? "").join(" "))
      if (terms.some((c) => findBounded(text, c) !== -1)) return i
    } catch {
      /* skip unreadable page */
    }
  }
  return null
}

export function PdfViewerPanel() {
  const { request, open } = usePdfViewer()
  if (!open || !request) return null
  // Remount per request so per-document state initialises fresh.
  return <DocViewer key={request.nonce} request={request} />
}

function DocViewer({ request }: { request: PdfViewRequest }) {
  switch (fileKind(request.fileName)) {
    case "pdf":
      return <PdfReader request={request} />
    case "image":
      return <ImageReader request={request} />
    default:
      return <FallbackReader request={request} />
  }
}

function ViewerHeader({ request, tools }: { request: PdfViewRequest; tools?: React.ReactNode }) {
  const { t } = useLang()
  const { close } = usePdfViewer()
  return (
    <div className="flex items-center gap-2 border-b border-border px-3 py-2">
      <FileText className="text-muted-foreground size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{request.fileName ?? t("Document", "المستند")}</div>
        {request.fieldName && (
          <div className="text-muted-foreground truncate text-xs">{request.fieldName}</div>
        )}
      </div>
      {tools}
      <a
        href={claimFileUrl(request.claimId, request.index)}
        target="_blank"
        rel="noreferrer"
        className="hover:bg-secondary rounded p-1 transition"
        aria-label={t("Open in new tab", "فتح في علامة تبويب جديدة")}
      >
        <Download className="size-4" />
      </a>
      <button
        type="button"
        onClick={close}
        className="hover:bg-secondary rounded p-1 transition"
        aria-label={t("Close", "إغلاق")}
      >
        <X className="size-4" />
      </button>
    </div>
  )
}

function ZoomControls({ onOut, onIn }: { onOut: () => void; onIn: () => void }) {
  const { t } = useLang()
  return (
    <div className="flex items-center gap-1">
      <button type="button" onClick={onOut} className="hover:bg-secondary rounded p-1 transition" aria-label={t("Zoom out", "تصغير")}>
        <Minus className="size-4" />
      </button>
      <button type="button" onClick={onIn} className="hover:bg-secondary rounded p-1 transition" aria-label={t("Zoom in", "تكبير")}>
        <Plus className="size-4" />
      </button>
    </div>
  )
}

function PdfReader({ request }: { request: PdfViewRequest }) {
  const { t, dir } = useLang()
  const [numPages, setNumPages] = React.useState(0)
  const [pageNum, setPageNum] = React.useState(Math.max(request.page ?? 1, 1))
  const [zoom, setZoom] = React.useState(0.94)
  const [containerW, setContainerW] = React.useState(0)
  const [error, setError] = React.useState<string | null>(null)
  const scrollRef = React.useRef<HTMLDivElement>(null)
  const pdfRef = React.useRef<PdfProxy | null>(null)
  const docSearched = React.useRef(false)

  // Azure CU OCR fallback (polygons drawn over the page) for when the text
  // layer can't be matched — scanned pages or a corrupt Arabic text layer.
  const [cuPolys, setCuPolys] = React.useState<LocatePoint[][] | null>(null)
  const [cuLoading, setCuLoading] = React.useState(false)
  const [pageDims, setPageDims] = React.useState<{ w: number; h: number } | null>(null)
  // The page CU actually found the value on (may differ from the cited page);
  // the overlay only draws while that page is showing.
  const [cuPage, setCuPage] = React.useState<number | null>(null)
  const cuTried = React.useRef(false)

  // Fit-to-width: track the panel's usable width. The scroll container reserves
  // its scrollbar gutter so clientWidth stays constant (no resize feedback loop).
  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const measure = () =>
      setContainerW((prev) => {
        const next = el.clientWidth - 24
        return Math.abs(next - prev) > 1 ? next : prev
      })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const pageWidth = containerW > 0 ? Math.round(containerW * zoom) : undefined

  const url = claimFileUrl(request.claimId, request.index)
  const file = React.useMemo(() => ({ url }), [url])

  // THE value (the number the reviewer clicked) vs its row context (item
  // code, unit price). The value is what gets found, scrolled to and, on a
  // scanned page, OCR-located; the context is only marked alongside it and
  // never decides which page to show — an item code like "2.14" also occurs
  // as clause numbering on unrelated pages.
  const rawList = React.useMemo(() => buildRawList(request.highlight), [request.highlight])
  const alsoRawList = React.useMemo(() => buildRawList(undefined, request.highlightAlso), [request.highlightAlso])
  const anchorList = React.useMemo(
    () => (request.highlightExtra?.trim() ? [request.highlightExtra.trim()] : []),
    [request.highlightExtra]
  )
  const primaryCandidates = React.useMemo(
    () => Array.from(new Set(rawList.map((c) => normalize(c)).filter(Boolean))),
    [rawList]
  )
  const alsoCandidates = React.useMemo(
    () =>
      Array.from(new Set(alsoRawList.map((c) => normalize(c)).filter(Boolean))).filter(
        (c) => !primaryCandidates.includes(c)
      ),
    [alsoRawList, primaryCandidates]
  )
  const candidates = React.useMemo(() => [...primaryCandidates, ...alsoCandidates], [primaryCandidates, alsoCandidates])

  // Wrap the value in a <mark> inside each text-layer item; matching runs on
  // the normalised copy, then maps back so the ORIGINAL printed text (commas,
  // Arabic digits) is what gets highlighted. The value gets the strong mark
  // (.cia-pdf-hit); row context a faint one (.cia-pdf-also).
  const textRenderer = React.useCallback(
    (item: { str: string }) => {
      if (!candidates.length) return escapeHtml(item.str)
      const { norm, map } = normalizeWithMap(item.str)
      for (const cand of candidates) {
        const idx = findBounded(norm, cand)
        if (idx === -1) continue
        const primary = primaryCandidates.includes(cand)
        const start = map[idx]
        const end = (map[idx + cand.length - 1] ?? item.str.length - 1) + 1
        // Text layer glyphs are transparent over the canvas — the mark's text
        // must stay transparent too, leaving only the tinted background.
        const cls = primary ? "cia-pdf-hit" : "cia-pdf-also"
        const bg = primary ? "rgba(250,204,21,0.55)" : "rgba(250,204,21,0.18)"
        return (
          `${escapeHtml(item.str.slice(0, start))}` +
          `<mark class="${cls}" style="color:transparent;background-color:${bg};border-radius:2px;">` +
          `${escapeHtml(item.str.slice(start, end))}</mark>${escapeHtml(item.str.slice(end))}`
        )
      }
      return escapeHtml(item.str)
    },
    [candidates, primaryCandidates]
  )

  // Server OCR-locate: ask CU for the value's polygons, jumping to the page it
  // was actually found on. The anchors (verbatim clause) go along as the weak
  // fallback. One shot per open — repeated failures would just re-bill OCR.
  const runCuFallback = React.useCallback(() => {
    if (cuTried.current || (!rawList.length && !alsoRawList.length && !anchorList.length)) return
    cuTried.current = true
    const target = pageNum
    setCuPage(target)
    setCuLoading(true)
    locateInDocument(request.claimId, request.index, target, rawList, alsoRawList, anchorList)
      .then((r) => {
        if (r.found && r.page && r.page !== target) {
          setCuPage(r.page)
          setPageNum(r.page)
        }
        setCuPolys(r.found ? r.polygons : [])
      })
      .finally(() => setCuLoading(false))
  }, [rawList, alsoRawList, anchorList, pageNum, request.claimId, request.index])

  // After the text layer lays out: scroll the hit into view; else search the
  // rest of the document for the value's page and jump there; else fall back
  // to server OCR-locate (scanned pages have an empty text layer).
  const onTextLayer = React.useCallback(() => {
    const hit = scrollRef.current?.querySelector(".cia-pdf-hit")
    if (hit) {
      hit.scrollIntoView({ block: "center", behavior: "smooth" })
      return
    }
    if (!candidates.length && !anchorList.length) return
    // The value is not in this page's text layer. Search the rest of the
    // document for THE VALUE only (never for the item code — that is how the
    // reader once landed on an unrelated clause), else OCR-locate it here.
    if (primaryCandidates.length && !docSearched.current && pdfRef.current) {
      docSearched.current = true
      findCandidatePage(pdfRef.current, primaryCandidates, pageNum).then((found) => {
        if (found) setPageNum(found) // re-render → textRenderer highlights there
        else runCuFallback()
      })
      return
    }
    // Row context found on the cited page: keep it in view while OCR looks
    // for the value itself (a garbled digit run in the text layer).
    scrollRef.current?.querySelector(".cia-pdf-also")?.scrollIntoView({ block: "center", behavior: "smooth" })
    runCuFallback()
  }, [candidates, anchorList, primaryCandidates, pageNum, runCuFallback])

  // Bring the CU highlight into view once it arrives.
  React.useEffect(() => {
    if (cuPolys && cuPolys.length) {
      scrollRef.current
        ?.querySelector(".cia-cu-hit")
        ?.scrollIntoView({ block: "center", behavior: "smooth" })
    }
  }, [cuPolys])

  const pdfTools = (
    <>
      <div className="text-muted-foreground flex items-center gap-1 text-xs">
        <button
          type="button"
          onClick={() => setPageNum((p) => Math.max(1, p - 1))}
          disabled={pageNum <= 1}
          className="hover:bg-secondary rounded p-1 transition disabled:opacity-40"
          aria-label={t("Previous page", "الصفحة السابقة")}
        >
          <ChevronLeft className="size-4 rtl:rotate-180" />
        </button>
        <span className="tabular-nums">
          {pageNum}
          {numPages ? ` / ${numPages}` : ""}
        </span>
        <button
          type="button"
          onClick={() => setPageNum((p) => (numPages ? Math.min(numPages, p + 1) : p + 1))}
          disabled={numPages > 0 && pageNum >= numPages}
          className="hover:bg-secondary rounded p-1 transition disabled:opacity-40"
          aria-label={t("Next page", "الصفحة التالية")}
        >
          <ChevronRight className="size-4 rtl:rotate-180" />
        </button>
      </div>
      <ZoomControls
        onOut={() => setZoom((z) => Math.max(0.4, +(z - 0.15).toFixed(2)))}
        onIn={() => setZoom((z) => Math.min(3, +(z + 0.15).toFixed(2)))}
      />
    </>
  )

  return (
    <aside dir={dir} className={PANEL_CLASS}>
      <ViewerHeader request={request} tools={pdfTools} />
      <div
        ref={scrollRef}
        className="bg-muted/30 relative min-h-0 flex-1 overflow-auto p-3 [scrollbar-gutter:stable]"
      >
        <Document
          file={file}
          onLoadSuccess={(pdf) => {
            pdfRef.current = pdf as unknown as PdfProxy
            setNumPages(pdf.numPages)
          }}
          onLoadError={(e) => setError(e?.message || "Failed to load document")}
          loading={
            <Centered>
              <Loader2 className="text-muted-foreground size-5 animate-spin" />
            </Centered>
          }
          error={
            <Centered>
              <p className="text-muted-foreground max-w-xs text-center text-sm">
                {error || t("Couldn't load this document.", "تعذّر تحميل هذا المستند.")}
              </p>
            </Centered>
          }
          className="flex justify-center"
        >
          <div className="relative">
            <Page
              pageNumber={pageNum}
              width={pageWidth}
              scale={pageWidth ? undefined : 1}
              renderAnnotationLayer={false}
              customTextRenderer={textRenderer}
              onRenderTextLayerSuccess={onTextLayer}
              onRenderSuccess={(p) => setPageDims({ w: p.width, h: p.height })}
              loading={
                <Centered>
                  <Loader2 className="text-muted-foreground size-5 animate-spin" />
                </Centered>
              }
              className="shadow-sm"
            />
            {pageNum === cuPage && pageDims && cuPolys && cuPolys.length > 0 && (
              <svg
                className="pointer-events-none absolute left-0 top-0"
                width={pageDims.w}
                height={pageDims.h}
              >
                {cuPolys.map((poly, i) => (
                  <polygon
                    key={i}
                    className={i === 0 ? "cia-cu-hit" : undefined}
                    points={poly.map((pt) => `${pt.x * pageDims.w},${pt.y * pageDims.h}`).join(" ")}
                    fill="rgba(250,204,21,0.35)"
                    stroke="rgba(202,138,4,0.85)"
                    strokeWidth={1}
                  />
                ))}
              </svg>
            )}
          </div>
        </Document>
        {cuLoading && (
          <div className="bg-card border-border text-muted-foreground sticky bottom-2 mx-auto flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs shadow-sm">
            <Loader2 className="size-3.5 animate-spin" />
            {t("Locating in document…", "جارٍ تحديد الموضع في المستند…")}
          </div>
        )}
      </div>
    </aside>
  )
}

function ImageReader({ request }: { request: PdfViewRequest }) {
  const { t, dir } = useLang()
  const [scale, setScale] = React.useState(1)
  // Images have no text layer — go straight to the server OCR-locate.
  const [cuPolys, setCuPolys] = React.useState<LocatePoint[][] | null>(null)
  const [cuLoading, setCuLoading] = React.useState(false)
  const cuTried = React.useRef(false)

  React.useEffect(() => {
    if (cuTried.current) return
    const rawList = buildRawList(request.highlight)
    const alsoRawList = buildRawList(undefined, request.highlightAlso)
    const anchors = request.highlightExtra?.trim() ? [request.highlightExtra.trim()] : []
    if (!rawList.length && !alsoRawList.length && !anchors.length) return
    cuTried.current = true
    setCuLoading(true)
    locateInDocument(request.claimId, request.index, 1, rawList, alsoRawList, anchors)
      .then((r) => setCuPolys(r.found ? r.polygons : []))
      .finally(() => setCuLoading(false))
  }, [request])

  return (
    <aside dir={dir} className={PANEL_CLASS}>
      <ViewerHeader
        request={request}
        tools={
          <ZoomControls
            onOut={() => setScale((s) => Math.max(0.25, +(s - 0.25).toFixed(2)))}
            onIn={() => setScale((s) => Math.min(5, +(s + 0.25).toFixed(2)))}
          />
        }
      />
      <div className="bg-muted/30 relative min-h-0 flex-1 overflow-auto p-3">
        <div className="relative mx-auto" style={{ width: `${Math.round(scale * 100)}%` }}>
          <img
            src={claimFileUrl(request.claimId, request.index)}
            alt={request.fileName ?? t("Document", "المستند")}
            className="block h-auto w-full shadow-sm"
          />
          {cuPolys && cuPolys.length > 0 && (
            <svg
              className="pointer-events-none absolute left-0 top-0 h-full w-full"
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
            >
              {cuPolys.map((poly, i) => (
                <polygon
                  key={i}
                  points={poly.map((pt) => `${pt.x},${pt.y}`).join(" ")}
                  fill="rgba(250,204,21,0.35)"
                  stroke="rgba(202,138,4,0.85)"
                  strokeWidth={0.002}
                />
              ))}
            </svg>
          )}
        </div>
        {cuLoading && (
          <div className="bg-card border-border text-muted-foreground sticky bottom-2 mx-auto flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs shadow-sm">
            <Loader2 className="size-3.5 animate-spin" />
            {t("Locating in document…", "جارٍ تحديد الموضع في المستند…")}
          </div>
        )}
      </div>
    </aside>
  )
}

function FallbackReader({ request }: { request: PdfViewRequest }) {
  const { t, dir } = useLang()
  return (
    <aside dir={dir} className={PANEL_CLASS}>
      <ViewerHeader request={request} />
      <div className="grid min-h-0 flex-1 place-items-center p-6 text-center">
        <div>
          <FileText className="text-muted-foreground/70 mx-auto size-10" />
          <p className="mt-3 break-all text-sm font-medium">{request.fileName}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t(
              "Inline preview isn't available for this file type.",
              "المعاينة داخل الصفحة غير متاحة لهذا النوع من الملفات."
            )}
          </p>
          <a
            href={claimFileUrl(request.claimId, request.index)}
            target="_blank"
            rel="noreferrer"
            className="border-border bg-card mt-4 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition hover:shadow-sm"
          >
            <Download className="size-4" />
            {t("Open / download", "فتح / تنزيل")}
          </a>
        </div>
      </div>
    </aside>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="grid min-h-40 place-items-center py-10">{children}</div>
}
