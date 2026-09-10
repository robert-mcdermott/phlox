// A file can be reported by many tool calls. The answer lists its latest descriptor
// once; tool cards still retain the individual operations. Full paths keep same-name
// files in different folders distinct. Saved URLs/indices are preserved unchanged.
export function uniqueArtifacts(artifacts = []) {
  const latest = new Map()
  for (const [index, artifact] of (artifacts || []).entries()) {
    latest.set(artifact.path ? `path:${artifact.path}` : `unkeyed:${index}`, artifact)
  }
  return [...latest.values()]
}
