import { useState, type CSSProperties, type ReactNode } from 'react'

export type Layer = {
  id: string
  label: string
  icon: ReactNode // small mark in the layer's own colour, also shown on the collapsed rail
  color: string // switch colour when on
  on: boolean
  onChange: (on: boolean) => void
  note?: ReactNode
  extra?: ReactNode // controls shown under the layer while it is on
}

const KEY = 'nostos.layers.open'

function readOpen() {
  try {
    return localStorage.getItem(KEY) !== '0'
  } catch {
    return true
  }
}

/** Collapsible layers card: switch per layer, folding to a slim icon rail. */
export default function LayerMenu({ layers }: { layers: Layer[] }) {
  const [open, setOpenState] = useState(readOpen)
  const setOpen = (v: boolean) => {
    setOpenState(v)
    try {
      localStorage.setItem(KEY, v ? '1' : '0')
    } catch {
      /* storage blocked: the menu still works, it just won't remember */
    }
  }

  if (!open)
    return (
      <nav className="layers rail" aria-label="Map layers">
        <button className="rail-toggle" aria-label="Open layers menu" aria-expanded="false" onClick={() => setOpen(true)}>
          <span aria-hidden="true">›</span>
        </button>
        {layers.map((l) => (
          <button key={l.id} className={`rail-layer${l.on ? ' on' : ''}`} role="switch" aria-checked={l.on}
            aria-label={l.label} title={l.label} onClick={() => l.onChange(!l.on)} style={{ '--layer': l.color } as CSSProperties}>
            {l.icon}
          </button>
        ))}
      </nav>
    )

  return (
    <nav className="layers" aria-label="Map layers">
      <div className="layers-head">
        <div>
          <p className="kicker">Layers</p>
          <p className="micro">Each one on its own</p>
        </div>
        <button className="rail-toggle" aria-label="Collapse layers menu" aria-expanded="true" onClick={() => setOpen(false)}>
          <span aria-hidden="true">‹</span>
        </button>
      </div>
      <ul>
        {layers.map((l) => (
          <li key={l.id} style={{ '--layer': l.color } as CSSProperties}>
            <div className="layer-row">
              <span className="layer-icon" aria-hidden="true">{l.icon}</span>
              <span className="layer-text">
                <span id={`layer-${l.id}`}>{l.label}</span>
                {l.note && <span className="micro">{l.note}</span>}
              </span>
              <button className="switch" role="switch" aria-checked={l.on} aria-labelledby={`layer-${l.id}`}
                onClick={() => l.onChange(!l.on)}>
                <span className="knob" />
              </button>
            </div>
            {l.on && l.extra}
          </li>
        ))}
      </ul>
    </nav>
  )
}
