/* eslint-disable react-refresh/only-export-components */
// Local dev/test image only (VITE_DEV_AUTH_BYPASS=1): stands in for @clerk/react so Playwright can drive
// the app signed in. Production builds alias the real package; the API pairs this with DEV_AUTH_BYPASS.
import type { ReactNode } from 'react'

export const ClerkProvider = ({ children }: { children: ReactNode }) => <>{children}</>
export const Show = ({ when, children }: { when: string; children: ReactNode; fallback?: ReactNode }) => <>{when === 'signed-in' ? children : null}</>
export const SignInButton = ({ children }: { children?: ReactNode }) => <>{children}</>
export const UserButton = () => <span className="micro">dev</span>
export const RedirectToSignIn = () => null
const auth = { getToken: async () => 'dev' } // one stable object, like Clerk's: a new getToken per render refired every fetch effect
export const useAuth = () => auth
