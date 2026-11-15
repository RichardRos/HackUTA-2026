import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

export type Layer = {
  id: string
  label: string
  icon: ReactNode // small mark in the layer's own colour
  color: string // chip outline colour when on
  on: boolean
  onChange: (on: boolean) => void
  error?: string | null
}

/** Compact multiselect of map layers; closes with × or a click anywhere outside it. */
export default function LayerMenu({ layers }: { layers: Layer[] }) {
  const [open, setOpen] = useState(true)
  const box = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    addEventListener('pointerdown', away)
    addEventListener('keydown', esc)
    return () => {
      removeEventListener('pointerdown', away)
      removeEventListener('keydown', esc)
    }
  }, [open])

  if (!open)
    return (
      <button className="layers-open button" aria-expanded="false" onClick={() => setOpen(true)}>
        Layers · {layers.filter((l) => l.on).length}/{layers.length}
      </button>
    )

  return (
    <nav className="layers" aria-label="Map layers" ref={box}>
      <div className="layers-head">
        <p className="kicker">Layers</p>
        <button className="close" aria-label="Close layers" onClick={() => setOpen(false)}>×</button>
      </div>
      <div className="layer-chips">
        {layers.map((l) => (
          <button key={l.id} type="button" className={`layer-chip${l.on ? ' on' : ''}${l.error ? ' failed' : ''}`}
            aria-pressed={l.on} title={l.error ?? undefined} onClick={() => l.onChange(!l.on)}
            style={{ '--layer': l.color } as CSSProperties}>
            <span className="layer-icon" aria-hidden="true">{l.icon}</span>
            {l.label}
            {l.error && <span className="sr-only">: {l.error}</span>}
          </button>
        ))}
      </div>
    </nav>
  )
}
