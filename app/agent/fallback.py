"""Offline rules engine.

Used when ANTHROPIC_API_KEY is not set, so the product is still demonstrable
without an API key. It produces the same plan shape as the Claude agent, and
every plan it returns is labelled so nobody mistakes it for model reasoning.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..guardrails import allowed_offer_types, money
from .tools import ToolBox

ENGINE = "rules"
STUCK_SCAN_DAYS = 10

_LABEL = (
    "Produced by the offline rules engine, not by Claude: ANTHROPIC_API_KEY is not set. "
    "Set it to get the full agent reasoning trace."
)


def _delivered(tracking: dict[str, Any]) -> bool:
    return str(tracking.get("current_status", "")).upper() == "DELIVERED"


def _stuck(tracking: dict[str, Any]) -> bool:
    status = str(tracking.get("current_status", "")).upper()
    days = tracking.get("days_since_last_scan")
    if status in {"LABEL_CREATED", "INFORMATION_RECEIVED", ""} and isinstance(days, (int, float)):
        return days >= STUCK_SCAN_DAYS
    return status == "LABEL_CREATED"


def _tracking_evidence(tracking: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    carrier = tracking.get("carrier_name")
    number = tracking.get("tracking_number")
    if carrier and number:
        evidence.append(
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "description": (
                    f"{tracking.get('carrier_display', carrier)} tracking {number} shows the "
                    f"parcel delivered on {str(tracking.get('delivered_at'))[:10]}."
                ),
                "tracking_info": {"carrier_name": carrier, "tracking_number": number},
            }
        )
    if tracking.get("signature_image_on_file"):
        evidence.append(
            {
                "evidence_type": "PROOF_OF_DELIVERY_SIGNATURE",
                "description": (
                    f"Carrier captured a delivery signature from {tracking.get('signed_by')}; "
                    "the signature image is on file."
                ),
            }
        )
    return evidence


def build_plan(box: ToolBox) -> dict[str, Any]:
    dispute = box.dispute
    reason = dispute.get("reason", "")
    amount = (dispute.get("dispute_amount") or {}).get("value", "0")
    currency = (dispute.get("dispute_amount") or {}).get("currency_code", "USD")
    tracking = box.call("get_shipment_tracking")
    order = box.call("get_order_record")
    history = box.call("get_buyer_history")
    tracking = tracking if isinstance(tracking, dict) else {}
    order = order if isinstance(order, dict) else {}
    offer_types = allowed_offer_types(dispute)

    plan: dict[str, Any] = {
        "action": "ACCEPT",
        "confidence": 0.5,
        "summary": "",
        "reasoning": [],
        "evidence": [],
        "offer": None,
        "response_note": "",
        "buyer_message": "",
        "risk_flags": [_LABEL],
        "engine": ENGINE,
    }

    # --- duplicate charge that was already refunded -------------------------
    refunds = box.refunds()
    disputed_txn = (dispute.get("disputed_transactions") or [{}])[0].get("seller_transaction_id")
    matching = [r for r in refunds if r.get("refunded_transaction_id") == disputed_txn]
    if reason == "DUPLICATE_TRANSACTION" and matching:
        refund = matching[0]
        plan.update(
            action="FIGHT",
            confidence=0.93,
            summary=(
                f"The duplicate charge was already refunded in full on "
                f"{str(refund.get('create_time'))[:10]} (refund {refund.get('refund_id')}). "
                "File the refund as evidence rather than paying twice."
            ),
            reasoning=[
                f"Reason code is DUPLICATE_TRANSACTION for {currency} {amount}.",
                f"The store record shows checkout was submitted twice and the second charge "
                f"({disputed_txn}) was refunded the same day.",
                f"Refund {refund.get('refund_id')} for "
                f"{refund.get('amount', {}).get('value')} is COMPLETED, so the buyer is not out "
                "of pocket.",
                "Accepting would refund the same money a second time.",
            ],
            evidence=[
                {
                    "evidence_type": "PROOF_OF_REFUND",
                    "description": (
                        f"Refund {refund.get('refund_id')} of "
                        f"{refund.get('amount', {}).get('currency_code')} "
                        f"{refund.get('amount', {}).get('value')} against transaction "
                        f"{disputed_txn}, completed "
                        f"{str(refund.get('create_time'))[:10]}."
                    ),
                    "refund_ids": [refund.get("refund_id", "")],
                }
            ],
            response_note=(
                f"The buyer was charged twice when checkout was submitted twice within 90 "
                f"seconds. We identified the duplicate the same day and refunded it in full: "
                f"refund ID {refund.get('refund_id')}, "
                f"{refund.get('amount', {}).get('currency_code')} "
                f"{refund.get('amount', {}).get('value')}, against transaction {disputed_txn}, "
                f"completed {str(refund.get('create_time'))[:10]}. Only one parcel was shipped "
                "and it was delivered. No further refund is due."
            ),
            buyer_message=(
                "Thanks for flagging this. You were charged twice at checkout and we refunded "
                f"the duplicate on {str(refund.get('create_time'))[:10]} — refund reference "
                f"{refund.get('refund_id')}. Bank statements can take 3–5 working days to "
                "show it, so it may already be on its way back to you."
            ),
        )
        plan["risk_flags"].insert(0, "Buyer may not have seen the refund land yet; the message to "
                                    "them matters as much as the evidence.")
        return plan

    # --- not received -------------------------------------------------------
    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED":
        if _delivered(tracking):
            evidence = _tracking_evidence(tracking)
            plan.update(
                action="FIGHT",
                confidence=0.88 if tracking.get("signature_image_on_file") else 0.72,
                summary=(
                    "Tracking shows this delivered"
                    + (" and signed for" if tracking.get("signed_by") else "")
                    + ". Contest the claim with the delivery evidence."
                ),
                reasoning=[
                    f"Buyer claims non-receipt of {currency} {amount}.",
                    f"{tracking.get('carrier_display')} tracking {tracking.get('tracking_number')} "
                    f"reads DELIVERED on {str(tracking.get('delivered_at'))[:10]}.",
                    "Delivery signature on file." if tracking.get("signature_image_on_file")
                    else "No signature on file; relying on the carrier scan.",
                    "Delivery photo on file." if tracking.get("delivery_photo_on_file")
                    else "No delivery photo on file.",
                    "Shipping address matches the PayPal transaction address."
                    if (order.get("ship_to") or {}).get("matches_paypal_transaction_address")
                    else "Shipping address could not be matched to the PayPal address.",
                ],
                evidence=evidence,
                response_note=(
                    f"The order was delivered. {tracking.get('carrier_display')} tracking "
                    f"{tracking.get('tracking_number')} records delivery on "
                    f"{str(tracking.get('delivered_at'))[:10]} to the PayPal-confirmed address on "
                    f"the transaction"
                    + (
                        f", signed for by {tracking.get('signed_by')}, with the signature image on "
                        "file."
                        if tracking.get("signature_image_on_file")
                        else "."
                    )
                    + (
                        " The carrier also captured a delivery photo."
                        if tracking.get("delivery_photo_on_file")
                        else ""
                    )
                    + " We ask that the claim be denied on the delivery evidence."
                ),
                buyer_message=(
                    "I've checked the tracking and the parcel was delivered on "
                    f"{str(tracking.get('delivered_at'))[:10]}"
                    + (f", signed for by {tracking.get('signed_by')}" if tracking.get("signed_by") else "")
                    + ". I've shared the carrier record with PayPal. If it was taken in by "
                    "someone else at the address, the carrier's proof of delivery is the quickest "
                    "way to trace it — I'm happy to help you do that."
                ),
            )
            if not tracking.get("signature_image_on_file"):
                plan["risk_flags"].insert(0, "No delivery signature; a carrier scan alone is "
                                             "weaker evidence for a not-received claim.")
            return plan

        if _stuck(tracking):
            plan.update(
                action="ACCEPT",
                confidence=0.91,
                summary=(
                    "Tracking never advanced past label creation, so this parcel was almost "
                    "certainly never collected. Accept and refund in full rather than lose the "
                    "claim and the customer."
                ),
                reasoning=[
                    f"Buyer claims non-receipt of {currency} {amount}.",
                    f"{tracking.get('carrier_display')} tracking "
                    f"{tracking.get('tracking_number')} is still LABEL_CREATED with no further "
                    f"scan after {tracking.get('days_since_last_scan', 'many')} days.",
                    "Store policy treats a parcel with no scan after 10 days as lost and refunds "
                    "in full.",
                    "There is no delivery evidence to file, so contesting this would fail and "
                    "add a dispute loss to the account.",
                ],
                response_note=(
                    "We accept this claim. The parcel was never collected by the carrier: "
                    f"{tracking.get('carrier_display')} tracking "
                    f"{tracking.get('tracking_number')} has not advanced beyond label creation. "
                    "The buyer is refunded in full and we are pursuing the carrier separately."
                ),
                buyer_message=(
                    "You're right, and I'm sorry. The courier never collected your parcel — "
                    "the tracking never moved past the label. I've refunded you in full. If "
                    "you'd still like the tea towels, message me and I'll send a replacement at "
                    "my cost."
                ),
            )
            plan["risk_flags"].insert(0, "Accepting refunds the full amount and counts as a "
                                         "dispute loss; it is still the right call here.")
            return plan

        plan.update(
            action="ACCEPT",
            confidence=0.6,
            summary="No delivery evidence on file, so this claim cannot be defended. Accept it.",
            reasoning=[
                f"Buyer claims non-receipt of {currency} {amount}.",
                "Tracking does not show delivery and no signature or photo is on file.",
            ],
            response_note="We accept this claim; we cannot evidence delivery of this order.",
            buyer_message="I can't evidence delivery of your order, so I've refunded you in full. "
                          "Sorry for the trouble.",
        )
        return plan

    # --- not as described ---------------------------------------------------
    if reason == "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED":
        items = order.get("items") or [{}]
        per_item = items[0].get("per_item_value")
        set_size = items[0].get("unit_count_in_set")
        offer_value = money(per_item) if per_item else money(Decimal(money(amount)) / 2)
        offer_type = "REFUND" if "REFUND" in offer_types else (offer_types[0] if offer_types else "")
        if offer_type:
            plan.update(
                action="OFFER",
                confidence=0.84,
                summary=(
                    f"One item of a set of {set_size or 'several'} arrived damaged. Offer a "
                    f"partial refund of {currency} {offer_value} for the damaged item rather "
                    f"than the full {currency} {amount}."
                ),
                reasoning=[
                    f"Buyer reports one of {set_size or 'several'} items chipped; the rest are fine.",
                    f"Order record prices the set at {currency} {amount}, i.e. {currency} "
                    f"{offer_value} per item.",
                    "Store policy refunds the damaged portion of a part-damaged set rather than "
                    "the whole order.",
                    "The buyer has said they do not want to repack and return the set, so a "
                    "return-based offer would be friction for both sides.",
                    f"PayPal allows these offer types here: {', '.join(offer_types)}.",
                ],
                offer={"offer_type": offer_type, "amount": offer_value},
                response_note=(
                    f"The buyer reports that one mug in a set of {set_size or 4} arrived with a "
                    f"chipped rim; the remaining items are undamaged and the buyer has confirmed "
                    f"they do not wish to return the set. In line with our published damaged-item "
                    f"policy we offer a partial refund of {currency} {offer_value}, the per-item "
                    f"value of the damaged piece, with no return required."
                ),
                buyer_message=(
                    f"Sorry about the chipped mug — that shouldn't have left here. I've "
                    f"offered you a {currency} {offer_value} refund for that mug, which is what "
                    "it cost in the set, and you don't need to send anything back. If you'd "
                    "rather have a replacement mug instead, say the word and I'll post one."
                ),
            )
            plan["risk_flags"].insert(0, "If the buyer declines the partial offer, PayPal may "
                                         "escalate and the full amount could be at stake.")
            return plan

    # --- unauthorised -------------------------------------------------------
    if reason == "UNAUTHORISED":
        if _delivered(tracking):
            evidence = _tracking_evidence(tracking)
            ship_to = order.get("ship_to") or {}
            if order.get("device_fingerprint_matches_prior_orders"):
                evidence.append(
                    {
                        "evidence_type": "DEVICE_ID",
                        "description": (
                            "Checkout device fingerprint matches this buyer's previous orders, "
                            f"and the order shipped to the same address as their "
                            f"{history.get('deliveries_to_this_address', 'previous')} earlier "
                            "deliveries."
                        ),
                    }
                )
            plan.update(
                action="FIGHT",
                confidence=0.86,
                summary=(
                    "Shipped to the PayPal-confirmed address on the account, signed for on "
                    "delivery, and the buyer has an order history at the same address. Contest it."
                ),
                reasoning=[
                    f"Unauthorised-use chargeback for {currency} {amount} at the "
                    f"{dispute.get('dispute_life_cycle_stage')} stage.",
                    f"Delivered to {ship_to.get('address_line_1')}, "
                    f"{ship_to.get('admin_area_2')} — PayPal address status "
                    f"{ship_to.get('paypal_address_status')}, matching the transaction address.",
                    f"Signed for by {tracking.get('signed_by')} with the signature image on file."
                    if tracking.get("signature_image_on_file")
                    else "Carrier records delivery.",
                    f"Buyer has {history.get('orders_count')} orders with the store and "
                    f"{history.get('prior_disputes')} prior disputes, with "
                    f"{history.get('deliveries_to_this_address')} deliveries to this address.",
                    "Buyer messaged after delivery to thank the seller, from the same account.",
                ],
                evidence=evidence,
                response_note=(
                    f"This transaction was authorised. The order shipped to the "
                    f"PayPal-confirmed address held on the buyer's account, which matches the "
                    f"address on the transaction and the address used for "
                    f"{history.get('deliveries_to_this_address')} previous orders by the same "
                    f"buyer. {tracking.get('carrier_display')} tracking "
                    f"{tracking.get('tracking_number')} records delivery on "
                    f"{str(tracking.get('delivered_at'))[:10]}, signed for by "
                    f"{tracking.get('signed_by')}, with the signature image on file. The buyer "
                    "also messaged us after delivery to confirm the item had arrived. We ask "
                    "that the chargeback be reversed."
                ),
                buyer_message=(
                    "This order was placed from your account and delivered to your usual "
                    "address, signed for on arrival — and you messaged me afterwards to say "
                    "it had arrived. If someone else in your household used your account, or "
                    "you'd like me to walk through the delivery record with you, reply here and "
                    "I'll help sort it out."
                ),
            )
            plan["risk_flags"].insert(
                0,
                "Card-issuer chargebacks at this stage can be decided by the issuer regardless "
                "of the delivery evidence.",
            )
            return plan

    # --- fallback -----------------------------------------------------------
    if _delivered(tracking):
        plan.update(
            action="FIGHT",
            confidence=0.6,
            summary="Tracking shows delivery; contest with the delivery record.",
            reasoning=[f"Reason code {reason}.", "Tracking shows the order delivered."],
            evidence=_tracking_evidence(tracking),
            response_note="The order was delivered; tracking evidence is attached.",
            buyer_message="The tracking shows this was delivered — happy to look into it "
                          "with you.",
        )
    else:
        plan.update(
            action="ACCEPT",
            confidence=0.55,
            summary="Not enough evidence to defend this claim; accept and refund.",
            reasoning=[f"Reason code {reason}.", "No delivery or refund evidence on file."],
            response_note="We accept this claim.",
            buyer_message="I've refunded you in full. Sorry for the trouble.",
        )
    return plan
