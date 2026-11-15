import { useCallback } from 'react'
import { useAuth } from '@clerk/react'

/** fetch for /api routes, carrying the Clerk session token the API verifies. */
export function useApi() {
  const { getToken } = useAuth()
  return useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const res = await fetch(`/api${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${await getToken()}`,
          ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        },
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail
        throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
      }
      return res.json()
    },
    [getToken],
  )
}
