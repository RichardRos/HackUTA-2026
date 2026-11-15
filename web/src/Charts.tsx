import { useState, type MouseEvent } from 'react'
import type { Contract } from './events'

// Validated with the dataviz skill's palette checker against the panel surface #10191d (dark mode).
const NOW = '#199e70'
const PAST = '#3987e5'

export type CurvePoint = { tenor: string; years: number; yield: number }
export type Fund = {
  symbol: string
  label: string
  unit?: string
  closes: { date: string; close: number }[]
  change_pct: number | null
}

const W = 360
const fmtDate = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric' })

/** Index of the x position closest to the pointer, in SVG units. */
function useHover(xs: number[]) {
  const [i, setI] = useState<number | null>(null)
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - box.left) / box.width) * W
    let best = 0
    xs.forEach((v, k) => Math.abs(v - x) < Math.abs(xs[best] - x) && (best = k))
    setI(best)
  }
  return { i, onMove, onLeave: () => setI(null) }
}

export function YieldCurve({ now, past }: { now: { as_of: string; curve: CurvePoint[] }; past: { as_of: string; curve: CurvePoint[] } }) {
  const H = 190
  const pad = { l: 44, r: 52, t: 10, b: 24 }
  const current = [...now.curve].sort((a, b) => a.years - b.years)
  const previous = [...past.curve].sort((a, b) => a.years - b.years)
  const previousByTenor = new Map(previous.map((p) => [p.tenor, p]))
  const all = [...current, ...previous].map((p) => p.yield)
  const lo = Math.floor((Math.min(...all) - 0.05) * 10) / 10
  const hi = Math.ceil((Math.max(...all) + 0.05) * 10) / 10
  const lx = (y: number) => Math.log(y)
  const x0 = lx(1 / 12)
  const x1 = lx(30)
  const sx = (years: number) => pad.l + ((lx(years) - x0) / (x1 - x0)) * (W - pad.l - pad.r)
  const sy = (v: number) => pad.t + ((hi - v) / (hi - lo)) * (H - pad.t - pad.b)
  const path = (c: CurvePoint[]) => c.map((p, k) => `${k ? 'L' : 'M'}${sx(p.years).toFixed(1)},${sy(p.yield).toFixed(1)}`).join('')
  const yTicks = [lo, (lo + hi) / 2, hi]
  const xTicks: [string, number][] = [['3M', 0.25], ['1Y', 1], ['2Y', 2], ['5Y', 5], ['10Y', 10], ['30Y', 30]]
  const hover = useHover(current.map((p) => sx(p.years)))
  const h = hover.i === null ? null : current[hover.i]
  const hp = h ? previousByTenor.get(h.tenor) : undefined
  const end = (c: CurvePoint[]) => c[c.length - 1]
  const nowEnd = end(current)
  const pastEnd = end(previous)
  const nowEndY = sy(nowEnd.yield)
  const pastEndY = sy(pastEnd.yield)
  const separated = Math.abs(nowEndY - pastEndY) < 14
  const nowAbove = nowEndY <= pastEndY
  const nowLabelY = nowEndY + (separated ? (nowAbove ? -2 : 10) : 3)
  const pastLabelY = pastEndY + (separated ? (nowAbove ? 10 : -2) : 3)

  return (
    <figure className="chart">
      <ul className="chart-legend">
        <li><svg width="18" height="8" aria-hidden="true"><line x1="0" y1="4" x2="18" y2="4" stroke={NOW} strokeWidth="2" /></svg>{fmtDate(now.as_of)}</li>
        <li><svg width="18" height="8" aria-hidden="true"><line x1="0" y1="4" x2="18" y2="4" stroke={PAST} strokeWidth="2" strokeDasharray="4 3" /></svg>{fmtDate(past.as_of)}</li>
      </ul>
      <div className="chart-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Treasury yield curve on ${now.as_of} and ${past.as_of}`}
          onMouseMove={hover.onMove} onMouseLeave={hover.onLeave}>
          {yTicks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} className="grid" />
              <text x={pad.l - 6} y={sy(t) + 3} textAnchor="end" className="tick">{t.toFixed(2)}%</text>
            </g>
          ))}
          {xTicks.map(([label, yrs]) => (
            <text key={label} x={sx(yrs)} y={H - 6} textAnchor="middle" className="tick">{label}</text>
          ))}
          <path d={path(previous)} fill="none" stroke={PAST} strokeWidth="2" strokeDasharray="4 3" strokeLinejoin="round" />
          <path d={path(current)} fill="none" stroke={NOW} strokeWidth="2" strokeLinejoin="round" />
          <text x={W - pad.r + 6} y={nowLabelY} className="direct">{fmtDate(now.as_of)}</text>
          <text x={W - pad.r + 6} y={pastLabelY} className="direct">{fmtDate(past.as_of)}</text>
          {h && (
            <g>
              <line x1={sx(h.years)} x2={sx(h.years)} y1={pad.t} y2={H - pad.b} className="crosshair" />
              {hp && <circle cx={sx(h.years)} cy={sy(hp.yield)} r="4" fill={PAST} className="dot" />}
              <circle cx={sx(h.years)} cy={sy(h.yield)} r="4" fill={NOW} className="dot" />
            </g>
          )}
        </svg>
        {h && (
          <div className="chart-tip" style={{ left: `clamp(90px, ${(sx(h.years) / W) * 100}%, calc(100% - 90px))` }}>
            <b>{h.tenor}</b>
            <span>{fmtDate(now.as_of)} {h.yield.toFixed(2)}%</span>
            {hp && <span>{fmtDate(past.as_of)} {hp.yield.toFixed(2)}%</span>}
          </div>
        )}
      </div>
      <details className="chart-table">
        <summary>Table</summary>
        <table>
          <thead><tr><th>Maturity</th><th>{fmtDate(now.as_of)}</th><th>{fmtDate(past.as_of)}</th></tr></thead>
          <tbody>
            {current.map((p) => {
              const q = previousByTenor.get(p.tenor)
              return <tr key={p.tenor}><td>{p.tenor}</td><td>{p.yield.toFixed(2)}%</td><td>{q ? `${q.yield.toFixed(2)}%` : '–'}</td></tr>
            })}
          </tbody>
        </table>
      </details>
    </figure>
  )
}

function Spark({ fund, range }: { fund: Fund; range: [number, number] }) {
  const SW = 160
  const SH = 44
  const c = fund.closes
  // Plotted as % change from the first close on a scale shared by every fund, so slopes compare honestly.
  const pct = (v: number) => (v / c[0].close - 1) * 100
  const [lo, hi] = range
  const sx = (k: number) => 2 + (k / Math.max(c.length - 1, 1)) * (SW - 4)
  const sy = (v: number) => 4 + (hi === lo ? 0.5 : (hi - pct(v)) / (hi - lo)) * (SH - 8)
  const [i, setI] = useState<number | null>(null)
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    setI(Math.round(((e.clientX - box.left) / box.width) * (c.length - 1)))
  }
  const p = i === null ? c[c.length - 1] : c[Math.max(0, Math.min(c.length - 1, i))]
  const up = (fund.change_pct ?? 0) >= 0
  return (
    <li className="spark">
      <div className="spark-head">
        <b>{fund.symbol}</b>
        <span className="micro">{fund.label}</span>
      </div>
      {c.length > 1 ? (
        <svg viewBox={`0 0 ${SW} ${SH}`} role="img" aria-label={`${fund.symbol} closes over ${c.length} days, ${fund.change_pct}%`}
          onMouseMove={onMove} onMouseLeave={() => setI(null)}>
          <line x1="0" x2={SW} y1={sy(c[0].close)} y2={sy(c[0].close)} className="zero" />
          <path d={c.map((q, k) => `${k ? 'L' : 'M'}${sx(k).toFixed(1)},${sy(q.close).toFixed(1)}`).join('')} fill="none" stroke={NOW} strokeWidth="2" strokeLinejoin="round" />
          {i !== null && <circle cx={sx(c.indexOf(p))} cy={sy(p.close)} r="4" fill={NOW} className="dot" />}
        </svg>
      ) : (
        <p className="micro">No bars</p>
      )}
      <p className="spark-foot">
        <span>{!p ? '–' : fund.unit ? `${p.close.toFixed(2)} ${fund.unit}` : `$${p.close.toFixed(2)}`}{i !== null && p && <span className="micro">{fmtDate(p.date)}</span>}</span>
        {fund.change_pct !== null && (
          <span><span className={up ? 'up' : 'down'} aria-hidden="true">{up ? '▲' : '▼'}</span> {up ? '+' : ''}{fund.change_pct}%</span>
        )}
      </p>
    </li>
  )
}

export function EtfTrends({ funds }: { funds: Fund[] }) {
  const all = funds.flatMap((f) => f.closes.map((p) => (p.close / f.closes[0].close - 1) * 100))
  const range: [number, number] = [Math.min(0, ...all), Math.max(0, ...all)]
  return (
    <>
      <ul className="sparks">{funds.map((f) => <Spark key={f.symbol} fund={f} range={range} />)}</ul>
      <p className="micro">Shared scale: {range[0].toFixed(1)}% to +{range[1].toFixed(1)}% from each series' first close. Dotted line: no change.</p>
    </>
  )
}

export function CommodityPriceChart({ contract }: { contract: Contract }) {
  const closes = contract.closes
    .filter((point) => Number.isFinite(point.close) && point.close > 0 && !Number.isNaN(Date.parse(point.date)))
    .sort((a, b) => a.date.localeCompare(b.date))
  const lastDate = closes.length ? Date.parse(closes[closes.length - 1].date) : 0
  const history = closes.filter((point) => Date.parse(point.date) >= lastDate - 29 * 86400000)
  const latest = typeof contract.price === 'number' && Number.isFinite(contract.price)
    ? contract.price
    : history[history.length - 1]?.close

  if (!history.length) {
    return <p className="muted">Price history unavailable for {contract.name}.</p>
  }

  const H = 132
  const pad = { l: 56, r: 8, t: 12, b: 24 }
  const values = history.map((point) => point.close)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || Math.max(Math.abs(hi) * 0.02, 0.01)
  const sx = (index: number) => pad.l + (index / Math.max(history.length - 1, 1)) * (W - pad.l - pad.r)
  const sy = (value: number) => pad.t + ((hi + span * 0.08 - value) / (span * 1.16)) * (H - pad.t - pad.b)
  const path = history.map((point, index) => `${index ? 'L' : 'M'}${sx(index).toFixed(1)},${sy(point.close).toFixed(1)}`).join('')
  const first = history[0]
  const last = history[history.length - 1]

  return (
    <div>
      <p className="figure">{latest === undefined ? '–' : `${latest.toFixed(2)} ${contract.unit}`}</p>
      <p className="micro">{contract.name} · {contract.region} · {contract.symbol}</p>
      {history.length > 1 ? (
        <figure className="chart">
          <div className="chart-plot">
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${contract.name} price over the last 30 days`}>
              {[lo, (lo + hi) / 2, hi].map((value, index) => (
                <g key={index}>
                  <line x1={pad.l} x2={W - pad.r} y1={sy(value)} y2={sy(value)} className="grid" />
                  <text x={pad.l - 6} y={sy(value) + 3} textAnchor="end" className="tick">{value.toFixed(2)}</text>
                </g>
              ))}
              <path d={path} fill="none" stroke={NOW} strokeWidth="2" strokeLinejoin="round" />
              <text x={pad.l} y={H - 5} textAnchor="start" className="tick">{fmtDate(first.date)}</text>
              <text x={W - pad.r} y={H - 5} textAnchor="end" className="tick">{fmtDate(last.date)}</text>
            </svg>
          </div>
          <p className="micro">Daily settlement · {contract.unit}</p>
        </figure>
      ) : <p className="muted">Not enough recent closes to draw a price curve.</p>}
    </div>
  )
}
