// Transform text nodes only: code, existing links, images and HTML keep their meaning.
export function remarkCitations() {
  return (tree) => {
    const visit = (node) => {
      if (['code', 'inlineCode', 'link', 'linkReference', 'image', 'html'].includes(node.type)) return
      if (!node.children) return
      node.children = node.children.flatMap((child) => {
        if (child.type !== 'text') { visit(child); return [child] }
        const nodes = [], pattern = /\[(S[1-9][0-9]{0,5})\]/g
        let start = 0, match
        while ((match = pattern.exec(child.value))) {
          if (match.index > start) nodes.push({ type: 'text', value: child.value.slice(start, match.index) })
          nodes.push({ type: 'link', url: `#phlox-source-${match[1]}`, children: [{ type: 'text', value: match[1] }] })
          start = pattern.lastIndex
        }
        if (!nodes.length) return [child]
        if (start < child.value.length) nodes.push({ type: 'text', value: child.value.slice(start) })
        return nodes
      })
    }
    visit(tree)
  }
}
