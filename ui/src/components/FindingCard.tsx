import { FileSearch } from "lucide-react"

import { StatusPill } from "@/components/StatusPill"
import { usePdfViewer } from "@/components/PdfViewerContext"
import { useLang } from "@/lib/i18n"
import type { Claim, Finding } from "@/types/domain"

/** One rule result with its normative source — the audit-trail unit.
 *
 * Evidence renders as labelled rows instead of raw JSON; every value that
 * lives in a staged document gets a locate button that opens the embedded
 * reader and highlights the value on the page it was read from.
 */

type Leaf = { path: string[]; value: string | number | boolean | null; siblings: Record<string, unknown> }

function flatten(value: unknown, path: string[], siblings: Record<string, unknown>, out: Leaf[]): void {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    out.push({ path, value: value as Leaf["value"], siblings })
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => flatten(item, [...path, String(i)], siblings, out))
    return
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>
    for (const [k, v] of Object.entries(obj)) flatten(v, [...path, k], obj, out)
  }
}

function flattenEvidence(evidence: Record<string, unknown>): Leaf[] {
  const out: Leaf[] = []
  flatten(evidence, [], evidence, out)
  return out
}

/** Which staged document a leaf's value should be located in, if any. */
function targetDocType(gate: string, path: string[]): string | null {
  const p = path.join(".").toLowerCase()
  if (p.includes("boq")) return "contract_boq"
  if (p.includes("invoice") || p.includes("qr")) return "invoice"
  if (p.includes("coc")) return "coc"
  // Values typed into the claim form / computed by the check — nothing to open.
  if (/(^|\.)(claim|expected|actual|missing|problems|notes|cumulative|payment_no|penalties)/.test(p)) return null
  const defaults: Record<string, string> = { intake: "invoice", coc_consistency: "coc" }
  return defaults[gate] ?? null
}

const HIDDEN_LEAVES = /(^|\.)(bidi_normalized|has_zatca_stamp|status)$/

export function FindingCard({ finding, claim }: { finding: Finding; claim?: Claim | null }) {
  const { t, pick } = useLang()
  const { openDocument } = usePdfViewer()

  const leaves = flattenEvidence(finding.evidence).filter(
    (l) => !HIDDEN_LEAVES.test(l.path.join("."))
  )

  const locate = (leaf: Leaf) => {
    if (!claim) return
    const docType = targetDocType(finding.gate, leaf.path)
    const index = claim.source_files.findIndex((f) => f.doc_type === docType)
    if (index === -1) return
    const file = claim.source_files[index]
    // The row's item/identifier sibling is marked too, so a unit price lands
    // next to its BoQ line rather than a bare number somewhere on the page.
    const also = ["item", "item_code", "invoice_no"]
      .map((k) => leaf.siblings[k])
      .filter((v): v is string | number => typeof v === "string" || typeof v === "number")
      .map(String)
    openDocument({
      claimId: claim.id,
      index,
      fileName: file.path.split("/").pop(),
      highlight: String(leaf.value),
      highlightAlso: also,
      fieldName: `${pick(finding.title_en, finding.title_ar)} — ${leaf.path.join(" › ")}`,
    })
  }

  const canLocate = (leaf: Leaf): boolean => {
    if (!claim || leaf.value === null || typeof leaf.value === "boolean") return false
    if (String(leaf.value).trim() === "") return false
    const docType = targetDocType(finding.gate, leaf.path)
    return docType !== null && claim.source_files.some((f) => f.doc_type === docType)
  }

  const display = (v: Leaf["value"]): string => {
    if (v === null) return "—"
    if (typeof v === "boolean") return v ? t("yes", "نعم") : t("no", "لا")
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { minimumFractionDigits: 2 })
    return String(v)
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{pick(finding.title_en, finding.title_ar)}</div>
          <p className="text-muted-foreground mt-1 text-sm">{pick(finding.detail_en, finding.detail_ar)}</p>
        </div>
        <StatusPill status={finding.severity} className="shrink-0" />
      </div>
      <div className="text-muted-foreground mt-2 text-xs">
        {t("Source", "المصدر")}: {finding.source.doc}
        {finding.source.ref ? ` — ${finding.source.ref}` : ""}
      </div>
      {leaves.length > 0 && (
        <details className="mt-1.5">
          <summary className="text-muted-foreground cursor-pointer select-none text-xs hover:text-foreground">
            {t("Evidence — values compared", "الأدلة — القيم المقارنة")}
          </summary>
          <div className="border-border/70 mt-1.5 overflow-hidden rounded-md border">
            {leaves.map((leaf, i) => (
              <div
                key={i}
                className="border-border/70 bg-muted/40 flex items-center gap-2 border-b px-2.5 py-1.5 text-xs last:border-0"
              >
                <span className="text-muted-foreground min-w-0 flex-1 truncate">
                  {leaf.path.join(" › ")}
                </span>
                <span className="max-w-[45%] truncate font-medium tabular-nums" dir="auto">
                  {display(leaf.value)}
                </span>
                {canLocate(leaf) && (
                  <button
                    type="button"
                    onClick={() => locate(leaf)}
                    className="text-primary hover:bg-secondary shrink-0 rounded p-1 transition"
                    aria-label={t("Locate in document", "تحديد الموضع في المستند")}
                    title={t("Locate in document", "تحديد الموضع في المستند")}
                  >
                    <FileSearch className="size-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
