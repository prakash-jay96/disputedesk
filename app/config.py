"""Runtime configuration, read once from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    # --- PayPal ---
    paypal_mode: str = field(default_factory=lambda: _env("PAYPAL_MODE", "mock").lower())
    paypal_base_url: str = field(
        default_factory=lambda: _env("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    )
    paypal_client_id: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_ID"))
    paypal_client_secret: str = field(default_factory=lambda: _env("PAYPAL_CLIENT_SECRET"))

    # --- Claude ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-sonnet-5-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "medium"))
    max_agent_turns: int = field(default_factory=lambda: int(_env("MAX_AGENT_TURNS", "8")))

    # --- App ---
    db_path: str = field(default_factory=lambda: _env("DISPUTEDESK_DB", "disputedesk.db"))
    seller_hourly_rate: float = field(default_factory=lambda: float(_env("SELLER_HOURLY_RATE", "35")))
    minutes_saved_per_dispute: int = field(
        default_factory=lambda: int(_env("MINUTES_SAVED_PER_DISPUTE", "45"))
    )

    @property
    def live_paypal(self) -> bool:
        return self.paypal_mode == "sandbox"

    @property
    def agent_is_live(self) -> bool:
        """False -> the offline rules engine answers instead of Claude."""
        return bool(self.anthropic_api_key)

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)


def get_settings() -> Settings:
    """Read fresh each call so tests can monkeypatch os.environ."""
    return Settings()
