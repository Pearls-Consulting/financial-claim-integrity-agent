"""Tests always run on the deterministic mock engines, regardless of what the
developer's .env selects — no network, no Azure spend, reproducible."""

import os

os.environ["EXTRACTOR_ENGINE"] = "mock"
os.environ["JUDGE_ENGINE"] = "mock"
os.environ["ERP_SOURCE"] = "mock"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Each test gets a throwaway SQLite file — tests must never write runs or
    submissions into the developer's live backend/data/claims.db."""
    from app.services import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "claims.db")
    monkeypatch.setattr(store, "_conn", None)
    yield


@pytest.fixture(autouse=True)
def _signed_in(request):
    """Every /api endpoint requires a session cookie. Pipeline/extraction tests
    are about the gates, not the login, so the session dependency is overridden
    with the configured user. Tests marked `real_auth` (tests/test_auth.py)
    exercise the real cookie flow instead."""
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    from app.api.deps import configured_user, require_session
    from app.main import app

    app.dependency_overrides[require_session] = configured_user
    yield
    app.dependency_overrides.pop(require_session, None)
