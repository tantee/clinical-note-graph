/**
 * Extract the set of [N] indices the LLM referenced in the answer markdown.
 * Used by RagPanel to compute which citations to render in the footer.
 */
export function parseCitedIndices(markdown) {
  const out = new Set()
  if (!markdown) return out
  for (const m of markdown.matchAll(/\[(\d+)\]/g)) out.add(Number(m[1]))
  return out
}
