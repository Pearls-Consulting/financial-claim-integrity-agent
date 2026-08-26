import { Navigate, Outlet, useLocation } from "react-router-dom"
import { Loader2 } from "lucide-react"

import { useAuth } from "@/lib/auth-context"

/**
 * Gate a group of routes behind a signed-in session (single role, so no
 * allow-list — the prequal agent's per-role bounce is not needed here).
 *
 *  - loading → spinner while the session probe is in flight
 *  - anon    → redirect to /login, preserving the intended destination
 */
export function ProtectedRoute() {
  const { user, status } = useAuth()
  const location = useLocation()

  if (status === "loading") {
    return (
      <div className="bg-background grid min-h-svh place-items-center">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    )
  }

  if (status === "anon" || !user) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  return <Outlet />
}
