import type { ReactNode } from 'react'

// Minimal, safe Markdown for the report's `reasoning` text. Handles paragraphs,
// bullet/numbered lists, **bold**, and `code`. Everything is emitted as React
// children (auto-escaped) using a fixed set of tags — there is no HTML-injection
// surface (no dangerouslySetInnerHTML, no raw-HTML passthrough).

const BULLET = /^\s*[-*]\s+/
const NUMBERED = /^\s*\d+\.\s+/
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let key = 0
  let m: RegExpExecArray | null
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('**')) out.push(<strong key={key++}>{tok.slice(2, -2)}</strong>)
    else out.push(<code key={key++}>{tok.slice(1, -1)}</code>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0
  while (i < lines.length) {
    if (!lines[i].trim()) { i++; continue }

    if (BULLET.test(lines[i]) || NUMBERED.test(lines[i])) {
      const ordered = NUMBERED.test(lines[i])
      const strip = ordered ? NUMBERED : BULLET
      const items: ReactNode[] = []
      while (i < lines.length && strip.test(lines[i])) {
        items.push(<li key={key++}>{renderInline(lines[i].replace(strip, ''))}</li>)
        i++
      }
      blocks.push(ordered ? <ol key={key++}>{items}</ol> : <ul key={key++}>{items}</ul>)
      continue
    }

    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) {
      para.push(lines[i])
      i++
    }
    blocks.push(<p key={key++}>{renderInline(para.join(' '))}</p>)
  }

  return <div className={className}>{blocks}</div>
}
