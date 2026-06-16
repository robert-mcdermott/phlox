import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { Check, Copy } from 'lucide-react'
import Mermaid from './Mermaid'

function CodeBlock({ className, children }) {
  const [copied, setCopied] = useState(false)
  const text = String(children)
  const copy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="relative group my-3">
      <button
        onClick={copy}
        className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition rounded bg-black/30 p-1.5 text-gray-200 hover:bg-black/50"
        title="Copy code"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
      <pre className="overflow-x-auto rounded-lg bg-[#0d1117] p-4 text-sm">
        <code className={`${className || ''} !text-[#e6edf3]`}>{children}</code>
      </pre>
    </div>
  )
}

export default function Markdown({ children }) {
  return (
    <div className="prose prose-sm max-w-none break-words
      prose-headings:text-content prose-p:text-content prose-li:text-content
      prose-strong:text-content prose-a:text-accent
      prose-code:text-content prose-code:before:content-[''] prose-code:after:content-['']
      prose-table:text-content prose-th:text-content prose-td:border-border">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeHighlight, rehypeKatex]}
        components={{
          // react-markdown v9 dropped the `inline` prop; detect block code by the
          // `language-*` class fenced blocks get. Everything else is inline `<code>`.
          pre: ({ children }) => children,
          code: ({ className, children }) => {
            if (/language-mermaid/.test(className || '')) {
              return <Mermaid code={String(children).trim()} />
            }
            return /language-/.test(className || '') ? (
              <CodeBlock className={className}>{children}</CodeBlock>
            ) : (
              <code className="rounded bg-surface-3 px-1.5 py-0.5 text-[0.85em]">{children}</code>
            )
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
              {children}
            </a>
          ),
        }}
      >
        {children || ''}
      </ReactMarkdown>
    </div>
  )
}
