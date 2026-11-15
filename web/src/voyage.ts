/** POST /api/voyage/route (voyage/router.py plan). Times UTC ISO, distances nm, heights m. */
export type RoutePoint = {
  lat: number
  lon: number
  hours: number
  t?: string
  hs?: number
  wind?: number
  current_along?: number
  sog_kn?: number
  factor?: number
  beyond_forecast?: boolean
  passage?: string
}
export type Place = { id?: string; name: string; lat: number; lon: number; country?: string; episode?: string; book?: string }
export type Leg = {
  from: Place
  to: Place
  depart: string
  arrive: string
  duration_h: number
  distance_nm: number
  saved_h: number
  max_hs_m: number
  hours_above_limit: number
  beyond_forecast_pct: number
  passages: string[]
  passage_hours: Record<string, number> // fixed transit time of each canal used
  eta_band: { early: string; late: string }
  shortest: { distance_nm: number; duration_h: number; arrive: string }
  path: RoutePoint[]
  shortest_path: { lat: number; lon: number }[]
}
export type Voyage = {
  mode: 'points' | 'ports' | 'odyssey'
  vessel: { id: string; label: string; service_kn: number; hs_limit_m: number }
  depart: string
  arrive: string
  duration_h: number
  distance_nm: number
  saved_h: number
  max_hs_m: number
  hours_above_limit: number
  beyond_forecast_pct: number
  eta_band: { early: string; late: string }
  stops: Place[]
  legs: Leg[]
  model: { trained_at?: string; rows?: number; real_rows?: number; cv_r2?: number; cv_rmse?: number
    weights?: Record<string, number>; conformal_q90?: number; note?: string }
}
export type VoyageOptions = {
  ports: Place[]
  odyssey: Place[]
  vessels: { id: string; label: string; service_kn: number; hs_limit_m: number }[]
}

/** Whole voyage as one track in voyage hours (legs offset by their departure). */
export function flatten(v: Voyage): RoutePoint[] {
  const start = Date.parse(v.depart)
  return v.legs.flatMap((leg) => {
    const offset = (Date.parse(leg.depart) - start) / 3_600_000
    return leg.path.map((p) => ({ ...p, hours: p.hours + offset }))
  })
}

/** Position and conditions at voyage hour `h`, interpolated along the track. */
export function at(track: RoutePoint[], h: number): RoutePoint | null {
  if (!track.length) return null
  if (h <= track[0].hours) return track[0]
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1]
    const b = track[i]
    if (h > b.hours) continue
    if (b.hours === a.hours) return b // in port between legs
    const f = (h - a.hours) / (b.hours - a.hours)
    let dlon = b.lon - a.lon
    if (dlon > 180) dlon -= 360
    if (dlon < -180) dlon += 360
    return { ...b, lat: a.lat + (b.lat - a.lat) * f, lon: ((a.lon + dlon * f + 540) % 360) - 180, hours: h }
  }
  return track[track.length - 1]
}

// Wave height along the route: the wave layer's scale, calm teal -> white -> storm amber.
const STOPS: [number, [number, number, number]][] = [[0, [61, 189, 201]], [3, [230, 244, 246]], [6, [255, 155, 89]]]
export function hsColor(hs: number | undefined) {
  const v = Math.max(0, Math.min(6, hs ?? 0))
  const k = v <= 3 ? 0 : 1
  const [h0, c0] = STOPS[k]
  const [h1, c1] = STOPS[k + 1]
  const f = (v - h0) / (h1 - h0)
  return `rgb(${c0.map((c, i) => Math.round(c + (c1[i] - c) * f)).join(',')})`
}

export const fmtUtc = (iso: string) => {
  const d = new Date(iso)
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' })} ${d.toISOString().slice(11, 16)} UTC`
}
export const fmtDuration = (h: number) => (h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)} h` : `${Math.floor(h / 24)} d ${Math.round(h % 24)} h`)
