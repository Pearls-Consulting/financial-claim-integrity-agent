"""The single-account cookie session: login / probe / logout / gate."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

pytestmark = pytest.mark.real_auth

EMAIL = "reviewer@sdb.local"
PASSWORD = "demo-pass-123"


@pytest.fixture(autouse=True)
def _account(monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL", EMAIL)
    monkeypatch.setenv("AUTH_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _login(client: TestClient, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_api_is_closed_without_a_session():
    client = TestClient(app)
    assert client.get("/api/claims").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    # The deploy health-check must stay public.
    assert client.get("/health").status_code == 200


def test_login_sets_cookie_and_opens_the_api():
    client = TestClient(app)
    res = _login(client)
    assert res.status_code == 200
    assert res.json()["email"] == EMAIL
    cookie = get_settings().session_cookie_name
    assert cookie in res.cookies
    set_cookie = res.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie

    assert client.get("/api/auth/me").json()["email"] == EMAIL
    assert client.get("/api/claims").status_code == 200


def test_email_is_case_insensitive():
    client = TestClient(app)
    assert _login(client, email="  Reviewer@SDB.local ").status_code == 200


@pytest.mark.parametrize(
    "email,password",
    [(EMAIL, "wrong"), ("other@sdb.local", PASSWORD), ("", ""), (EMAIL, "")],
)
def test_bad_credentials_are_one_generic_401(email, password):
    client = TestClient(app)
    res = _login(client, email=email, password=password)
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password"


def test_blank_configured_password_disables_login(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()
    client = TestClient(app)
    assert _login(client, password="").status_code == 401


def test_logout_clears_the_session():
    client = TestClient(app)
    _login(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_or_foreign_token_is_rejected():
    client = TestClient(app)
    s = get_settings()
    forged = jwt.encode(
        {"sub": EMAIL, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "not-the-secret-not-the-secret-not-the-secret",
        algorithm="HS256",
    )
    client.cookies.set(s.session_cookie_name, forged)
    assert client.get("/api/auth/me").status_code == 401

    # Signed with the right secret but for a different subject.
    other = jwt.encode(
        {"sub": "someone@else", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        s.jwt_secret,
        algorithm="HS256",
    )
    client.cookies.set(s.session_cookie_name, other)
    assert client.get("/api/auth/me").status_code == 401


def test_stale_session_slides_forward():
    """Past its half-life a valid session is re-issued on the response."""
    client = TestClient(app)
    s = get_settings()
    now = datetime.now(timezone.utc)
    old = jwt.encode(
        {
            "sub": EMAIL,
            "iat": now - timedelta(hours=s.session_idle_hours * 0.75),
            "exp": now + timedelta(hours=1),
            "auth_time": int((now - timedelta(hours=1)).timestamp()),
        },
        s.jwt_secret,
        algorithm="HS256",
    )
    client.cookies.set(s.session_cookie_name, old)
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    assert "set-cookie" in res.headers
    fresh = jwt.decode(res.cookies[s.session_cookie_name], s.jwt_secret, algorithms=["HS256"])
    assert fresh["auth_time"] == int((now - timedelta(hours=1)).timestamp())
