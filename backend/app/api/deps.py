"""Request-time auth dependency.

`require_session` reads the session cookie, verifies the JWT, and returns the
signed-in user — or 401. Applied to the whole `/api` router (see routes.py) so
every claim/document endpoint is protected by one line; `/health` stays public
for the deploy health-check and `/api/auth/*` carries its own rules.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from app.core.config import get_settings
from app.core.security import decode_access_token, rotate_if_stale


@dataclass(frozen=True)
class CurrentUser:
    email: str
    display_name: str
    display_name_ar: str


def configured_user() -> CurrentUser:
    """The single account this deployment signs in (from settings)."""
    s = get_settings()
    return CurrentUser(
        email=s.auth_email.strip().lower(),
        display_name=s.auth_name,
        display_name_ar=s.auth_name_ar,
    )


def set_session_cookie(response: Response, token: str) -> None:
    """Write the session cookie (login and the sliding re-issue share this)."""
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_idle_hours * 3600,
        httponly=True,
        samesite="lax",  # blocks cross-site POST — CSRF mitigation
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().session_cookie_name, path="/")


def require_session(request: Request, response: Response) -> CurrentUser:
    """Resolve the signed-in user from the session cookie, or 401.

    Slides the session forward: past the token's half-life a fresh cookie is
    written onto this response, so an active reviewer never has to log back in
    until the absolute cap.
    """
    token = request.cookies.get(get_settings().session_cookie_name)
    payload = decode_access_token(token or "")
    user = configured_user()
    # The subject must still be the configured account — changing AUTH_EMAIL
    # invalidates old sessions the same way rotating the secret does.
    if not payload or payload.get("sub") != user.email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    rotated = rotate_if_stale(payload)
    if rotated:
        set_session_cookie(response, rotated)
    return user
