import { useEffect, useState } from 'react'
import { useApi } from './api'
import { EtfTrends, type Fund } from './Charts'
import { COMMODITIES, type Commodity, type Contract } from './events'
import FocusPanel, { Card } from './FocusPanel'

type Futures = { contracts: Contract[]; spread: { name: string; value: number; unit: string } | null }
type News = { id: string; source: string; link: string; title: string; published_at: string }
type Market = {
  question_raw: string
  url?: string
  status?: string
  close_time?: string | null
  outcomes?: { raw?: string; price?: number }[]
}

function useFetch<T>(path: string) {
  const api = useApi()
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true })
  useEffect(() => {
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

const dateLabel = (value?: string | null) => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

const externalUrl = (value?: string) => {
  try {
    const url = new URL(value ?? '')
    return url.protocol === 'https:' ? url.href : undefined
  } catch {
    return undefined
  }
}

function yesProbability(outcomes: Market['outcomes']) {
  const values = outcomes?.flatMap((outcome) => typeof outcome.price === 'number' && Number.isFinite(outcome.price) && outcome.price >= 0 && outcome.price <= 100 ? [outcome.price] : []) ?? []
  const yes = outcomes?.find((outcome) => outcome.raw?.trim().toLowerCase() === 'yes')?.price
  if (typeof yes !== 'number' || !Number.isFinite(yes) || yes < 0 || yes > 100) return null
  // Some feeds use 0–1, others 0–100. Other outcomes disambiguate a value of exactly 1.
  const cents = values.some((value) => value > 1)
  return `${(cents || yes > 1 ? yes : yes * 100).toFixed(0)}%`
}

function marketState(status?: string) {
  const value = status?.trim()
  if (!value) return 'Status unavailable'
  if (/^(open|active|trading)$/i.test(value)) return 'Active'
  if (/^(closed|settled|expired|finalized)$/i.test(value)) return 'Closed'
  return value
}

function validCloses(closes: { date: string; close: number }[] = []) {
  return closes.filter((point) => Number.isFinite(point.close) && point.close > 0 && !!dateLabel(point.date))
}

export default function CommodityPanel({ commodity, onClose }: { commodity: Commodity; onClose: () => void }) {
  const futures = useFetch<Futures>(`/commodities/${commodity}`)
  const news = useFetch<News[]>(`/commodities/${commodity}/news`)
  const etfs = useFetch<{ funds: Fund[] }>(`/commodities/etfs?commodity=${encodeURIComponent(commodity)}&days=30`)
  const markets = useFetch<Market[]>(`/commodities/markets?commodity=${encodeURIComponent(commodity)}&limit=20`)
  const f = futures.data
  const name = COMMODITIES[commodity]
  const contracts = f?.contracts ?? []
  const contractCurves = contracts
    .map((contract) => ({ contract, closes: validCloses(contract.closes) }))
    .filter(({ closes }) => closes.length > 1)
    .map(({ contract, closes }) => ({
      symbol: contract.symbol,
      label: `${contract.region} · ${contract.settlement === 'cash' ? 'cash-settled' : 'physical delivery'}`,
      unit: contract.unit,
      closes,
      change_pct: contract.change_pct,
    }))
  const funds = (etfs.data?.funds ?? [])
    .map((fund) => ({ ...fund, closes: validCloses(fund.closes) }))
    .filter((fund) => fund.closes.length > 0)

  return (
    <FocusPanel
      label={`${name} futures`}
      kicker="Commodity futures"
      title={name}
      onClose={onClose}
      hero={
        <>
          {futures.loading && <p className="muted">Loading futures…</p>}
          {futures.error && <p className="error" role="alert">{futures.error}</p>}
          {f?.spread && (
            <p className="figure-row">
              <span className="micro">{f.spread.name} spread</span>
              {f.spread.value > 0 ? '+' : ''}{f.spread.value.toFixed(2)} {f.spread.unit}
            </p>
          )}
          {f && <p className="muted">Front-month regional benchmarks, relative price curves, related funds, and {name.toLowerCase()}-filtered headlines and markets.</p>}
        </>
      }
    >
      <Card title="Front-month benchmarks" span={2}>
        {futures.loading && <p className="muted">Loading benchmark prices…</p>}
        {futures.error && <p className="error" role="alert">Benchmark prices unavailable.</p>}
        {f && !contracts.length && <p className="muted">No benchmark prices available.</p>}
        {!!contracts.length && (
          <ul className="tiles">
            {contracts.map((contract) => {
              const closes = validCloses(contract.closes)
              const latest = closes.slice().sort((a, b) => a.date.localeCompare(b.date)).at(-1)
              const price = typeof contract.price === 'number' && Number.isFinite(contract.price) ? contract.price : latest?.close
              const isUp = contract.change_pct !== null && contract.change_pct >= 0
              return (
                <li key={contract.symbol}>
                  <p className="section-title">{contract.name}</p>
                  <span className="tile-value">{price === undefined ? '–' : `${price.toFixed(2)} ${contract.unit}`}</span>
                  <p className="micro">{contract.symbol} · {contract.region}</p>
                  <p className="micro">{contract.settlement === 'cash' ? 'Cash-settled' : 'Physical delivery'}{contract.price == null && latest && ` · latest close ${dateLabel(latest.date)}`}</p>
                  {contract.change_pct !== null && (
                    <p className="micro"><span className={isUp ? 'up' : 'down'}>{isUp ? '+' : ''}{contract.change_pct}%</span> · 30-day change</p>
                  )}
                </li>
              )
            })}
          </ul>
        )}
        {!!contractCurves.length && (
          <>
            <h4 className="section-title">Benchmark curves · 30 days</h4>
            <EtfTrends funds={contractCurves} />
          </>
        )}
      </Card>
      <Card title="Related funds · 30 days" span={1}>
        {etfs.loading && <p className="muted">Loading related funds…</p>}
        {etfs.error && <p className="error" role="alert">{etfs.error}</p>}
        {etfs.data && !funds.length && <p className="muted">No related fund price history available.</p>}
        {!!funds.length && <EtfTrends funds={funds} />}
      </Card>
      <Card title="Kalshi markets" span={2}>
        {markets.loading && <p className="muted">Loading markets…</p>}
        {markets.error && <p className="error" role="alert">{markets.error}</p>}
        {markets.data && !markets.data.length && <p className="muted">No matching {name.toLowerCase()} markets.</p>}
        <ul className="rows">
          {markets.data?.map((market, index) => {
            const url = externalUrl(market.url)
            const yes = yesProbability(market.outcomes)
            const closes = dateLabel(market.close_time)
            return (
              <li key={`${market.url ?? market.question_raw}-${index}`}>
                {url ? <a href={url} target="_blank" rel="noopener noreferrer">{market.question_raw}</a> : <span>{market.question_raw}</span>}
                <p className="micro">
                  {marketState(market.status)}
                  {yes && ` · Yes ${yes}`}
                  {closes && ` · closes ${closes}`}
                </p>
              </li>
            )
          })}
        </ul>
      </Card>
      <Card title={`${name} news`} span={1}>
        {news.loading && <p className="muted">Loading news…</p>}
        {news.error && <p className="error" role="alert">{news.error}</p>}
        {news.data && !news.data.length && <p className="muted">No matching coverage in the last 14 days.</p>}
        <ul className="rows">
          {news.data?.map((item) => (
            <li key={item.id}>
              <a href={item.link} target="_blank" rel="noopener noreferrer">{item.title}</a>
              <p className="micro">{item.source}{dateLabel(item.published_at) && ` · ${dateLabel(item.published_at)}`}</p>
            </li>
          ))}
        </ul>
      </Card>
    </FocusPanel>
  )
}
