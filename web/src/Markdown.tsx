import type { ReactNode } from 'react'

// shortcut: covers what Gemini emits (headings, lists, bold/italic/code, links); swap for react-markdown if tables or nesting are needed.
// Builds React elements, never HTML strings, so model output cannot inject markup.

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(INLINE)) {
    const t = m[0]
    if (m.index > last) out.push(text.slice(last, m.index))
    const k = m.index
    if (t.startsWith('**') || t.startsWith('__')) out.push(<strong key={k}>{inline(t.slice(2, -2))}</strong>)
    else if (t.startsWith('`')) out.push(<code key={k}>{t.slice(1, -1)}</code>)
    else if (t.startsWith('[')) out.push(<a key={k} href={m[2]} target="_blank" rel="noreferrer">{t.slice(1, t.indexOf(']'))}</a>)
    else out.push(<em key={k}>{inline(t.slice(1, -1))}</em>)
    last = k + t.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  const flush = () => {
    if (!list) return
    const items = list.items.map((it, i) => <li key={i}>{inline(it)}</li>)
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>)
    list = null
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    const bullet = line.match(/^[-*•]\s+(.*)/)
    const numbered = line.match(/^\d+[.)]\s+(.*)/)
    if (bullet || numbered) {
      const ordered = !!numbered
      if (list && list.ordered !== ordered) flush()
      list ??= { ordered, items: [] }
      list.items.push((bullet ?? numbered)![1])
      continue
    }
    flush()
    if (!line) continue
    const heading = line.match(/^#{1,6}\s+(.*)/)
    blocks.push(heading ? <h4 key={blocks.length}>{inline(heading[1])}</h4> : <p key={blocks.length}>{inline(line)}</p>)
  }
  flush()
  return <div className="md">{blocks}</div>
}

export function Inline({ text }: { text: string }) {
  return <>{inline(text)}</>
}
