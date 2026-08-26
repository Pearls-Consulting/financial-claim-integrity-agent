import * as React from "react"

import { type AuthUser, fetchMe, login as apiLogin, logout as apiLogout } from "@/lib/auth-api"
import { onUnauthorized } from "@/lib/auth-bus"

/**
 * Authentication state for the app.
 *
 * On mount it probes GET /api/auth/me once to recover an existing session
 * (the cookie survives reloads). It also subscribes to the unauthorized bus,
 * so a 401 from any request drops the user — ProtectedRoute then bounces to
 * /login. State only: this provider never navigates, so it needs no router
 * context. Mirrors the i18n provider/hook shape.
 */

export type AuthStatus = "loading" | "authed" | "anon"

interface AuthContextValue {
  user: AuthUser | null
  status: AuthStatus
  signIn: (email: string, password: string) => Promise<AuthUser>
  signOut: () => Promise<void>
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<AuthUser | null>(null)
  const [status, setStatus] = React.useState<AuthStatus>("loading")

  // Bootstrap: recover an existing session once on mount. The `active` guard
  // keeps a stale StrictMode double-invoke from clobbering the real result.
  React.useEffect(() => {
    let active = true
    fetchMe()
      .then((u) => {
        if (!active) return
        setUser(u)
        setStatus(u ? "authed" : "anon")
      })
      .catch(() => {
        if (active) setStatus("anon")
      })
    return () => {
      active = false
    }
  }, [])

  // A 401 anywhere means the session is gone — drop the user.
  React.useEffect(
    () =>
      onUnauthorized(() => {
        setUser(null)
        setStatus("anon")
      }),
    []
  )

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      signIn: async (email, password) => {
        const u = await apiLogin(email, password)
        setUser(u)
        setStatus("authed")
        return u
      },
      signOut: async () => {
        // Drop the user synchronously (before the first await) so a caller can
        // navigate in the same event handler and React batches both into one
        // render — no frame where LoginPage sees an authed user, or where
        // ProtectedRoute sees anon while still mounted.
        setUser(null)
        setStatus("anon")
        await apiLogout()
      },
    }),
    [user, status]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider")
  return ctx
}
