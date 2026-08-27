import type { Claim } from "@/types/domain"

/** Index into claim.source_files of the document an extracted value came
 *  from: the file it names (`source_file`, set by the reader — a claim may
 *  hold the contract, the BoQ and appendices as separate files), else the
 *  first file of the given doc types. -1 when nothing fits. */
export function docIndexFor(
  claim: Claim | null | undefined,
  sourceFile: string | undefined,
  docTypes: string[]
): number {
  if (!claim) return -1
  if (sourceFile) {
    const byName = claim.source_files.findIndex((f) => f.path.split("/").pop() === sourceFile)
    if (byName !== -1) return byName
  }
  return claim.source_files.findIndex((f) => docTypes.includes(f.doc_type))
}
