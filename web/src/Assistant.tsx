import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useApi } from './api'
import Markdown from './Markdown'

type Msg = { role: 'user' | 'model'; text: string; tools?: string[] }

const STARTERS = [
  'Is the Treasury curve inverted anywhere?',
  'Which US grid regions are running hottest versus forecast?',
  'Any UK filings mentioning sanctions this month?',
]

const VIEW_STARTERS = ['Summarize what I’m looking at', 'What’s driving this right now?', 'Any related markets or news?']

// Greek mode: the Pythia answers, and only about the sea.
const SEA_STARTERS = [
  'How many vessels is the AIS feed tracking right now?',
  'When is the next high tide in New York Harbor?',
  'Any news on shipping disruptions this week?',
]

/** Gemini chat backed by the in-process MCP tools; `context` describes the open view so "this" resolves. */
export default function Assistant({ topic, context, greekMode = false }: { topic?: string; context?: string; greekMode?: boolean }) {
  const api = useApi()
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [msgs, busy])

  const send = async (text: string) => {
    const q = text.trim()
    if (!q || busy) return
    const next = [...msgs, { role: 'user' as const, text: q }]
    setMsgs(next)
    setDraft('')
    setBusy(true)
    try {
      const r = await api<{ reply: string; tools_used: string[] }>('/assistant', {
        method: 'POST',
        body: JSON.stringify({ messages: next.slice(-20).map(({ role, text }) => ({ role, text })), context }),
      })
      setMsgs([...next, { role: 'model', text: r.reply, tools: r.tools_used }])
    } catch (e) {
      setMsgs([...next, { role: 'model', text: `Sorry, that failed: ${(e as Error).message}` }])
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    send(draft)
  }

  if (!open)
    return (
      <button className="assistant-open button" onClick={() => setOpen(true)}>
        {greekMode
          ? topic ? `Ask the oracle about ${topic}` : 'Consult the oracle'
          : topic ? `Ask about ${topic}` : 'Ask Nostos'}
      </button>
    )

  return (
    <section className="assistant" aria-label={greekMode ? 'The oracle' : 'Nostos assistant'}>
      <div className="detail-head">
        <p className="kicker">
          {greekMode
            ? topic ? `The Pythia, on ${topic}` : 'The Pythia of Delphi / Gemini + MCP tools'
            : topic ? `Asking about ${topic}` : 'Assistant / Gemini + MCP tools'}
        </p>
        <button className="close" aria-label="Close assistant" onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="assistant-log" aria-live="polite">
        {!msgs.length && (
          <ul className="starters">
            {(context ? VIEW_STARTERS : greekMode ? SEA_STARTERS : STARTERS).map((s) => <li key={s}><button className="link" onClick={() => send(s)}>{s}</button></li>)}
          </ul>
        )}
        {msgs.map((m, k) => (
          <div key={k} className={`msg ${m.role}`}>
            {m.role === 'model' ? <Markdown text={m.text} /> : <p>{m.text}</p>}
            {!!m.tools?.length && <p className="micro">Used {[...new Set(m.tools)].join(', ')}</p>}
          </div>
        ))}
        {busy && <p className="muted shimmer">{greekMode ? 'The Pythia breathes the vapours…' : 'Looking it up…'}</p>}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="assistant-form">
        <label htmlFor="ask" className="sr-only">Ask a question</label>
        <input id="ask" value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder={topic ? `Ask about ${topic}…` : greekMode ? 'Ask about ships, ports, tides, wind and waves…' : 'Ask about markets, yields, filings…'}
          maxLength={4000} disabled={busy} />
      </form>
    </section>
  )
}
