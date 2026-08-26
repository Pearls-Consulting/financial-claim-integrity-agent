"""Authentication primitives — password hashing and session tokens.

Lifted from the prequalification agent (app/core/security.py) minus the role
model: this deployment has a single reviewer login. Pure crypto with no FastAPI
imports, so it is unit-testable on its own; the request-time glue (reading the
cookie, enforcing the session) lives in app/api/deps.py.

Sessions are stateless JWTs (HS256) signed with `settings.jwt_secret` and
carried in an httpOnly cookie. They slide: an active session is silently
re-issued (`rotate_if_stale`) within an idle window, up to an absolute cap.
Stateless = nothing server-side to revoke before expiry — rotate JWT_SECRET to
invalidate every session at once.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import bcrypt
import jwt

from app.core.config import get_settings

_JWT_ALG = "HS256"


# --- passwords -------------------------------------------------------------

def hash_password(plain: str) -> str:
    """bcrypt hash of `plain`."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """True if `plain` matches the stored bcrypt hash. Never raises."""
    if not plain or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


@lru_cache
def _configured_password_hash(password: str) -> str:
    """Hash the configured AUTH_PASSWORD once per process so every login
    attempt goes through the same bcrypt compare a stored hash would."""
    return hash_password(password) if password else ""


def verify_login(email: str, password: str) -> bool:
    """True when `email` + `password` match the single configured account.

    Email is compared case-insensitively; a blank AUTH_PASSWORD disables login
    outright rather than accepting an empty password.
    """
    s = get_settings()
    if not s.auth_password:
        return False
    email_ok = (email or "").strip().lower() == s.auth_email.strip().lower()
    # Always run the bcrypt compare so a wrong email costs the same as a wrong
    # password (no timing tell on which half was wrong).
    password_ok = verify_password(password, _configured_password_hash(s.auth_password))
    return email_ok and password_ok


# --- session tokens --------------------------------------------------------

def create_access_token(sub: str, auth_time: Optional[datetime] = None) -> str:
    """Sign a session JWT for `sub`.

    `exp` is `now + session_idle_hours` (the sliding window), clamped so it never
    outlives the absolute cap measured from `auth_time` — the original login,
    carried forward unchanged across re-issues.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    anchor = auth_time or now
    exp = now + timedelta(hours=settings.session_idle_hours)
    if settings.session_absolute_hours > 0:
        exp = min(exp, anchor + timedelta(hours=settings.session_absolute_hours))
    payload = {
        "sub": sub,
        "iat": now,
        "exp": exp,
        "auth_time": int(anchor.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALG)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode + verify a session JWT. None if missing, expired, or tampered."""
    if not token:
        return None
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[_JWT_ALG])
    except jwt.InvalidTokenError:
        return None


def rotate_if_stale(payload: dict) -> Optional[str]:
    """A fresh token to re-issue if this session should slide forward, else None.

    Only once the current token is past its half-life (so we don't set a cookie
    on every request), and never past the absolute cap. Preserves the original
    `auth_time` anchor so the cap keeps counting from the real login.
    """
    settings = get_settings()
    iat = payload.get("iat")
    if iat is None:
        return None
    auth_time = payload.get("auth_time")
    now = datetime.now(timezone.utc).timestamp()

    if (
        settings.session_absolute_hours > 0
        and auth_time is not None
        and now >= auth_time + settings.session_absolute_hours * 3600
    ):
        return None
    if now - iat < settings.session_idle_hours * 3600 / 2:
        return None

    anchor = datetime.fromtimestamp(auth_time, tz=timezone.utc) if auth_time is not None else None
    return create_access_token(payload["sub"], auth_time=anchor)
