/**
 * Unauthorized (session-expired) event bus.
 *
 * The API layer can't navigate, so when any request comes back 401 it emits
 * here (see lib/api.ts). The AuthProvider subscribes, drops the user, and the
 * route guard bounces to /login on the next render. Module-level singleton —
 * there is one auth context for the app. Same pattern as the prequal agent.
 */

type Listener = () => void

const listeners = new Set<Listener>()

/** Signal that a request was rejected as unauthenticated (the session is gone). */
export function emitUnauthorized(): void {
  for (const l of [...listeners]) l()
}

/** Subscribe to unauthorized signals (the AuthProvider). Returns an unsubscribe. */
export function onUnauthorized(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
