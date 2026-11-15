import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { RedirectToSignIn, Show, SignInButton, UserButton } from '@clerk/react'
import Splash from './Splash'
import World from './World'
import Search from './Search'
import ModelScores from './ModelScores'

function readGreekMode() {
  try {
    return localStorage.getItem('nostos-greek-mode') === 'true'
  } catch {
    return false
  }
}

function Protected({ children }: { children: ReactNode }) {
  return (
    <Show when="signed-in" fallback={<RedirectToSignIn />}>
      {children}
    </Show>
  )
}

export default function App() {
  const [greekMode, setGreekMode] = useState(readGreekMode)

  useEffect(() => {
    document.documentElement.dataset.greek = String(greekMode)
    try {
      localStorage.setItem('nostos-greek-mode', String(greekMode))
    } catch {
      // The mode still works for this session when storage is unavailable.
    }
  }, [greekMode])

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="nav">
        <NavLink to="/" className="brand" aria-label="Nostos home">
          {greekMode
            ? <><span className="brand-laurel" aria-hidden="true">❧</span><span lang="grc">ΝΟΣΤΟΣ</span><span className="brand-laurel flip" aria-hidden="true">❧</span></>
            : 'Nostos'}
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/world" aria-label="World">
            {greekMode ? <span className="nav-greek"><span lang="grc">ΚΟΣΜΟΣ</span><small>World</small></span> : 'World'}
          </NavLink>
          <NavLink to="/search" aria-label="Search">
            {greekMode ? <span className="nav-greek"><span lang="grc">ΜΑΝΤΕΙΟΝ</span><small>The oracle</small></span> : 'Search'}
          </NavLink>
          <NavLink to="/model" aria-label="Model">
            {greekMode ? <span className="nav-greek"><span lang="grc">ΠΡΟΓΝΩΣΙΣ</span><small>Model</small></span> : 'Model'}
          </NavLink>
        </nav>
        <div className="nav-end">
          <button
            type="button"
            className="greek-toggle"
            aria-label={`Greek mode ${greekMode ? 'on' : 'off'}`}
            aria-pressed={greekMode}
            onClick={() => setGreekMode((mode) => !mode)}
          >
            <span className="greek-toggle-mark" aria-hidden="true">Ω</span>
            <span>Greek mode</span>
            <span className="greek-toggle-state">{greekMode ? 'On' : 'Off'}</span>
          </button>
          <Show
            when="signed-in"
            fallback={
              <SignInButton mode="modal">
                <button className="link">Sign in</button>
              </SignInButton>
            }
          >
            <UserButton />
          </Show>
        </div>
      </header>
      <main id="main">
        <Routes>
          <Route
            path="/"
            element={
              <Show when="signed-in" fallback={<Splash greekMode={greekMode} />}>
                <Navigate to="/world" replace />
              </Show>
            }
          />
          <Route path="/world" element={<Protected><World greekMode={greekMode} /></Protected>} />
          <Route path="/search" element={<Protected><Search greekMode={greekMode} /></Protected>} />
          <Route path="/model" element={<Protected><ModelScores /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  )
}
