import { useEffect, useState, type MouseEvent, type ReactNode } from 'react'
import { useApi } from './api'

type Score = { lead_days: number; model: string; role: string; days: number; mae_f: number; bias_f: number; within_2f_pct: number }
type City = { location: string; station_id: string; days: number; nostos_mae_f: number | null; nbm_corrected_mae_f: number | null; nbm_mae_f: number | null }
type Day = { climate_date: string; observed_f: number | null; nostos_f: number | null; nbm_corrected_f: number | null; nbm_f: number | null }
type Load<T> = { data?: T; error?: string }

const LABELS: Record<string, string> = { nostos: 'Nostos', nbm_corrected: 'NBM corrected', ncep_nbm_conus: 'NBM', naive_persistence: 'Persistence' }
const SERIES = [
  { key: 'observed_f', label: 'Observed', color: 'var(--text)', dash: '' },
  { key: 'nostos_f', label: 'Nostos', color: '#199e70', dash: '' },
  { key: 'nbm_corrected_f', label: 'NBM corrected', color: '#3987e5', dash: '' },
  { key: 'nbm_f', label: 'NBM', color: 'var(--muted)', dash: '4 3' },
] as const
const cityName = (loc: string) => loc.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
const f1 = (v: number | null) => (v === null ? '–' : v.toFixed(1))

function useLoad<T>(path: string | null) {
  const api = useApi()
  const [state, setState] = useState<Load<T>>({})
  useEffect(() => {
    if (!path) return
    let live = true
    setState({})
    api<T>(path).then((data) => live && setState({ data }), (e: Error) => live && setState({ error: e.message }))
    return () => {
      live = false
    }
  }, [api, path])
  return state
}

/** Body of a fixed-height card: loading, error and empty states take the same box as the content. */
function Body<T>({ state, empty, children }: { state: Load<T[]>; empty: string; children: (rows: T[]) => ReactNode }) {
  if (state.error) return <p className="model-state error" role="alert">{state.error}</p>
  if (!state.data) return <p className="model-state" role="status">Loading…</p>
  if (!state.data.length) return <p className="model-state">{empty}</p>
  return <>{children(state.data)}</>
}

function Timeline({ days, city }: { days: Day[]; city: string }) {
  const W = 640
  const H = 230
  const pad = { l: 36, r: 12, t: 10, b: 24 }
  const [hover, setHover] = useState<number | null>(null)
  const values = days.flatMap((d) => SERIES.map((s) => d[s.key])).filter((v): v is number => v !== null)
  if (!values.length) return <p className="model-state">No values published yet.</p>
  const lo = Math.floor(Math.min(...values) - 2)
  const hi = Math.ceil(Math.max(...values) + 2)
  const sx = (k: number) => pad.l + (k / Math.max(days.length - 1, 1)) * (W - pad.l - pad.r)
  const sy = (v: number) => pad.t + ((hi - v) / (hi - lo)) * (H - pad.t - pad.b)
  // Gaps (unpublished observations) break the line instead of bridging them.
  const path = (key: (typeof SERIES)[number]['key']) =>
    days.map((d, k) => (d[key] === null ? '' : `${k && days[k - 1][key] !== null ? 'L' : 'M'}${sx(k).toFixed(1)},${sy(d[key]!).toFixed(1)}`)).join('')
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    setHover(Math.max(0, Math.min(days.length - 1, Math.round((((e.clientX - box.left) / box.width) * W - pad.l) / (W - pad.l - pad.r) * (days.length - 1)))))
  }
  const h = hover === null ? null : days[hover]
  const step = Math.max(1, Math.ceil(days.length / 6))
  const fmt = (iso: string) => new Date(iso + 'T12:00:00Z').toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' })

  return (
    <figure className="chart">
      <ul className="chart-legend">
        {SERIES.map((s) => (
          <li key={s.key}><svg width="18" height="8" aria-hidden="true"><line x1="0" y1="4" x2="18" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} /></svg>{s.label}</li>
        ))}
      </ul>
      <div className="chart-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${city} daily high: observed vs Nostos vs NBM corrected vs NBM, °F`}
          onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          {[lo, (lo + hi) / 2, hi].map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} className="grid" />
              <text x={pad.l - 6} y={sy(t) + 3} textAnchor="end" className="tick">{t.toFixed(0)}°</text>
            </g>
          ))}
          {days.map((d, k) => k % step === 0 && <text key={d.climate_date} x={sx(k)} y={H - 6} textAnchor="middle" className="tick">{fmt(d.climate_date)}</text>)}
          {SERIES.map((s) => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth={s.key === 'observed_f' ? 2.5 : 1.75} strokeDasharray={s.dash} strokeLinejoin="round" />)}
          {h && <line x1={sx(hover!)} x2={sx(hover!)} y1={pad.t} y2={H - pad.b} className="crosshair" />}
        </svg>
        {h && (
          <div className="chart-tip" style={{ left: `clamp(90px, ${(sx(hover!) / W) * 100}%, calc(100% - 90px))` }}>
            <b>{fmt(h.climate_date)}</b>
            {SERIES.map((s) => <span key={s.key}>{s.label} {f1(h[s.key])}°F</span>)}
          </div>
        )}
      </div>
    </figure>
  )
}

export default function ModelScores() {
  const [lead, setLead] = useState(1)
  const [city, setCity] = useState<string | null>(null)
  const board = useLoad<Score[]>(`/wx/views/leaderboard?lead_days=${lead}`)
  const cities = useLoad<City[]>('/wx/views/cities')
  const timeline = useLoad<Day[]>(city && `/wx/views/cities/${city}/timeline`)

  return (
    <div className="search model">
      <div className="search-inner">
        <header className="search-header">
          <p className="kicker">Daily high temperature · test period Sep 14 – Nov 12, 2026</p>
          <h1>Model scores</h1>
        </header>

        <section className="model-card" aria-labelledby="model-board">
          <div className="model-card-head">
            <h2 id="model-board" className="section-title">Leaderboard · lower MAE is better</h2>
            <div className="model-leads" role="group" aria-label="Lead time">
              {[1, 2, 3].map((d) => (
                <button key={d} type="button" aria-pressed={lead === d} onClick={() => setLead(d)}>{d} day{d > 1 ? 's' : ''}</button>
              ))}
            </div>
          </div>
          <div className="model-body model-body-board">
            <Body state={board} empty="No scores for this lead yet.">
              {(rows) => (
                <table className="model-table">
                  <thead><tr><th>Model</th><th>Role</th><th>Days</th><th>MAE °F</th><th>Bias °F</th><th>Within 2°F</th></tr></thead>
                  <tbody>
                    {[...rows].sort((a, b) => a.mae_f - b.mae_f).map((r) => (
                      <tr key={r.model} className={r.role === 'nostos' ? 'model-ours' : undefined}>
                        <td>{LABELS[r.model] ?? r.model}</td>
                        <td className="muted">{r.role}</td>
                        <td>{r.days}</td>
                        <td>{r.mae_f.toFixed(2)}</td>
                        <td>{r.bias_f > 0 ? '+' : ''}{r.bias_f.toFixed(2)}</td>
                        <td>{r.within_2f_pct.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Body>
          </div>
        </section>

        <div className="model-grid">
          <section className="model-card" aria-labelledby="model-cities">
            <h2 id="model-cities" className="section-title">City MAE °F · 1-day lead · select a city</h2>
            <div className="model-body model-body-cities">
              <Body state={cities} empty="No city scores yet.">
                {(rows) => (
                  <table className="model-table">
                    <thead><tr><th>City</th><th>Nostos</th><th>NBM corr.</th><th>NBM</th></tr></thead>
                    <tbody>
                      {rows.map((c) => {
                        const vals = [c.nostos_mae_f, c.nbm_corrected_mae_f, c.nbm_mae_f]
                        const best = Math.min(...vals.filter((v): v is number => v !== null))
                        return (
                          <tr key={c.location} className={city === c.location ? 'model-selected' : undefined}>
                            <td>
                              <button type="button" className="model-city" aria-pressed={city === c.location} onClick={() => setCity(c.location)}>
                                {cityName(c.location)} <span className="muted">{c.station_id}</span>
                              </button>
                            </td>
                            {vals.map((v, k) => <td key={k} className={v === best ? 'model-best' : undefined}>{v === null ? '–' : v.toFixed(2)}</td>)}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                )}
              </Body>
            </div>
          </section>

          <section className="model-card" aria-labelledby="model-timeline">
            <h2 id="model-timeline" className="section-title">{city ? `${cityName(city)} · daily high °F` : 'City timeline'}</h2>
            <div className="model-body model-body-timeline">
              {city
                ? <Body state={timeline} empty="No days published for this city yet.">{(days) => <Timeline days={days} city={cityName(city)} />}</Body>
                : <p className="model-state">Select a city to compare observed highs with each forecast.</p>}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
