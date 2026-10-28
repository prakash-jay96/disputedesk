"""Dispute client selection: mock sandbox by default, real API on request."""
from __future__ import annotations

from functools import lru_cache

from ..config import Settings, get_settings
from .base import DisputeClient, PayPalError
from .mock import MockDisputeClient
from .sandbox import SandboxDisputeClient

__all__ = ["DisputeClient", "PayPalError", "get_client", "reset_client"]

_mock_singleton: MockDisputeClient | None = None


def _mock() -> MockDisputeClient:
    global _mock_singleton
    if _mock_singleton is None:
        _mock_singleton = MockDisputeClient()
    return _mock_singleton


@lru_cache(maxsize=4)
def _sandbox(base_url: str, client_id: str, client_secret: str) -> SandboxDisputeClient:
    # Seller-side records still come from the local store in sandbox mode; a real
    # deployment would swap this lookup for the merchant's order database.
    return SandboxDisputeClient(
        base_url,
        client_id,
        client_secret,
        store_lookup=_mock().get_store_record,
    )


def get_client(settings: Settings | None = None) -> DisputeClient:
    settings = settings or get_settings()
    if settings.live_paypal:
        return _sandbox(
            settings.paypal_base_url, settings.paypal_client_id, settings.paypal_client_secret
        )
    return _mock()


def reset_client() -> None:
    """Restore the mock fixtures to their original state (used by tests and the UI)."""
    _mock().reset()
    _sandbox.cache_clear()
