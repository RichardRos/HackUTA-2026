import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { useApi } from './api'
import { at, flatten, fmtDuration, fmtUtc, hsColor, type RoutePoint, type Voyage, type VoyageOptions } from './voyage'

type Mode = 'ports' | 'points' | 'odyssey'
type LatLon = { lat: number; lon: number }
export type Picking = 'start' | 'end' | null

const W = 320
const H = 74
const PAD = { l: 30, r: 8, t: 8, b: 16 }

/** One measure over voyage hours (small multiples: waves and speed get their own axes). */
function Strip({ track, value, label, unit, max, hour, onHour, limit, color }: {
  track: RoutePoint[]; value: (p: RoutePoint) => number | undefined; label: string; unit: string; max: number
  hour: number; onHour: (h: number) => void; limit?: number; color: string
}) {
  const end = track[track.length - 1]?.hours || 1
  const sx = (h: number) => PAD.l + (h / end) * (W - PAD.l - PAD.r)
  const sy = (v: number) => PAD.t + (1 - Math.min(v, max) / max) * (H - PAD.t - PAD.b)
  const pts = track.filter((p) => value(p) != null)
  const line = pts.map((p, k) => `${k ? 'L' : 'M'}${sx(p.hours).toFixed(1)},${sy(value(p)!).toFixed(1)}`).join('')
  const area = pts.length ? `${line}L${sx(pts[pts.length - 1].hours)},${sy(0)}L${sx(pts[0].hours)},${sy(0)}Z` : ''
  const move = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - box.left) / box.width) * W
    onHour(Math.max(0, Math.min(end, ((x - PAD.l) / (W - PAD.l - PAD.r)) * end)))
  }
  const now = at(track, hour)
  return (
    <figure className="chart voyage-strip">
      <figcaption className="section-title">{label}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label} along the voyage`} onMouseMove={move}>
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={PAD.l - 4} y={sy(t) + 3} textAnchor="end" className="tick">{t % 1 ? t.toFixed(1) : t}</text>
          </g>
        ))}
        {limit != null && limit < max && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(limit)} y2={sy(limit)} className="crosshair" />
            <text x={W - PAD.r} y={sy(limit) - 3} textAnchor="end" className="tick">vessel limit</text>
          </g>
        )}
        <path d={area} fill={color} opacity="0.14" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        <line x1={sx(hour)} x2={sx(hour)} y1={PAD.t} y2={H - PAD.b} className="crosshair" />
        {now && value(now) != null && <circle cx={sx(hour)} cy={sy(value(now)!)} r="4" fill={color} className="dot" />}
        <text x={PAD.l} y={H - 3} className="tick">0 h</text>
        <text x={W - PAD.r} y={H - 3} textAnchor="end" className="tick">{fmtDuration(end)}</text>
      </svg>
      <span className="micro">{now && value(now) != null ? `${value(now)!.toFixed(1)} ${unit} at ${fmtDuration(hour)}` : ' '}</span>
    </figure>
  )
}

export default function VoyagePanel({ greekMode, picked, picking, onPicking, onResult, hour, onHour }: {
  greekMode: boolean
  picked: { start: LatLon | null; end: LatLon | null }
  picking: Picking
  onPicking: (p: Picking) => void
  onResult: (v: Voyage | null) => void
  hour: number
  onHour: (h: number) => void
}) {
  const api = useApi()
  const [options, setOptions] = useState<VoyageOptions | null>(null)
  const [mode, setMode] = useState<Mode>(greekMode ? 'odyssey' : 'ports')
  const [startPort, setStartPort] = useState('cnsha')
  const [endPort, setEndPort] = useState('nlrtm')
  const [vessel, setVessel] = useState(greekMode ? 'trireme' : 'container')
  const [speed, setSpeed] = useState('')
  const [depart, setDepart] = useState('')
  const [status, setStatus] = useState<'idle' | 'loading' | string>('idle')
  const [result, setResult] = useState<Voyage | null>(null)
  const [playing, setPlaying] = useState(false)
  const frame = useRef(0)

  useEffect(() => {
    let live = true
    api<VoyageOptions>('/voyage/options').then((o) => live && setOptions(o)).catch((e: Error) => live && setStatus(e.message))
    return () => { live = false }
  }, [api])
  useEffect(() => { if (mode === 'odyssey') setVessel('trireme') }, [mode])

  const track = useMemo(() => (result ? flatten(result) : []), [result])
  const end = track[track.length - 1]?.hours ?? 0

  useEffect(() => {
    if (!playing) return
    const startReal = performance.now()
    const from = hour >= end ? 0 : hour
    const tick = (now: number) => {
      const h = from + ((now - startReal) / 20_000) * end // the whole voyage in 20 s
      onHour(Math.min(end, h))
      if (h < end) frame.current = requestAnimationFrame(tick)
      else setPlaying(false)
    }
    frame.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only on play/stop
  }, [playing])

  const ready = mode === 'odyssey' || (mode === 'ports' ? startPort !== endPort : !!(picked.start && picked.end))
  const plan = () => {
    setStatus('loading')
    setPlaying(false)
    const body = {
      mode, vessel,
      ...(mode === 'ports' && { start_port: startPort, end_port: endPort }),
      ...(mode === 'points' && { start: picked.start, end: picked.end }),
      ...(speed && { speed_kn: Number(speed) }),
      ...(depart && { depart: new Date(depart).toISOString() }),
    }
    api<Voyage>('/voyage/route', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })
      .then((v) => {
        setResult(v)
        onResult(v)
        onHour(0)
        setStatus('idle')
      })
      .catch((e: Error) => setStatus(e.message))
  }
  const clear = () => {
    setResult(null)
    onResult(null)
  }
  const ports = useMemo(() => [...(options?.ports ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [options])
  const here = at(track, hour)
  const fmtLL = (p: LatLon | null) => (p ? `${p.lat.toFixed(2)}, ${p.lon.toFixed(2)}` : 'not set')
  const nowLocal = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
  const maxLocal = new Date(Date.now() + 7 * 86_400_000 - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)

  return (
    <section className="voyage" aria-label="Voyage planner">
      <p className="kicker">{greekMode ? 'Plot a course' : 'Voyage planner'}</p>
      <div className="view-switch voyage-modes" role="group" aria-label="Route type">
        {(['ports', 'points', 'odyssey'] as const).map((m) => (
          <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'on' : ''} onClick={() => { setMode(m); onPicking(null) }}>
            {m === 'ports' ? 'Ports' : m === 'points' ? 'Custom' : 'Odyssey'}
          </button>
        ))}
      </div>

      {mode === 'ports' && (
        <div className="voyage-fields">
          <label>From
            <select value={startPort} onChange={(e) => setStartPort(e.target.value)}>
              {ports.map((p) => <option key={p.id} value={p.id}>{p.name}, {p.country}</option>)}
            </select>
          </label>
          <label>To
            <select value={endPort} onChange={(e) => setEndPort(e.target.value)}>
              {ports.map((p) => <option key={p.id} value={p.id}>{p.name}, {p.country}</option>)}
            </select>
          </label>
        </div>
      )}
      {mode === 'points' && (
        <div className="voyage-fields">
          {(['start', 'end'] as const).map((which) => (
            <div key={which} className="voyage-pick">
              <span><b>{which === 'start' ? 'Start' : 'End'}</b> <span className="micro">{fmtLL(picked[which])}</span></span>
              <button type="button" className={`chip${picking === which ? ' on' : ''}`} aria-pressed={picking === which}
                onClick={() => onPicking(picking === which ? null : which)}>
                {picking === which ? 'Click the globe…' : 'Pick on globe'}
              </button>
            </div>
          ))}
          <p className="micro">Points within 200 km of the sea snap to the nearest navigable water.</p>
        </div>
      )}
      {mode === 'odyssey' && (
        <ol className="voyage-stops">
          {(options?.odyssey ?? []).map((s) => <li key={s.id}><b>{s.name}</b> <span className="micro">{s.episode} · {s.book}</span></li>)}
        </ol>
      )}

      <div className="voyage-fields two">
        <label>Vessel
          <select value={vessel} onChange={(e) => setVessel(e.target.value)}>
            {(options?.vessels ?? []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        </label>
        <label>Speed (kn)
          <input type="number" min={2} max={30} step={0.5} inputMode="decimal" value={speed}
            placeholder={String(options?.vessels.find((v) => v.id === vessel)?.service_kn ?? '')} onChange={(e) => setSpeed(e.target.value)} />
        </label>
        <label className="wide">Departure (your time; blank = now)
          <input type="datetime-local" min={nowLocal} max={maxLocal} value={depart} onChange={(e) => setDepart(e.target.value)} />
        </label>
      </div>
      <div className="voyage-actions">
        <button type="button" className="button" disabled={!ready || status === 'loading'} onClick={plan}>
          {status === 'loading' ? 'Routing…' : 'Plan route'}
        </button>
        {result && <button type="button" className="chip" onClick={clear}>Clear</button>}
      </div>
      {status === 'loading' && <p className="micro" aria-live="polite">Fetching forecasts and searching the corridor; a new ocean crossing takes 5–30 s.</p>}
      {status !== 'idle' && status !== 'loading' && <p className="error" role="alert">{status}</p>}

      {result && (
        <div className="voyage-result" aria-live="polite">
          <dl className="voyage-stats">
            <div><dt>Arrives</dt><dd>{fmtUtc(result.arrive)}</dd><span className="micro">90%: {fmtUtc(result.eta_band.early)} – {fmtUtc(result.eta_band.late)}</span></div>
            <div><dt>Duration</dt><dd>{fmtDuration(result.duration_h)}</dd><span className="micro">{Math.round(result.distance_nm).toLocaleString()} nm</span></div>
            <div><dt>vs shortest route</dt><dd>{result.saved_h >= 0.1 ? `${fmtDuration(result.saved_h)} faster` : 'same'}</dd>
              <span className="micro">shortest path, same forecast</span></div>
            <div><dt>Worst seas</dt><dd>{result.max_hs_m.toFixed(1)} m</dd>
              <span className="micro">{result.hours_above_limit > 0 ? `${fmtDuration(result.hours_above_limit)} above the ${result.vessel.hs_limit_m} m limit` : `limit ${result.vessel.hs_limit_m} m`}</span></div>
          </dl>
          {result.conditions_source.startsWith('Forecast unavailable')
            ? <p className="micro error" role="status">The forecast provider is rate-limiting us, so this route assumes a calm sea. Try again in a minute for weather-aware timing.</p>
            : result.beyond_forecast_pct > 0 && (
            <p className="micro">{Math.round(result.beyond_forecast_pct)}% of the route is past the ~9-day marine forecast; conditions there hold the last forecast.</p>
          )}

          <div className="voyage-scrub">
            <button type="button" className="chip" onClick={() => setPlaying((p) => !p)} aria-pressed={playing}>{playing ? 'Pause' : 'Play'}</button>
            <input type="range" min={0} max={end} step={0.5} value={hour} aria-label="Voyage time"
              onChange={(e) => { setPlaying(false); onHour(Number(e.target.value)) }} />
          </div>
          {here && (
            <p className="micro voyage-here">
              {here.t ? fmtUtc(here.t) : fmtDuration(hour)} · {here.lat.toFixed(1)}, {here.lon.toFixed(1)}
              {here.sog_kn != null && ` · ${here.sog_kn} kn`}{here.wind != null && ` · wind ${here.wind} m/s`}
            </p>
          )}
          <Strip track={track} value={(p) => p.hs} label="Wave height when the ship gets there" unit="m"
            max={Math.max(3, Math.ceil(result.max_hs_m + 0.5))} hour={hour} onHour={onHour} limit={result.vessel.hs_limit_m} color={hsColor(2)} />
          <Strip track={track} value={(p) => p.sog_kn} label="Speed over ground" unit="kn"
            max={Math.ceil(result.vessel.service_kn * 1.3)} hour={hour} onHour={onHour} color="#ffc77a" />

          <div className="voyage-legend" aria-label="Route colour">
            <span className="micro">Route colour: wave height</span>
            <span className="voyage-ramp" aria-hidden="true" />
            <span className="micro voyage-ramp-labels"><span>0 m</span><span>3 m</span><span>6 m+</span></span>
            <span className="micro"><i className="voyage-dash" aria-hidden="true" /> shortest path</span>
          </div>

          {result.legs.length > 1 && (
            <ol className="voyage-legs">
              {result.legs.map((leg) => {
                const startH = (Date.parse(leg.depart) - Date.parse(result.depart)) / 3_600_000
                return (
                  <li key={leg.to.name}>
                    <button type="button" onClick={() => onHour(startH)}>
                      <b>{leg.to.name}</b>
                      <span className="micro">{leg.to.episode ? `${leg.to.episode} · ` : ''}{fmtUtc(leg.arrive)} · {fmtDuration(leg.duration_h)}</span>
                    </button>
                  </li>
                )
              })}
            </ol>
          )}
          {result.legs.some((l) => l.passages.length) && (
            <p className="micro">Via {[...new Set(result.legs.flatMap((l) => l.passages))].map((name) => {
              const hours = result.legs.find((l) => l.passage_hours?.[name] != null)?.passage_hours[name]
              return hours != null ? `${name} (${hours} h transit, not counting queueing)` : name
            }).join(', ')}</p>
          )}

          <details className="voyage-model">
            <summary>How this was routed</summary>
            <p>Each step is costed with the waves, wind and current forecast <b>for the hour the ship reaches it</b>, so a storm that will
              have passed costs nothing and one moving onto the route is avoided.</p>
            <p>Speed in those conditions comes from a SuperLearner (linear, ridge, lasso, random forest and XGBoost, stacked by a ridge
              meta-learner){result.model.cv_r2 != null && <>: cross-validated R² {result.model.cv_r2.toFixed(3)}</>}
              {result.model.rows != null && <>, {result.model.real_rows ? `${result.model.real_rows.toLocaleString()} AIS fixes and ` : ''}{(result.model.rows - (result.model.real_rows ?? 0)).toLocaleString()} physics-prior rows</>}.
              The ETA band is a 90% split-conformal interval (±{result.model.conformal_q90} of speed).</p>
            {result.model.weights && <p className="micro">Weights: {Object.entries(result.model.weights).map(([k, w]) => `${k} ${w}`).join(' · ')}</p>}
            {result.model.note && <p className="micro">{result.model.note}</p>}
          </details>
        </div>
      )}
    </section>
  )
}
