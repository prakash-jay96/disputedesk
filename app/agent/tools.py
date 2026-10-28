"""The agent's tool surface.

Six read-only tools over the dispute and the seller's own store records, plus
one terminal tool the agent uses to hand back a plan. Nothing here can change
anything: writes to PayPal happen only after the seller approves, from the API
layer, never from the agent loop.
"""
from __future__ import annotations

import json
from typing import Any

EVIDENCE_TYPES = [
    "PROOF_OF_FULFILLMENT",
    "PROOF_OF_REFUND",
    "PROOF_OF_DELIVERY_SIGNATURE",
    "PROOF_OF_RETURN",
    "ITEM_DESCRIPTION",
    "RETURN_POLICY",
    "DEVICE_ID",
    "OTHER",
]

OFFER_TYPES = [
    "REFUND",
    "REFUND_WITH_RETURN",
    "REFUND_WITH_REPLACEMENT",
    "REPLACEMENT_WITHOUT_REFUND",
]

READ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_dispute_details",
        "description": (
            "The full PayPal dispute record: reason code, disputed amount, status, lifecycle "
            "stage, the buyer's own messages, the seller response deadline, and "
            "allowed_response_options (which tells you which offer types PayPal will accept). "
            "Call this first."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_order_record",
        "description": (
            "The seller's internal order record: line items with per-unit prices, totals, the "
            "shipping address and whether PayPal confirmed it, signature requirements, and any "
            "internal notes such as a detected duplicate charge."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_shipment_tracking",
        "description": (
            "Carrier tracking for the order: carrier name, tracking number, current status, the "
            "full scan event history, and whether a delivery signature or delivery photo is on "
            "file. Use this to judge a not-received claim."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_customer_messages",
        "description": (
            "The message history between buyer and seller in the seller's own system, before and "
            "after the dispute was opened."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_store_policies",
        "description": (
            "The seller's published policies: returns window, damaged-item handling, shipping and "
            "lost-parcel policy, and the seller's stated stance on disputes. The plan must be "
            "consistent with these."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_buyer_history",
        "description": (
            "This buyer's history with the store: order count, lifetime value, prior disputes, "
            "how many deliveries have gone to the same address, and account age."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_resolution_plan",
    "description": (
        "Hand back your final recommendation. Call this exactly once, as your last action, after "
        "you have gathered the evidence you need. The plan is shown to the seller for approval "
        "and nothing is sent to PayPal until they approve it, so be specific and honest about "
        "weaknesses."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["FIGHT", "OFFER", "ACCEPT"],
                "description": (
                    "FIGHT: contest the claim by filing evidence. OFFER: propose a partial or "
                    "full settlement to the buyer. ACCEPT: concede the claim and refund in full."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How likely this action is to be the right call, 0 to 1.",
            },
            "summary": {
                "type": "string",
                "description": "One or two sentences the seller reads first.",
            },
            "reasoning": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The steps that led to the action, each tied to something you actually read."
                ),
            },
            "evidence": {
                "type": "array",
                "description": (
                    "Evidence to file with PayPal. Required for FIGHT, empty otherwise."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
                        "description": {
                            "type": "string",
                            "description": "What this evidence shows, in the seller's words.",
                        },
                        "tracking_info": {
                            "type": "object",
                            "properties": {
                                "carrier_name": {"type": "string"},
                                "tracking_number": {"type": "string"},
                            },
                            "required": ["carrier_name", "tracking_number"],
                            "additionalProperties": False,
                        },
                        "refund_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["evidence_type", "description"],
                    "additionalProperties": False,
                },
            },
            "offer": {
                "type": "object",
                "description": "Required for OFFER, omitted otherwise.",
                "properties": {
                    "offer_type": {"type": "string", "enum": OFFER_TYPES},
                    "amount": {
                        "type": "string",
                        "description": "Amount as a decimal string, e.g. \"15.50\". Must not "
                                       "exceed the disputed amount.",
                    },
                },
                "required": ["offer_type", "amount"],
                "additionalProperties": False,
            },
            "response_note": {
                "type": "string",
                "description": (
                    "The note sent to PayPal with the response, addressed to the PayPal reviewer. "
                    "Factual, specific, 2000 characters maximum."
                ),
            },
            "buyer_message": {
                "type": "string",
                "description": "A short, courteous message to the buyer explaining the outcome.",
            },
            "risk_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Anything the seller should know before approving: weak evidence, missing "
                    "records, deadline pressure, a reason this could still be lost."
                ),
            },
        },
        "required": [
            "action",
            "confidence",
            "summary",
            "reasoning",
            "evidence",
            "response_note",
            "buyer_message",
            "risk_flags",
        ],
        "additionalProperties": False,
    },
}

ALL_TOOLS: list[dict[str, Any]] = READ_TOOLS + [SUBMIT_TOOL]
READ_TOOL_NAMES = {t["name"] for t in READ_TOOLS}
SUBMIT_TOOL_NAME = SUBMIT_TOOL["name"]

_MISSING = {
    "available": False,
    "note": "No record on file for this dispute. Reason about the dispute without it, and say so "
            "in risk_flags.",
}


class ToolBox:
    """Dispatches the read-only tools for one dispute."""

    def __init__(self, client, dispute_id: str) -> None:
        self._client = client
        self._dispute_id = dispute_id
        self._dispute: dict[str, Any] | None = None
        self._store: dict[str, Any] | None = None
        self._store_loaded = False

    # --- data ---------------------------------------------------------------

    @property
    def dispute(self) -> dict[str, Any]:
        if self._dispute is None:
            self._dispute = self._client.get_dispute(self._dispute_id)
        return self._dispute

    @property
    def store(self) -> dict[str, Any] | None:
        if not self._store_loaded:
            self._store = self._client.get_store_record(self._dispute_id)
            self._store_loaded = True
        return self._store

    def _section(self, key: str) -> Any:
        store = self.store
        if not store or key not in store:
            return dict(_MISSING)
        return store[key]

    # --- dispatch -----------------------------------------------------------

    def call(self, name: str) -> Any:
        if name == "get_dispute_details":
            return self.dispute
        if name == "get_order_record":
            return self._section("order")
        if name == "get_shipment_tracking":
            return self._section("tracking")
        if name == "get_customer_messages":
            store = self.store
            return {
                "store_messages": (store or {}).get("messages", []),
                "paypal_dispute_messages": self.dispute.get("messages", []),
                "photo_evidence": (store or {}).get("photo_evidence"),
            }
        if name == "get_store_policies":
            from ..paypal.fixtures import STORE_POLICIES

            return STORE_POLICIES
        if name == "get_buyer_history":
            return self._section("buyer_history")
        raise KeyError(f"unknown tool {name!r}")

    def call_as_text(self, name: str) -> str:
        return json.dumps(self.call(name), indent=2, default=str)

    def refunds(self) -> list[dict[str, Any]]:
        store = self.store or {}
        value = store.get("refunds")
        return value if isinstance(value, list) else []
