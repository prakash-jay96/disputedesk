"""Server-side checks between an approved plan and the PayPal API.

Nothing reaches PayPal without passing through here, even if the model (or a
hand-edited request body) asks for something else.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

NOTE_MAX_CHARS = 2000
SUBMITTABLE_STATUSES = {"WAITING_FOR_SELLER_RESPONSE"}


class GuardrailError(ValueError):
    """A plan was rejected before it could be sent to PayPal."""


def money(value: Any) -> str:
    """Normalise an amount to a 2-decimal string, the only shape PayPal accepts."""
    try:
        dec = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, AttributeError):
        raise GuardrailError(f"{value!r} is not a valid amount")
    if dec < 0:
        raise GuardrailError("amount cannot be negative")
    return str(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def truncate_note(note: str | None) -> str:
    note = (note or "").strip()
    if len(note) <= NOTE_MAX_CHARS:
        return note
    return note[: NOTE_MAX_CHARS - 1].rstrip() + "…"


def check_status(dispute: dict) -> None:
    status = dispute.get("status")
    if status not in SUBMITTABLE_STATUSES:
        raise GuardrailError(
            f"dispute status is {status}; PayPal only accepts a seller response while it is "
            "WAITING_FOR_SELLER_RESPONSE"
        )


def allowed_offer_types(dispute: dict) -> list[str]:
    opts = (dispute.get("allowed_response_options") or {}).get("make_offer") or {}
    return list(opts.get("offer_types") or [])


def check_offer(dispute: dict, offer_type: str, amount_value: str) -> tuple[str, str]:
    """Validate an offer against the dispute. Returns (offer_type, normalised amount)."""
    allowed = allowed_offer_types(dispute)
    if not allowed:
        raise GuardrailError("PayPal does not allow an offer on this dispute")
    if offer_type not in allowed:
        raise GuardrailError(
            f"offer_type {offer_type!r} is not allowed here; PayPal allows {', '.join(allowed)}"
        )

    value = money(amount_value)
    if Decimal(value) <= 0:
        raise GuardrailError("offer amount must be greater than zero")

    disputed = dispute.get("dispute_amount") or {}
    cap = money(disputed.get("value", "0"))
    if Decimal(value) > Decimal(cap):
        raise GuardrailError(
            f"offer of {value} exceeds the disputed amount of {cap}; refusing to over-refund"
        )
    return offer_type, value


def dispute_currency(dispute: dict) -> str:
    return (dispute.get("dispute_amount") or {}).get("currency_code", "USD")
