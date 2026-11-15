import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useApi } from './api'
import Markdown, { Inline } from './Markdown'
import { cityName, f1, madeAt, type Tomorrow } from './wx'
import { CommodityPriceChart, EtfTrends, YieldCurve, type CurvePoint, type Fund } from './Charts'
import FocusPanel, { Card } from './FocusPanel'
import { COMMODITIES, globalWeatherCountryCode, regionalCommodityBenchmarks, toWeatherEvent, type Commodity, type Contract, type GlobalWeather } from './events'

type Failed = { error: string }
type News = { id: number; source: string; link: string; title: string; published_at: string }
type Market = { platform: string; market_id: string; url?: string; question_raw: string; status?: string; outcomes: { raw?: string; price?: number }[] }
type CommodityMarket = { question_raw: string; url?: string; status?: string; close_time?: string | null; outcomes?: { raw?: string; price?: number }[] }
type Filing = { company: string; form: string; filed: string; description?: string; location?: string; url: string }
type Filings = { supported: boolean; total?: number; filings: Filing[] }
type Curve = { as_of: string; curve: CurvePoint[]; month_ago: { as_of: string; curve: CurvePoint[] }; source: string }
type Rundown = {
  country: string
  news: News[] | Failed
  markets: Market[] | Failed
  filings: Filings | Failed
  treasury_curve?: Curve | Failed
  treasury_etfs?: { funds: Fund[] } | Failed
  bonds?: Bond[] | Failed
}
type Bond = { symbol: string; name: string; tenor: string; yield: number; change_1m_bp: number | null }
type Editorial = { headline: string; summary: string; watch: string[] }

const failed = (x: unknown): x is Failed => !!x && typeof x === 'object' && 'error' in x
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })

function useFetch<T>(path: string | null) {
  const api = useApi()
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: !!path })
  useEffect(() => {
    if (!path) {
      setState({ loading: false })
      return
    }
    let live = true
    setState({ loading: true })
    api<T>(path)
      .then((data) => live && setState({ data, loading: false }))
      .catch((e: Error) => live && setState({ error: e.message, loading: false }))
    return () => {
      live = false
    }
  }, [api, path])
  return state
}

function FilingList({ country, initial }: { country: string; initial: Filings }) {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState<string | null>(null)
  const searched = useFetch<Filings>(q ? `/countries/${encodeURIComponent(country)}/filings?q=${encodeURIComponent(q)}` : null)
  const data = q ? searched.data : initial
  const submit = (e: FormEvent) => {
    e.preventDefault()
    setQ(draft.trim() || null)
  }
  if (!initial.supported) return <p className="muted">EDGAR has no location code for {country}.</p>
  return (
    <>
      <form onSubmit={submit} className="filing-search" role="search">
        <label htmlFor="fq" className="sr-only">Search filings</label>
        <input id="fq" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Search filings: sanctions, bankruptcy…" maxLength={100} />
      </form>
      {searched.loading && <p className="muted">Searching EDGAR…</p>}
      {searched.error && <p className="error">{searched.error}</p>}
      {data && !data.filings.length && <p className="muted">No filings in the last 30 days{q ? ` mentioning “${q}”` : ''}.</p>}
      <ul className="rows">
        {data?.filings.map((f) => (
          <li key={f.url}>
            <a href={f.url} target="_blank" rel="noreferrer">{f.company}</a>
            <p className="micro">{f.form} / {day(f.filed)}{f.description && f.description !== `FORM ${f.form}` ? ` / ${f.description}` : ''}</p>
          </li>
        ))}
      </ul>
    </>
  )
}

function GovernmentAnnouncements({ country }: { country: string }) {
  const api = useApi()
  const socket = useRef<WebSocket | null>(null)
  const attempt = useRef(0)
  const [state, setState] = useState<{ status: 'idle' | 'pending' | 'done' | 'failed'; answer?: string; error?: string }>({ status: 'idle' })

  useEffect(() => () => {
    attempt.current += 1
    socket.current?.close()
  }, [])

  const checkAnnouncements = useCallback(async () => {
    const current = ++attempt.current
    socket.current?.close()
    socket.current = null
    setState({ status: 'pending' })
    const question = `Search X for official announcements from the government of ${country}, a Natural Earth country, from the last 7 days. Return up to 5 newest first. Include the date, official account name and handle, a concise factual summary, and the direct X post URL for each. Only use verified official government, ministry, agency, head-of-state, or central-bank accounts. Exclude news, commentary, and unofficial accounts. Plainly say when no announcements can be verified.`

    const fail = (message = 'Official announcements couldn’t be retrieved. Please try again.') => {
      if (current !== attempt.current) return
      setState({ status: 'failed', error: message })
      socket.current?.close()
      socket.current = null
    }

    try {
      const response = await api<{ request_id: string; status: 'pending' }>('/ask', {
        method: 'POST',
        body: JSON.stringify({ question }),
      })
      if (current !== attempt.current) return
      if (!response?.request_id) {
        fail()
        return
      }

      const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${wsProtocol}://${location.host}/ws/ask/${response.request_id}`)
      socket.current = ws
      let finished = false
      const finishWithError = () => {
        if (finished || current !== attempt.current) return
        finished = true
        fail()
      }
      ws.onmessage = (event) => {
        if (finished || current !== attempt.current) return
        try {
          const result = JSON.parse(event.data)
          if (!result || result.request_id !== response.request_id || !['done', 'failed'].includes(result.status)) {
            finishWithError()
            return
          }
          finished = true
          if (result.status === 'done') {
            setState({ status: 'done', answer: typeof result.answer === 'string' ? result.answer : '' })
          } else {
            const publicError = typeof result.error === 'string' ? result.error.trim().slice(0, 240) : ''
            setState({ status: 'failed', error: publicError || 'No official announcements could be retrieved. Please try again.' })
          }
          ws.close()
          if (socket.current === ws) socket.current = null
        } catch {
          finishWithError()
        }
      }
      ws.onerror = finishWithError
      ws.onclose = finishWithError
    } catch {
      fail()
    }
  }, [api, country])

  useEffect(() => {
    void checkAnnouncements()
  }, [checkAnnouncements])

  return (
    <Card title="Government announcements">
      <button className="link announcement-action" type="button" onClick={checkAnnouncements} disabled={state.status === 'pending'}>
        {state.status === 'pending' ? 'Checking official X…' : 'Check official X'}
      </button>
      <p className="micro">Source: Official accounts on X / Live via Grok</p>
      <div aria-live="polite">
        {state.status === 'pending' && <p className="muted">Searching recent official posts…</p>}
        {state.status === 'failed' && <p className="error">{state.error}</p>}
        {state.status === 'done' && (
          state.answer?.trim()
            ? state.answer.trim().split(/\n+/).map((paragraph, index) => <p key={index}>{paragraph}</p>)
            : <p className="muted">No answer was returned. Please try again.</p>
        )}
      </div>
    </Card>
  )
}

function WeatherCard({ country }: { country: string }) {
  // US: our own model's forecast for its 20 cities; GFS city points when it isn't live (or for every other country).
  const live = useFetch<Tomorrow[]>(country === 'United States of America' ? '/wx/forecast/tomorrow' : null)
  if (live.loading) return <Card title="Weather"><p className="muted" aria-live="polite">Loading forecast…</p></Card>
  if (live.data?.length) return (
    <Card title="Tomorrow's high · Nostos">
      <ul className="rows">
        {live.data.map((r) => (
          <li key={r.location}>
            <strong>{cityName(r.location)}</strong> <span>{f1(r.nostos_f)}°F</span>
            <p className="micro">NOAA blend {f1(r.nbm_f)}°F · corrected {f1(r.nbm_corrected_f)}°F</p>
          </li>
        ))}
      </ul>
      <p className="micro">Nostos model · forecast made {madeAt(live.data[0].issued_at)} · scored against NWS highs tomorrow</p>
    </Card>
  )
  return <GfsWeatherCard country={country} />
}

function GfsWeatherCard({ country }: { country: string }) {
  const code = globalWeatherCountryCode(country)
  const weather = useFetch<GlobalWeather[]>(code ? `/weather/global?country=${code}` : null)
  const events = Array.isArray(weather.data)
    ? weather.data.flatMap((row) => {
      if (!row || typeof row !== 'object' || typeof row.point_id !== 'string' || !row.point_id || typeof row.name !== 'string' || !row.name.trim()) return []
      const event = toWeatherEvent(row)
      return event ? [{ row, event }] : []
    })
    : []

  return (
    <Card title="Weather">
      {!code ? <p className="muted">No global-city coverage for this country.</p>
        : weather.loading ? <p className="muted" aria-live="polite">Loading forecast…</p>
          : weather.error ? <p className="error" role="alert">Weather unavailable: {weather.error}</p>
            : !events.length ? <p className="muted">{Array.isArray(weather.data) && weather.data.length ? 'No valid city forecasts returned.' : `No global-city forecast coverage for ${country}.`}</p>
              : (
                <>
                  <ul className="rows">
                    {events.map(({ row, event }) => {
                      const context = [
                        typeof row.apparent_temp_f === 'number' && Number.isFinite(row.apparent_temp_f) && `feels like ${Math.round(row.apparent_temp_f)}°F`,
                        typeof row.wind_10m_mph === 'number' && Number.isFinite(row.wind_10m_mph) && `wind ${Math.round(row.wind_10m_mph)} mph`,
                      ].filter(Boolean).join(' · ')
                      return (
                        <li key={event.id}>
                          <strong>{event.title}</strong> <span>{event.value}</span>
                          {context && <p className="micro">{context}</p>}
                        </li>
                      )
                    })}
                  </ul>
                  <p className="micro">Source: NOAA GFS</p>
                </>
              )}
    </Card>
  )
}

export default function CountryPanel({ country, onClose, mode = 'world', commodity = 'oil', coordinates = null }: {
  country: string
  onClose: () => void
  mode?: 'world' | 'commodities'
  commodity?: Commodity
  coordinates?: { lat: number; lng: number } | null
}) {
  const commodityMode = mode === 'commodities'
  const base = `/countries/${encodeURIComponent(country)}`
  const rundown = useFetch<Rundown>(base)
  const editorial = useFetch<Editorial>(commodityMode
    ? `/commodities/${commodity}/editorial?country=${encodeURIComponent(country)}`
    : `${base}/editorial`)
  const commodityEtfs = useFetch<{ funds: Fund[] }>(commodityMode ? `/commodities/etfs?commodity=${commodity}&days=30` : null)
  const commodityFutures = useFetch<{ contracts: Contract[] }>(commodityMode ? `/commodities/${commodity}` : null)
  const commodityMarkets = useFetch<CommodityMarket[]>(commodityMode ? `/commodities/markets?commodity=${commodity}&limit=20` : null)
  const commodityNews = useFetch<News[]>(commodityMode ? `/commodities/headlines?country=${encodeURIComponent(country)}&limit=12` : null)
  const r = rundown.data
  const local = useFetch<{ region: string | null; news: News[]; hours: number }>(!commodityMode && coordinates
    ? `/news/local?lat=${coordinates.lat.toFixed(3)}&lon=${coordinates.lng.toFixed(3)}` : null)
  const benchmarks = regionalCommodityBenchmarks(commodityFutures.data?.contracts ?? [], coordinates)
  const us = country === 'United States of America'
  const [bondView, setBondView] = useState<'bonds' | 'etfs'>('bonds')
  // Rundown cards mount immediately and show their own loading/error/missing state, so the card set
  // (and the grid) never changes when the fetch lands.
  const fromRundown = <T,>(x: T | Failed | undefined, missing: string, show: (x: T) => ReactNode, failText = '') =>
    !r ? (rundown.error ? <p className="error" role="alert">{rundown.error}</p> : <p className="muted">Loading sources…</p>)
      : x === undefined ? <p className="muted">{missing}</p>
        : failed(x) ? <p className="error">{failText}{x.error}</p> : show(x)

  const briefing = (
    <div className="editorial" aria-live="polite">
      {editorial.loading && <p className="muted shimmer">Gemini is writing the briefing…</p>}
      {editorial.error && <p className="muted">Briefing unavailable: {editorial.error}</p>}
      {editorial.data && (
        <div className="editorial-cols">
          <div>
            <h3>{editorial.data.headline}</h3>
            <Markdown text={editorial.data.summary} />
          </div>
          {!!editorial.data.watch.length && (
            <div>
              <p className="section-title">Watch</p>
              <ul className="watch">{editorial.data.watch.map((w) => <li key={w}><Inline text={w} /></li>)}</ul>
              <p className="micro">Written by Gemini from the sources on this page</p>
            </div>
          )}
        </div>
      )}
    </div>
  )

  const markets = (
    <>
      <h3 className="section-title">Prediction markets</h3>
      {!r ? <p className="muted">Loading…</p> : failed(r.markets) ? <p className="error">{r.markets.error}</p> : !r.markets.length ? <p className="muted">No matching markets.</p> : (
        <ul className="rows">
          {r.markets.map((m) => (
            <li key={`${m.platform}-${m.market_id}`}>
              <a href={m.url} target="_blank" rel="noreferrer">{m.question_raw}</a>
              <p className="micro">
                {m.platform}
                {m.outcomes.slice(0, 3).map((o, k) => (
                  <span key={k}> / {o.raw} {o.price != null && `${Math.round(o.price * 100)}%`}</span>
                ))}
              </p>
            </li>
          ))}
        </ul>
      )}
    </>
  )

  const commodityMarketList = (
    <>
      <h3 className="section-title">Kalshi commodity markets</h3>
      {commodityMarkets.loading && <p className="muted">Loading markets…</p>}
      {commodityMarkets.error && <p className="error" role="alert">{commodityMarkets.error}</p>}
      {commodityMarkets.data && !commodityMarkets.data.length && <p className="muted">No matching markets.</p>}
      <ul className="rows">
        {commodityMarkets.data?.map((market, index) => (
          <li key={`${market.url ?? market.question_raw}-${index}`}>
            {market.url ? <a href={market.url} target="_blank" rel="noreferrer">{market.question_raw}</a> : <span>{market.question_raw}</span>}
            <p className="micro">
              {market.status ?? 'Market'}
              {market.close_time && ` / closes ${day(market.close_time)}`}
              {market.outcomes?.slice(0, 3).map((outcome, i) => (
                <span key={i}> / {outcome.raw}{outcome.price != null && ` ${Math.round(outcome.price * 100)}%`}</span>
              ))}
            </p>
          </li>
        ))}
      </ul>
    </>
  )

  return (
    <FocusPanel label={`${country} rundown`} kicker={commodityMode ? 'Commodity briefing' : 'Country briefing'} title={country}
      hero={briefing}
      aside={commodityMode ? commodityMarketList : markets} onClose={onClose}>
      {commodityMode ? (
        <Card title="Commodity news">
          {commodityNews.loading && <p className="muted">Loading headlines…</p>}
          {commodityNews.error && <p className="error" role="alert">{commodityNews.error}</p>}
          {commodityNews.data && !commodityNews.data.length && <p className="muted">No commodity headlines for {country}.</p>}
          <ul className="rows">
            {commodityNews.data?.map((n) => (
              <li key={n.id}>
                <a href={n.link} target="_blank" rel="noreferrer">{n.title}</a>
                <p className="micro">{n.source} / {day(n.published_at)}</p>
              </li>
            ))}
          </ul>
        </Card>
      ) : coordinates ? (
        <Card title={local.data?.region ? `${local.data.region} news` : 'Regional news'}>
          {local.loading ? <p className="muted" aria-live="polite">Finding regional stories…</p>
            : local.error ? <p className="error" role="alert">Regional news unavailable: {local.error}</p>
              : !local.data?.news.length ? <p className="muted">No stories for {local.data?.region ?? 'this area'} or anywhere around it in the last year.</p> : (
                <>
                <ul className="rows">
                  {local.data.news.map((n) => (
                    <li key={n.id}>
                      <a href={n.link} target="_blank" rel="noreferrer">{n.title}</a>
                      <p className="micro">{n.source} / {day(n.published_at)}</p>
                    </li>
                  ))}
                </ul>
                {local.data.hours > 336 && <p className="micro">Nothing in the last 14 days; showing the last {local.data.hours === 2160 ? '90 days' : 'year'}.</p>}
                </>
              )}
        </Card>
      ) : (
        <Card title="News">
          {fromRundown(r?.news, 'No news returned.', (news) => !news.length ? <p className="muted">No WSJ or FT coverage in the last year.</p> : (
            <ul className="rows">
              {news.map((n) => (
                <li key={n.id}>
                  <a href={n.link} target="_blank" rel="noreferrer">{n.title}</a>
                  <p className="micro">{n.source} / {day(n.published_at)}</p>
                </li>
              ))}
            </ul>
          ))}
        </Card>
      )}
      {commodityMode ? (
        <>
          <Card title={coordinates ? 'Benchmarks for this region' : 'Benchmark prices'}>
            {commodityFutures.loading && <p className="muted">Loading benchmark prices…</p>}
            {commodityFutures.error && <p className="error" role="alert">Benchmark prices unavailable: {commodityFutures.error}</p>}
            {commodityFutures.data && !benchmarks.length && <p className="muted">No mapped regional benchmarks are available for {COMMODITIES[commodity].toLowerCase()}.</p>}
            {benchmarks.map((contract) => <CommodityPriceChart key={contract.symbol} contract={contract} />)}
            {!!benchmarks.length && <p className="micro">Market futures benchmarks—not retail or local physical prices. {coordinates ? 'Contracts that price this trading region, plus global exchange references.' : 'All mapped benchmark contracts.'}</p>}
          </Card>
          <Card title={`${COMMODITIES[commodity]} ETFs · 30 days`}>
            {commodityEtfs.loading && <p className="muted">Loading funds…</p>}
            {commodityEtfs.error && <p className="error" role="alert">{commodityEtfs.error}</p>}
            {commodityEtfs.data && !commodityEtfs.data.funds.length && <p className="muted">No ETF data available.</p>}
            {!!commodityEtfs.data?.funds.length && <EtfTrends funds={commodityEtfs.data.funds} />}
          </Card>
        </>
      ) : <WeatherCard country={country} />}
      <GovernmentAnnouncements country={country} />
      {!commodityMode && (
        <Card title={us ? 'Treasury yield curve' : 'Government yield curve'}>
          {fromRundown(r?.treasury_curve, `No yield curve available for ${country}.`, (curve) => (
            <>
              <YieldCurve now={curve} past={curve.month_ago} />
              <p className="micro">Source: {curve.source}</p>
            </>
          ), 'Yield curve unavailable: ')}
        </Card>
      )}
      {!commodityMode && (
        <Card title="Government bonds">
          {/* US Treasury ETFs share this card so every country gets the same six cards in the same places */}
          {us && (
            <div className="motion" role="radiogroup" aria-label="Bond view">
              {(['bonds', 'etfs'] as const).map((v) => (
                <button key={v} role="radio" aria-checked={bondView === v} className={bondView === v ? 'on' : ''} onClick={() => setBondView(v)}>
                  {v === 'bonds' ? 'Yields' : 'Treasury ETFs'}
                </button>
              ))}
            </div>
          )}
          {us && bondView === 'etfs' ? fromRundown(r?.treasury_etfs, 'No Treasury ETF data returned.', (etfs) => (
            <>
              <EtfTrends funds={etfs.funds} />
              <p className="micro">Source: Alpaca, iShares Treasury ETFs, last month</p>
            </>
          )) : fromRundown(r?.bonds, `No government bond data available for ${country}.`, (bonds) => (
            <>
              <ul className="rows">
                {bonds.map((b) => (
                  <li key={b.symbol}>
                    <strong>{b.tenor}</strong> <span>{b.yield.toFixed(2)}%</span>
                    {b.change_1m_bp !== null && <span className="micro"> {b.change_1m_bp >= 0 ? '+' : ''}{b.change_1m_bp.toFixed(0)} bp / 1M</span>}
                  </li>
                ))}
              </ul>
              <p className="micro">{bonds[0]?.name.replace(/\d+ (Year|Month) /, '')} · TradingView</p>
            </>
          ), 'Bond yields unavailable: ')}
        </Card>
      )}
      <Card title="SEC filings">
        {fromRundown(r?.filings, 'No filings returned.', (filings) => <FilingList country={country} initial={filings} />)}
      </Card>
    </FocusPanel>
  )
}
