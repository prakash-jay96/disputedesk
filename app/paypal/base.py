"""The PayPal surface DisputeDesk uses, shared by the mock and the real client."""
from __future__ import annotations

from typing import Any, Protocol


class PayPalError(RuntimeError):
    """A PayPal call failed. Carries the status code and parsed body when there is one."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DisputeClient(Protocol):
    mode: str

    def list_disputes(self) -> list[dict[str, Any]]: ...

    def get_dispute(self, dispute_id: str) -> dict[str, Any]: ...

    def get_store_record(self, dispute_id: str) -> dict[str, Any] | None: ...

    def provide_evidence(
        self, dispute_id: str, evidences: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any], dict[str, Any]]: ...

    def make_offer(
        self, dispute_id: str, note: str, offer_type: str, value: str, currency_code: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]: ...

    def accept_claim(
        self, dispute_id: str, note: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]: ...

    def send_message(
        self, dispute_id: str, message: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]: ...
