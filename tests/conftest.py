import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Each test gets its own database, mock mode, and no Claude key."""
    monkeypatch.setenv("DISPUTEDESK_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)

    from app import db
    from app.paypal import reset_client

    reset_client()
    db.init_db()
    db.reset_db()
    yield
    reset_client()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def paypal():
    from app.paypal import get_client

    return get_client()
