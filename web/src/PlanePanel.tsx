import FocusPanel, { Card } from './FocusPanel'

type Aircraft = {
  hex: string
  callsign: string | null
  lat: number
  lon: number
  altitude_ft: number | null
  on_ground: boolean
  ground_speed_kt: number | null
  track_deg: number | null
  origin_country: string | null
  cargo_operator: string | null
  seen_seconds_ago: number | null
}

const ago = (s: number) => (s < 60 ? `${s}s ago` : `${Math.round(s / 60)} min ago`)
const value = (x: unknown) => x != null && x !== ''

export default function PlanePanel({ hex, aircraft, onClose }: { hex: string; aircraft: Aircraft | null; onClose: () => void }) {
  const facts: [string, string | number | null | undefined][] = aircraft
    ? [
        ['ICAO hex', aircraft.hex],
        ['Origin country', aircraft.origin_country],
        ['Cargo operator', aircraft.cargo_operator],
        ['Position', `${aircraft.lat.toFixed(3)}, ${aircraft.lon.toFixed(3)}`],
        ['Last seen', aircraft.seen_seconds_ago != null ? ago(aircraft.seen_seconds_ago) : null],
        ['Flight state', aircraft.on_ground ? 'On the ground' : 'Airborne'],
        ['Cargo state', aircraft.cargo_operator ? 'Cargo' : 'Passenger / other'],
      ]
    : []
  const tiles: [string, string | number | null | undefined][] = aircraft
    ? [
        ['Altitude', aircraft.altitude_ft != null ? `${aircraft.altitude_ft.toLocaleString()} ft` : null],
        ['Ground speed', aircraft.ground_speed_kt != null ? `${Math.round(aircraft.ground_speed_kt)} kt` : null],
        ['Track', aircraft.track_deg != null ? `${Math.round(aircraft.track_deg)}°` : null],
      ]
    : []

  return (
    <FocusPanel
      label="Flight details"
      kicker="Aircraft / live ADS-B"
      title={aircraft?.callsign?.trim() || `ICAO ${hex.toUpperCase()}`}
      onClose={onClose}
      aside={
        <>
          <h3 className="section-title">Flight status</h3>
          <p className="big">{aircraft ? aircraft.on_ground ? 'On the ground' : 'Airborne' : 'Not in the latest snapshot'}</p>
          {aircraft?.seen_seconds_ago != null && <p className="muted">Last seen {ago(aircraft.seen_seconds_ago)}</p>}
          <p className="micro">Source: OpenSky Network</p>
        </>
      }
      hero={
        aircraft ? (
          <ul className="tiles">
            {tiles.filter(([, x]) => value(x)).map(([k, x]) => (
              <li key={k}><span className="section-title">{k}</span><span className="tile-value">{x}</span></li>
            ))}
          </ul>
        ) : <p className="muted">This flight is no longer in the latest global snapshot.</p>
      }
    >
      <Card title="Aircraft">
        {aircraft ? (
          <dl className="facts">
            {facts.filter(([, x]) => value(x)).map(([k, x]) => <div key={k}><dt>{k}</dt><dd>{x}</dd></div>)}
          </dl>
        ) : <p className="muted">Not in the latest snapshot.</p>}
      </Card>
    </FocusPanel>
  )
}
