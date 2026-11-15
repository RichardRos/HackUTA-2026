import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Full-screen dashboard for one selection. World adds `.world.focused`, which shrinks the globe
 * into the top-left cell. The header card (title + `hero`) sits beside the globe, `aside` takes the
 * last column of that row, and `children` are the cards below, in a grid of equal-size cells.
 */
export default function FocusPanel({ label, kicker, title, hero, aside, onClose, children }: {
  label: string
  kicker: string
  title: ReactNode
  hero?: ReactNode
  aside?: ReactNode
  onClose: () => void
  children?: ReactNode
}) {
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => close.current?.focus({ preventScroll: true }), [])
  return (
    <section className={`focus${aside ? ' has-aside' : ''}`} aria-label={label}>
      <div className="card hero">
        <div className="focus-head">
          <div>
            <p className="kicker">{kicker}</p>
            <h2>{title}</h2>
          </div>
          <button ref={close} className="close" aria-label={`Close ${label}`} onClick={onClose}>×</button>
        </div>
        {hero}
      </div>
      {aside && <div className="card hero-aside">{aside}</div>}
      {children && <div className="focus-cards">{children}</div>}
    </section>
  )
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title && <h3 className="section-title">{title}</h3>}
      <div className="card-body">{children}</div>
    </section>
  )
}
