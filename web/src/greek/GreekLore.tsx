import { useEffect, useMemo, useRef } from 'react'
import { medallionSvg } from './art'
import { greekNumeral, MONSTERS, STATIONS, type LoreEntry } from './myth'

/**
 * The legend of a monster or one stop of the Odyssey, on a parchment scroll. Previous / next walk the
 * bestiary (or the voyage) in order, and the globe flies along with them.
 */
export default function GreekLore({ entry, onPick, onClose }: {
  entry: LoreEntry
  onPick: (entry: LoreEntry) => void
  onClose: () => void
}) {
  const list: LoreEntry[] = entry.kind === 'monster' ? MONSTERS : STATIONS
  const i = Math.max(0, list.findIndex((e) => e.id === entry.id))
  const prev = list[(i - 1 + list.length) % list.length]
  const next = list[(i + 1) % list.length]
  const art = useMemo(() => (entry.kind === 'monster' ? medallionSvg(entry.id, 'card') : null), [entry])
  const close = useRef<HTMLButtonElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  // Block bodies: newer browsers return a Promise from scroll methods, which React would take for a cleanup.
  useEffect(() => {
    close.current?.focus({ preventScroll: true })
  }, [])
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = 0
  }, [entry])

  return (
    <aside className="lore" aria-label={`${entry.name}: ${entry.kind === 'monster' ? 'legend' : 'episode'}`}>
      <div className="lore-scroll" ref={scroll}>
        <div className="lore-head">
          <p className="lore-kicker">
            {entry.kind === 'monster' ? 'Bestiary' : 'Nostos · the voyage home'}
            <span lang="grc"> {greekNumeral(i + 1)}</span> of {list.length}
          </p>
          <button ref={close} type="button" className="lore-close" aria-label="Close the legend" onClick={onClose}>×</button>
        </div>

        {art
          ? <div className="lore-art" dangerouslySetInnerHTML={{ __html: art }} />
          : <div className="lore-coin" aria-hidden="true"><span lang="grc">{greekNumeral(i + 1)}</span></div>}

        <p className="lore-greek" lang="grc">{entry.greek}</p>
        <h2>{entry.name}</h2>
        <p className="lore-epithet">
          {entry.kind === 'monster'
            ? <>{entry.epithet} · <i>{entry.translit}</i></>
            : <>Homer, {entry.book}</>}
        </p>
        <p className="lore-text">{entry.lore}</p>

        {entry.kind === 'monster' && (
          <dl className="lore-facts">
            <div><dt>Haunt</dt><dd>{entry.place}</dd></div>
            <div><dt>Vanquished by</dt><dd>{entry.foe}</dd></div>
            <div><dt>Sources</dt><dd>{entry.source}</dd></div>
          </dl>
        )}

        <nav className="lore-nav" aria-label={entry.kind === 'monster' ? 'Other monsters' : 'Other stops'}>
          <button type="button" onClick={() => onPick(prev)}><span aria-hidden="true">‹</span> {prev.name}</button>
          <button type="button" onClick={() => onPick(next)}>{next.name} <span aria-hidden="true">›</span></button>
        </nav>
      </div>
    </aside>
  )
}
