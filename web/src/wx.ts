/** GET /api/wx/forecast/tomorrow (model/predict_live.py, nightly): tomorrow's high per city, °F. */
export type Tomorrow = { location: string; station_id: string; climate_date: string; issued_at: string; nostos_f: number | null; nbm_corrected_f: number | null; nbm_f: number | null }

export const notLiveYet = (error?: string) => !!error && /404|not found/i.test(error) // endpoint ships with the model team's PR
export const madeAt = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
export const cityName = (loc: string) => loc.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
export const f1 = (v: number | null) => (v === null || !Number.isFinite(v) ? '–' : v.toFixed(1))
