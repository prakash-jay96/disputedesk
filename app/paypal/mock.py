"""An in-memory stand-in for the PayPal Disputes v1 sandbox.

Same request and response shapes as the real API, so swapping PAYPAL_MODE
changes nothing above this layer.
"""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from . import fixtures
from .base import PayPalError

_BASE = "https://api-m.sandbox.paypal.com/v1/customer/disputes"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _links(dispute_id: str) -> list[dict[str, str]]:
    return [{"href": f"{_BASE}/{dispute_id}", "rel": "self", "method": "GET"}]


class MockDisputeClient:
    """Mutable copy of the fixtures, guarded by a lock for the agent thread."""

    mode = "mock"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._disputes = {d["dispute_id"]: d for d in fixtures.fresh_disputes()}
            self._store = fixtures.fresh_store()

    # --- reads --------------------------------------------------------------

    def list_disputes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(d) for d in self._disputes.values()]

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        with self._lock:
            dispute = self._disputes.get(dispute_id)
            if dispute is None:
                raise PayPalError(f"dispute {dispute_id} not found", 404, {
                    "name": "RESOURCE_NOT_FOUND",
                    "message": "The specified resource does not exist.",
                })
            return copy.deepcopy(dispute)

    def get_store_record(self, dispute_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._store.get(dispute_id)
            return copy.deepcopy(record) if record else None

    # --- writes -------------------------------------------------------------

    def _require(self, dispute_id: str) -> dict[str, Any]:
        dispute = self._disputes.get(dispute_id)
        if dispute is None:
            raise PayPalError(f"dispute {dispute_id} not found", 404, {
                "name": "RESOURCE_NOT_FOUND",
                "message": "The specified resource does not exist.",
            })
        if dispute["status"] != "WAITING_FOR_SELLER_RESPONSE":
            raise PayPalError(
                "dispute is not awaiting a seller response",
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "DISPUTE_STATE_INELIGIBLE"}],
                },
            )
        return dispute

    def provide_evidence(self, dispute_id: str, evidences):
        endpoint = f"{_BASE}/{dispute_id}/provide-evidence"
        body = {"evidences": evidences}
        with self._lock:
            dispute = self._require(dispute_id)
            dispute["status"] = "UNDER_REVIEW"
            dispute["update_time"] = _now_iso()
            dispute["evidences"] = evidences
        return endpoint, body, {
            "links": _links(dispute_id),
            "_mock": True,
            "_effect": "evidence filed; dispute moved to UNDER_REVIEW",
        }

    def make_offer(self, dispute_id: str, note: str, offer_type: str, value: str, currency_code: str):
        endpoint = f"{_BASE}/{dispute_id}/make-offer"
        body = {
            "note": note,
            "offer_amount": {"currency_code": currency_code, "value": value},
            "offer_type": offer_type,
        }
        with self._lock:
            dispute = self._require(dispute_id)
            allowed = (dispute.get("allowed_response_options") or {}).get("make_offer") or {}
            if offer_type not in (allowed.get("offer_types") or []):
                raise PayPalError(
                    "offer type is not allowed for this dispute",
                    422,
                    {"name": "UNPROCESSABLE_ENTITY",
                     "details": [{"issue": "OFFER_TYPE_NOT_ALLOWED", "field": "/offer_type"}]},
                )
            dispute["status"] = "WAITING_FOR_BUYER_RESPONSE"
            dispute["update_time"] = _now_iso()
            dispute["offer"] = {
                "buyer_requested_amount": copy.deepcopy(dispute["dispute_amount"]),
                "seller_offered_amount": {"currency_code": currency_code, "value": value},
                "offer_type": offer_type,
            }
        return endpoint, body, {
            "links": _links(dispute_id),
            "_mock": True,
            "_effect": f"offer of {currency_code} {value} sent; awaiting buyer",
        }

    def accept_claim(self, dispute_id: str, note: str):
        endpoint = f"{_BASE}/{dispute_id}/accept-claim"
        body = {"note": note}
        with self._lock:
            dispute = self._require(dispute_id)
            dispute["status"] = "RESOLVED"
            dispute["update_time"] = _now_iso()
            dispute["dispute_outcome"] = {
                "outcome_code": "RESOLVED_BUYER_FAVOUR",
                "amount_refunded": copy.deepcopy(dispute["dispute_amount"]),
            }
        return endpoint, body, {
            "links": _links(dispute_id),
            "_mock": True,
            "_effect": "claim accepted; buyer refunded in full",
        }

    def send_message(self, dispute_id: str, message: str):
        endpoint = f"{_BASE}/{dispute_id}/send-message"
        body = {"message": message}
        with self._lock:
            dispute = self._require(dispute_id)
            dispute["messages"].append(
                {"posted_by": "SELLER", "time_posted": _now_iso(), "content": message}
            )
            # A message to the buyer does not close the seller's response window,
            # so the status is deliberately left alone here.
            dispute["update_time"] = _now_iso()
        return endpoint, body, {
            "links": _links(dispute_id),
            "_mock": True,
            "_effect": "message posted to the dispute thread",
            "_message_id": uuid.uuid4().hex[:10].upper(),
        }
