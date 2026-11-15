import { SignInButton } from '@clerk/react'
import { lazy, Suspense } from 'react'
import GreekSky from './greek/GreekSky'
import IonicColumn from './greek/IonicColumn'
const Earth = lazy(() => import('./Earth'))

export default function Splash({ greekMode = false }: { greekMode?: boolean }) {
  return (
    <div className="splash">
      {greekMode && (
        <>
          <GreekSky />
          <IonicColumn side="left" />
          <IonicColumn side="right" />
        </>
      )}
      <div className="splash-copy">
        {greekMode ? (
          <>
            <p className="splash-epigraph" lang="grc">ἄνδρα μοι ἔννεπε, Μοῦσα, πολύτροπον, ὃς μάλα πολλὰ πλάγχθη</p>
            <p className="splash-translation">“Tell me, Muse, of the man of many turns, who wandered far and wide” · Odyssey 1.1–2</p>
            <p className="kicker">Ships / Tides / Wind / Waves</p>
            <h1>Every ship on the <span className="nowrap">wine-dark</span> sea</h1>
            <p className="muted">
              <span lang="grc">Νόστος</span> is Homer’s word for the voyage home. Live vessels sail as triremes, with Odysseus’
              route from Troy to Ithaca and the monsters he met along the way.
            </p>
          </>
        ) : (
          <>
            <p className="kicker">Weather / Markets / World events</p>
            <h1>Weather, markets and world events on one live globe</h1>
            <p className="muted">
              Nostos lines up NOAA forecasts, grid data, prediction markets and the news, so you can see where a
              forecast and a market disagree.
            </p>
          </>
        )}
        <SignInButton mode="modal" forceRedirectUrl="/world">
          <button className="button">{greekMode ? 'Set sail' : 'Explore the globe'} <span aria-hidden="true">↗</span></button>
        </SignInButton>
      </div>
      <Suspense fallback={null}>
        <Earth ambient greekMode={greekMode} />
      </Suspense>
    </div>
  )
}
