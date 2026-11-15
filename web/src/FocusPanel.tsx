import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Full-screen dashboard for one selection. World adds `.world.focused`, which shrinks the globe
 * into the top-left cell. The header card (title + `hero`) sits beside the globe, `aside` takes the
 * last column of that row, and `children` are the cards below, so everything is visible at once.
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
      {children}
    </section>
  )
}

export function Card({ title, span = 1, children }: { title?: string; span?: 1 | 2 | 3 | 4; children: ReactNode }) {
  return (
    <section className={`card span-${span}`}>
      {title && <h3 className="section-title">{title}</h3>}
      <div className="card-body">{children}</div>
    </section>
  )
}
