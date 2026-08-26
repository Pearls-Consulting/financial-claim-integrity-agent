"""Authentication endpoints — login, the session probe, and logout.

Same shape as the prequalification agent's /api/auth: login sets the httpOnly
session cookie, logout clears it, GET /me is the frontend's bootstrap probe.
Requests are same-origin (Vite proxy in dev, one nginx vhost in prod), so the
browser attaches the cookie itself — no Authorization header anywhere.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import (
    CurrentUser,
    clear_session_cookie,
    configured_user,
    require_session,
    set_session_cookie,
)
from app.core.security import create_access_token, verify_login

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    email: str
    display_name: str
    display_name_ar: str


def _user_out(user: CurrentUser) -> UserOut:
    return UserOut(
        email=user.email,
        display_name=user.display_name,
        display_name_ar=user.display_name_ar,
    )


@router.post("/login", response_model=UserOut)
def login(req: LoginRequest, response: Response) -> UserOut:
    """Validate credentials, set the session cookie, return the user.

    One generic 401 for unknown email / wrong password / login disabled — never
    reveal which.
    """
    if not verify_login(req.email, req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    user = configured_user()
    set_session_cookie(response, create_access_token(sub=user.email))
    return _user_out(user)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(require_session)) -> UserOut:
    """The current session's user. 401 when there is no valid session."""
    return _user_out(user)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    """Clear the session cookie. Idempotent — safe when not logged in."""
    clear_session_cookie(response)
    return {"ok": True}
