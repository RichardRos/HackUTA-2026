import { SignInButton } from '@clerk/react'
import { lazy, Suspense } from 'react'
const Earth = lazy(() => import('./Earth'))

export default function Splash({ greekMode = false }: { greekMode?: boolean }) {
  return (
    <div className="splash">
      <div className="splash-copy">
        <p className="kicker">Weather / Markets / World events</p>
        <h1>Weather, markets and world events on one live globe</h1>
        <p className="muted">
          Nostos lines up NOAA forecasts, grid data, prediction markets and the news, so you can see where a
          forecast and a market disagree.
        </p>
        <SignInButton mode="modal" forceRedirectUrl="/world">
          <button className="button">Explore the globe <span aria-hidden="true">↗</span></button>
        </SignInButton>
      </div>
      <Suspense fallback={null}>
        <Earth ambient greekMode={greekMode} />
      </Suspense>
    </div>
  )
}
