import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useApi } from './api'
import Markdown from './Markdown'

type News = { id: number; source: string; link: string; title: string; summary?: string; published_at: string }
type Market = {
  platform: string
  market_id: string
  url?: string
  question_normalized?: string
  question_raw?: string
  outcomes: { normalized?: string; raw?: string; price?: number }[]
}
type Result<T> = T[] | { error: string } | undefined
type SearchSummary =
  | { text: string; sources: { id: number; kind: 'news' | 'market'; title: string; url: string; source: string }[] }
  | { error: string }
type SearchResponse = {
  interpretation: string
  results: { news?: Result<News>; markets?: Result<Market> }
  summary: SearchSummary | null
}

// Mirrors news.TOPICS keys, the tags the ingest stamps on every article.
const TOPICS = ['oil', 'natural-gas', 'metals', 'agriculture', 'power-grid', 'renewables', 'nuclear', 'coal', 'weather', 'climate',
  'shipping', 'rates', 'inflation', 'stocks', 'crypto', 'geopolitics', 'texas']
const FILTERS = ['sources', 'news_source', 'market_platform', 'tag', 'from', 'to'] as const
const SUGGESTIONS = ['oil supply', 'Texas power', 'shipping disruptions']
// Greek mode keeps to the sea: no oil, no power grid.
const SEA_SUGGESTIONS = ['shipping disruptions', 'port congestion', 'Suez Canal transits']

/** API query string from the page's URL params; dates become a full UTC-day window. */
function searchQuery(params: URLSearchParams) {
  const out = new URLSearchParams()
  const q = params.get('q')
  if (q) out.set('q', q)
  params.getAll('sources').forEach((v) => out.append('sources', v))
  for (const k of ['news_source', 'market_platform', 'tag']) if (params.get(k)) out.set(k, params.get(k)!)
  const from = params.get('from')
  const to = params.get('to')
  if (from || to) {
    out.set('start', `${from || '2000-01-01'}T00:00:00Z`)
    out.set('end', to ? `${to}T23:59:59Z` : new Date().toISOString())
  }
  return out.toString()
}

const rows = <T,>(r: Result<T>) => (Array.isArray(r) ? r : [])
const failed = (r: Result<unknown>) => (r && !Array.isArray(r) ? r.error : null)

export default function Search({ greekMode = false }: { greekMode?: boolean }) {
  const api = useApi()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const query = searchQuery(params)
  const hasFilters = FILTERS.some((k) => params.has(k))
  const [draft, setDraft] = useState(q)
  const [data, setData] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setDraft(q)
    if (!q && !hasFilters) return
    let live = true
    setLoading(true)
    setError(null)
    setData(null)
    api<SearchResponse>(`/search?${query}`)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [q, query, hasFilters, api])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const next = new URLSearchParams(params)
    if (draft.trim()) next.set('q', draft.trim())
    else next.delete('q')
    setParams(next)
  }
  const chooseSuggestion = (suggestion: string) => {
    setDraft(suggestion)
    const next = new URLSearchParams(params)
    next.set('q', suggestion)
    setParams(next)
  }
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  const sources = params.getAll('sources')
  const toggleSource = (source: string) => {
    const next = new URLSearchParams(params)
    next.delete('sources')
    const on = sources.includes(source) ? sources.filter((s) => s !== source) : [...sources, source]
    on.forEach((s) => next.append('sources', s))
    setParams(next)
  }

  const news = rows(data?.results.news)
  const markets = rows(data?.results.markets)
  const showResults = Boolean(data && (q || hasFilters))

  return (
    <div className="search">
      <div className="search-inner">
        <header className="search-header">
          {greekMode && <p className="greek-overline" lang="grc">ΜΑΝΤΕΙΟΝ · ΔΕΛΦΟΙ</p>}
          <h1>{greekMode ? 'Consult the oracle' : 'Market and news search'}</h1>
          {greekMode && <p className="oracle-motto"><span lang="grc">γνῶθι σεαυτόν</span> · “know thyself”, carved over the temple door at Delphi</p>}
        </header>

        <form className="search-form" onSubmit={submit} role="search">
          <label htmlFor="q">{greekMode ? 'Ask the Pythia about shipping and the sea' : 'Search markets and news'}</label>
          <div className="search-form-row">
            <input
              id="q"
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Enter search terms"
              maxLength={500}
              autoComplete="off"
            />
            <button type="submit" className="search-submit">{greekMode ? 'Ask the oracle' : 'Find results'}</button>
          </div>
        </form>

        <div className="search-suggestions" role="group" aria-label="Suggested searches">
          <span>Try:</span>
          {(greekMode ? SEA_SUGGESTIONS : SUGGESTIONS).map((suggestion) => (
            <button key={suggestion} type="button" onClick={() => chooseSuggestion(suggestion)}>{suggestion}</button>
          ))}
        </div>

        <details className="search-advanced" open={hasFilters}>
          <summary>Filters</summary>
          <fieldset className="search-filters">
            <legend className="sr-only">Search filters</legend>
            <div className="search-filter search-filter-sources" role="group" aria-label="Sources">
              <span className="search-filter-label">Sources</span>
              <label><input type="checkbox" checked={sources.includes('news')} onChange={() => toggleSource('news')} /> News</label>
              <label><input type="checkbox" checked={sources.includes('markets')} onChange={() => toggleSource('markets')} /> Markets</label>
            </div>
            <label className="search-filter">
              <span className="search-filter-label">Topic</span>
              <select aria-label="Topic" value={params.get('tag') ?? ''} onChange={(e) => setFilter('tag', e.target.value)}>
                <option value="">Any topic</option>
                {TOPICS.map((t) => <option key={t} value={t}>{t.replace('-', ' ')}</option>)}
              </select>
            </label>
            <label className="search-filter">
              <span className="search-filter-label">Outlet</span>
              <select aria-label="Outlet" value={params.get('news_source') ?? ''} onChange={(e) => setFilter('news_source', e.target.value)}>
                <option value="">Any outlet</option>
                <option value="wsj">WSJ</option>
                <option value="ft">FT</option>
              </select>
            </label>
            <label className="search-filter">
              <span className="search-filter-label">Platform</span>
              <select aria-label="Platform" value={params.get('market_platform') ?? ''} onChange={(e) => setFilter('market_platform', e.target.value)}>
                <option value="">Any platform</option>
                <option value="kalshi">Kalshi</option>
                <option value="polymarket">Polymarket</option>
              </select>
            </label>
            <label className="search-filter">
              <span className="search-filter-label">From date</span>
              <input aria-label="From date" type="date" value={params.get('from') ?? ''} max={params.get('to') ?? undefined} onChange={(e) => setFilter('from', e.target.value)} />
            </label>
            <label className="search-filter">
              <span className="search-filter-label">To date</span>
              <input aria-label="To date" type="date" value={params.get('to') ?? ''} min={params.get('from') ?? undefined} onChange={(e) => setFilter('to', e.target.value)} />
            </label>
            {hasFilters && (
              <button type="button" className="search-clear" onClick={() => setParams(q ? { q } : {})}>Clear filters</button>
            )}
          </fieldset>
        </details>

        {loading && <p className="search-state" role="status">Searching…</p>}
        {error && <p className="search-error" role="alert">{error}</p>}

        {showResults && !loading && data && (
          <section className="search-results" aria-label="Search results">
            <h2>{q ? <>Results for “{q}”</> : 'Results'}</h2>
            {data.summary && ('error' in data.summary
              ? <p className="search-summary-error" role="status">{data.summary.error}</p>
              : <section className="search-ai-summary" aria-label="AI summary">
                  <h3>AI summary</h3>
                  <Markdown text={data.summary.text} />
                  <h4>Sources</h4>
                  <ul>
                    {data.summary.sources.map((source) => (
                      <li key={`${source.kind}-${source.id}`}>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          <span>{source.source}</span>: {source.title}
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
            )}
            <div className="search-result-sections">
              {'markets' in data.results && <section className="search-result-section">
                <h3>Markets</h3>
                {failed(data.results.markets) && <p className="search-error">{failed(data.results.markets)}</p>}
                {!markets.length && !failed(data.results.markets) && <p className="search-empty">No matching markets.</p>}
                <ul>
                  {markets.map((m) => (
                    <li key={`${m.platform}-${m.market_id}`}>
                      <a href={m.url} target="_blank" rel="noreferrer">{m.question_normalized ?? m.question_raw}</a>
                      <div className="search-result-meta">
                        <span>{m.platform}</span>
                        {m.outcomes.map((o, i) => (
                          <span key={i}>{o.normalized ?? o.raw}{o.price != null && ` ${Math.round(o.price * 100)}%`}</span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>}
              {'news' in data.results && <section className="search-result-section">
                <h3>News</h3>
                {failed(data.results.news) && <p className="search-error">{failed(data.results.news)}</p>}
                {!news.length && !failed(data.results.news) && <p className="search-empty">No matching articles.</p>}
                <ul>
                  {news.map((n) => (
                    <li key={n.id}>
                      <a href={n.link} target="_blank" rel="noreferrer">{n.title}</a>
                      {n.summary && <p className="search-summary">{n.summary}</p>}
                      <div className="search-result-meta">
                        <span>{n.source}</span>
                        <time dateTime={n.published_at}>{new Date(n.published_at).toLocaleString()}</time>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
