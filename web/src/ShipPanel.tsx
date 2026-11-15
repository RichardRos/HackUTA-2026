import { useEffect, useState } from 'react'
import { useApi } from './api'
import type { TrackPoint } from './Earth'
import FocusPanel, { Card } from './FocusPanel'

type Ship = {
  mmsi: number
  name?: string
  imo?: number | null
  callsign?: string | null
  flag: string
  category: string
  type?: string
  hazard?: string | null
  destination?: string | null
  eta?: string | null
  speed_kn?: number | null
  course?: number | null
  heading?: number | null
  status?: string | null
  length_m?: number | null
  beam_m?: number | null
  draught_m?: number | null
  lat?: number
  lon?: number
  last_heard_s?: number
  live?: boolean
  first_seen?: string
  stored_at?: string
}

type Track = { positions: (TrackPoint & { time: string | null })[] }

const ago = (s: number) => (s < 60 ? `${s}s ago` : `${Math.round(s / 60)} min ago`)

export default function ShipPanel({ mmsi, onClose, onTrack }: { mmsi: number; onClose: () => void; onTrack?: (t: TrackPoint[]) => void }) {
  const api = useApi()
  const [ship, setShip] = useState<Ship | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fixes, setFixes] = useState<number | null>(null)

  useEffect(() => {
    let live = true
    api<Track>(`/ships/${mmsi}/track`)
      .then((t) => {
        if (!live) return
        setFixes(t.positions.filter((x) => !("live" in x)).length)
        onTrack?.(t.positions)
      })
      .catch(() => live && setFixes(null))
    return () => {
      live = false
      onTrack?.([])
    }
  }, [api, mmsi, onTrack])

  useEffect(() => {
    let live = true
    const load = () =>
      api<Ship>(`/ships/${mmsi}`)
        .then((s) => live && setShip(s))
        .catch((e: Error) => live && setError(e.message))
    load()
    const timer = setInterval(load, 15000) // positions arrive every few seconds; refresh the card
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, mmsi])

  const rows: [string, string | number | null | undefined][] = ship
    ? [
        ['Type', ship.type ?? ship.category],
        ['Cargo hazard', ship.hazard ?? (ship.category === 'cargo' || ship.category === 'tanker' ? 'none declared' : null)],
        ['Flag', ship.flag],
        ['Destination', ship.destination],
        ['ETA', ship.eta],
        ['Status', ship.status],
        ['Speed', ship.speed_kn != null ? `${ship.speed_kn} kn` : null],
        ['Course', ship.course != null ? `${Math.round(ship.course)}°` : null],
        ['Size', ship.length_m ? `${ship.length_m} × ${ship.beam_m ?? '?'} m` : null],
        ['Draught', ship.draught_m ? `${ship.draught_m} m` : null],
        ['MMSI', ship.mmsi],
        ['IMO', ship.imo],
        ['Call sign', ship.callsign],
        ['Position', ship.lat != null ? `${ship.lat.toFixed(3)}, ${ship.lon?.toFixed(3)}` : null],
      ]
    : []

  const v = (x: unknown) => x != null && x !== ''
  const tiles: [string, string | number | null | undefined][] = ship
    ? [
        ['Type', ship.type ?? ship.category],
        ['Speed', ship.speed_kn != null ? `${ship.speed_kn} kn` : null],
        ['Course', ship.course != null ? `${Math.round(ship.course)}°` : null],
        ['Destination', ship.destination],
        ['ETA', ship.eta],
        ['Flag', ship.flag],
      ]
    : []
  const vessel = rows.filter(([k]) => ['Cargo hazard', 'Status', 'Size', 'Draught'].includes(k))
  const identity = rows.filter(([k]) => ['MMSI', 'IMO', 'Call sign', 'Position'].includes(k))
  const facts = (list: typeof rows) => !ship ? <p className="muted">{error ? 'Unavailable' : 'Loading…'}</p> : (
    <dl className="facts">
      {list.filter(([, x]) => v(x)).map(([k, x]) => <div key={k}><dt>{k}</dt><dd>{x}</dd></div>)}
    </dl>
  )

  // aside and cards render from the first frame so the grid never reflows when the fetch lands
  const voyage = (
    <>
      <h3 className="section-title">Voyage</h3>
      {!ship ? <p className="muted">{error ? 'Unavailable' : 'Loading…'}</p> : <>
      <p className="big">{ship.live === false ? 'Not heard live' : `Heard ${ago(ship.last_heard_s ?? 0)}`}</p>
      {ship.live === false && ship.stored_at && <p className="micro">Last stored {new Date(ship.stored_at).toLocaleString()}</p>}
      {ship.first_seen && <p className="muted">Tracked since {new Date(ship.first_seen).toLocaleDateString()}</p>}
      {fixes != null && <p className="muted">{fixes} stored position{fixes === 1 ? '' : 's'}, drawn in amber on the globe</p>}
      </>}
      <p className="micro">Source: aisstream.io</p>
    </>
  )

  return (
    <FocusPanel
      label="Ship details"
      kicker="Vessel / live AIS"
      title={ship ? ship.name ?? `MMSI ${ship.mmsi}` : error ? 'Vessel unavailable' : 'Loading vessel…'}
      onClose={onClose}
      aside={voyage}
      hero={
        <>
          {error && <p className="error" role="alert">{error}</p>}
          {ship && (
            <ul className="tiles">
              {tiles.filter(([, x]) => v(x)).map(([k, x]) => (
                <li key={k}><span className="section-title">{k}</span><span className="tile-value">{x}</span></li>
              ))}
            </ul>
          )}
          {ship?.category === 'unknown' && <p className="micro">Type and destination arrive every few minutes over AIS.</p>}
        </>
      }
    >
      <Card title="Vessel">{facts(vessel)}</Card>
      <Card title="Identity">{facts(identity)}</Card>
    </FocusPanel>
  )
}
