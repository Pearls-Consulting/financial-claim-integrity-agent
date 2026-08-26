/**
 * Auth API — login, the session probe, and logout.
 *
 * Deliberately plain fetch, NOT the shared api.ts error path, so a 401 here
 * never trips the global unauthorized bus: GET /me is just probing for an
 * existing session, and a failed login shows an inline message rather than a
 * redirect. Requests are same-origin (Vite proxy in dev, one nginx vhost in
 * prod), so the httpOnly session cookie rides along — no Authorization header.
 */

export interface AuthUser {
  email: string
  displayName: string
  displayNameAr: string
}

interface UserOut {
  email: string
  display_name: string
  display_name_ar: string
}

function toUser(u: UserOut): AuthUser {
  return { email: u.email, displayName: u.display_name, displayNameAr: u.display_name_ar }
}

/** Probe the current session. Returns the user, or null when not signed in. */
export async function fetchMe(signal?: AbortSignal): Promise<AuthUser | null> {
  const res = await fetch("/api/auth/me", { signal })
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`Session check failed (${res.status})`)
  return toUser(await res.json())
}

/** Sign in with email + password. Throws Error(detail) on bad credentials. */
export async function login(email: string, password: string): Promise<AuthUser> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    let detail = "Invalid email or password"
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* keep default */
    }
    throw new Error(detail)
  }
  return toUser(await res.json())
}

/** Clear the session cookie. Idempotent. */
export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" })
}
