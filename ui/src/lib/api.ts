import type { Claim, InvoiceDoc, RunResult, Stage } from "@/types/domain"

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
