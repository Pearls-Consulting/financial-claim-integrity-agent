import type { Claim, ContractExtract, DetectedAttachment, InvoiceDoc, RunResult, Stage } from "@/types/domain"

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  stages: () => get<Stage[]>("/api/stages"),
  claims: () => get<Claim[]>("/api/claims"),
  claim: (id: string) => get<Claim>(`/api/claims/${id}`),
  latestRun: (id: string) => get<RunResult | null>(`/api/claims/${id}/run`),
  /** Run every gate, or a cumulative subset for the guided step-by-step flow. */
  run: async (id: string, gates?: string[]): Promise<RunResult> => {
    const qs = gates?.length ? `?gates=${gates.join(",")}` : ""
    const res = await fetch(`/api/claims/${id}/run${qs}`, { method: "POST" })
    if (!res.ok) throw new Error(`run ${id}: ${res.status}`)
    return res.json() as Promise<RunResult>
  },
  submit: async (form: FormData): Promise<Claim> => {
    const res = await fetch("/api/submissions", { method: "POST", body: form })
    if (!res.ok) throw new Error(`submit: ${res.status} ${await res.text()}`)
    return res.json() as Promise<Claim>
  },
  /** Persist the wizard position so a closed tab resumes where it left off. */
  setProgress: async (id: string, step: number): Promise<void> => {
    const fd = new FormData()
    fd.append("step", String(step))
    await fetch(`/api/claims/${id}/progress`, { method: "POST", body: fd })
  },
  /** OCR + structure one invoice for form autofill; null = no reader (mock engine). */
  extractInvoice: async (file: File): Promise<InvoiceDoc | null> => {
    const fd = new FormData()
    fd.append("invoice", file)
    const res = await fetch("/api/extract/invoice", { method: "POST", body: fd })
    if (!res.ok) throw new Error(`extract: ${res.status} ${await res.text()}`)
    return res.json() as Promise<InvoiceDoc | null>
  },
  /** OCR + structure one contract/BoQ for the step-2 suggestions (contract
   *  value, end date); null = no reader (mock engine). */
  extractBoq: async (file: File): Promise<ContractExtract | null> => {
    const fd = new FormData()
    fd.append("contract_boq", file)
    const res = await fetch("/api/extract/boq", { method: "POST", body: fd })
    if (!res.ok) throw new Error(`extract: ${res.status} ${await res.text()}`)
    return res.json() as Promise<ContractExtract | null>
  },
  /** Identify uploaded vendor-file documents (CR, zakat, award letter, ...)
   *  and lift their identity fields. Heuristic-only under the mock engine. */
  extractAttachments: async (files: File[]): Promise<DetectedAttachment[]> => {
    const fd = new FormData()
    for (const f of files) fd.append("files", f)
    const res = await fetch("/api/extract/attachments", { method: "POST", body: fd })
    if (!res.ok) throw new Error(`extract: ${res.status} ${await res.text()}`)
    return res.json() as Promise<DetectedAttachment[]>
  },
  /** Attach later-step documents / ERP context to an existing submission. */
  update: async (id: string, form: FormData): Promise<Claim> => {
    const res = await fetch(`/api/submissions/${id}`, { method: "POST", body: form })
    if (!res.ok) throw new Error(`update ${id}: ${res.status} ${await res.text()}`)
    return res.json() as Promise<Claim>
  },
}

/** Inline URL for one of a claim's staged source files (PDF viewer / images). */
export function claimFileUrl(claimId: string, index: number): string {
  return `/api/claims/${claimId}/files/${index}`
}

/** One point of an OCR-located polygon, in page fractions (0..1). */
export interface LocatePoint {
  x: number
  y: number
}

export interface LocateResult {
  found: boolean
  polygons: LocatePoint[][]
  page?: number | null // where the value was ACTUALLY found
}

/** OCR-locate a value on a staged document page — the viewer's fallback for
 *  scanned pages with no text layer. Degrades to found=false on any error. */
export async function locateInDocument(
  claimId: string,
  index: number,
  page: number,
  values: string[],
  anchors: string[] = []
): Promise<LocateResult> {
  try {
    const res = await fetch(`/api/claims/${claimId}/files/${index}/locate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page, values, anchors }),
    })
    if (!res.ok) return { found: false, polygons: [] }
    return (await res.json()) as LocateResult
  } catch {
    return { found: false, polygons: [] }
  }
}

/** Zip of the documents that took part in the matching (invoice, contract/BoQ,
 *  acceptance document) — a plain link so the browser handles the download. */
export function exportUrl(claimId: string): string {
  return `/api/claims/${claimId}/export`
}
