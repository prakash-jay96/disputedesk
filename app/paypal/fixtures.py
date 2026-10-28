"""Five realistic disputes in the PayPal Disputes v1 JSON shape, plus the
seller-side store records the agent reads with its read-only tools.

These power PAYPAL_MODE=mock so the whole product is demonstrable without
sandbox credentials. Field names and enum values match the real API.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

SELLER = {
    "merchant_id": "8KJ4RPQ2WX6YN",
    "name": "Harbour & Fold",
    "email": "orders@harbourandfold.example",
}

STORE_POLICIES = {
    "store_name": SELLER["name"],
    "returns_window_days": 30,
    "returns_policy": (
        "Unused items in original packaging may be returned within 30 days for a full refund. "
        "Damaged or faulty items are replaced or refunded without return, at the buyer's choice, "
        "once a photo is supplied."
    ),
    "damaged_item_policy": (
        "For a part-damaged multi-item set we refund the damaged portion rather than the whole "
        "order, unless the buyer prefers a replacement."
    ),
    "shipping_policy": (
        "Dispatched within 2 business days by tracked courier. Signature required on orders over "
        "$100. We treat a parcel as lost if tracking has not advanced for 10 days and refund in full."
    ),
    "lost_parcel_policy": (
        "If the carrier has not scanned the parcel beyond label creation after 10 days we accept "
        "the claim and refund the buyer in full, then pursue the carrier ourselves."
    ),
    "dispute_stance": (
        "We defend claims where delivery or refund is documented, and settle quickly where the "
        "buyer is plainly out of pocket. We never contest a claim we would lose on the evidence."
    ),
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _amount(value: str, currency: str = "USD") -> dict[str, str]:
    return {"currency_code": currency, "value": value}


def _dispute(
    *,
    dispute_id: str,
    opened_days_ago: int,
    due_in_days: int,
    reason: str,
    stage: str,
    amount: str,
    txn_id: str,
    order_id: str,
    buyer_name: str,
    item_name: str,
    messages: list[tuple[str, int, str]],
    offer_types: list[str] | None,
) -> dict[str, Any]:
    now = _now()
    created = now - timedelta(days=opened_days_ago)
    due = now + timedelta(days=due_in_days)
    allowed: dict[str, Any] = {
        "participant_actions": ["ACCEPT_CLAIM", "PROVIDE_EVIDENCE", "SEND_MESSAGE"],
    }
    if offer_types:
        allowed["make_offer"] = {"offer_types": offer_types}
    return {
        "dispute_id": dispute_id,
        "create_time": _iso(created),
        "update_time": _iso(created + timedelta(hours=6)),
        "disputed_transactions": [
            {
                "seller_transaction_id": txn_id,
                "create_time": _iso(created - timedelta(days=9)),
                "transaction_status": "COMPLETED",
                "gross_amount": _amount(amount),
                "invoice_number": order_id,
                "custom": order_id,
                "buyer": {"name": buyer_name},
                "seller": dict(SELLER),
                "items": [{"item_id": txn_id + "-1", "item_name": item_name}],
            }
        ],
        "reason": reason,
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_amount": _amount(amount),
        "dispute_asset_symbol": None,
        "dispute_outcome": None,
        "dispute_life_cycle_stage": stage,
        "dispute_channel": "INTERNAL",
        "messages": [
            {
                "posted_by": who,
                "time_posted": _iso(created + timedelta(hours=hours)),
                "content": text,
            }
            for who, hours, text in messages
        ],
        "extensions": {},
        "offer": None,
        "seller_response_due_date": _iso(due),
        "allowed_response_options": allowed,
        "links": [
            {
                "href": f"https://api-m.sandbox.paypal.com/v1/customer/disputes/{dispute_id}",
                "rel": "self",
                "method": "GET",
            }
        ],
    }


# --------------------------------------------------------------------------
# 1. $149 headphones, not received, but delivered with signature + photo
# --------------------------------------------------------------------------
D1 = _dispute(
    dispute_id="PP-D-10041",
    opened_days_ago=3,
    due_in_days=7,
    reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    stage="INQUIRY",
    amount="149.00",
    txn_id="5YJ18402KX9114T",
    order_id="HF-24181",
    buyer_name="Daniel Osei",
    item_name="Fieldline ANC Headphones — Graphite",
    messages=[
        (
            "BUYER",
            1,
            "I never got these headphones. Nothing has arrived and I've checked with everyone in "
            "the building. I'd like a refund please.",
        )
    ],
    offer_types=["REFUND", "REFUND_WITH_RETURN"],
)

# --------------------------------------------------------------------------
# 2. $62 mug set, 1 of 4 chipped -> partial refund
# --------------------------------------------------------------------------
D2 = _dispute(
    dispute_id="PP-D-10042",
    opened_days_ago=2,
    due_in_days=8,
    reason="MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
    stage="INQUIRY",
    amount="62.00",
    txn_id="7HK93220LM4471P",
    order_id="HF-24206",
    buyer_name="Priya Raman",
    item_name="Stoneware Mug Set of 4 — Oatmeal",
    messages=[
        (
            "BUYER",
            2,
            "The set arrived today and one of the four mugs has a chip on the rim, right where you "
            "drink. The other three are fine. Photo attached. I don't really want to repack the "
            "whole box, but I shouldn't pay full price for a chipped mug.",
        )
    ],
    offer_types=["REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT"],
)

# --------------------------------------------------------------------------
# 3. $38 tea towels, tracking stuck at label created for 3 weeks -> accept
# --------------------------------------------------------------------------
D3 = _dispute(
    dispute_id="PP-D-10043",
    opened_days_ago=4,
    due_in_days=5,
    reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    stage="INQUIRY",
    amount="38.00",
    txn_id="3CF55781QQ2288R",
    order_id="HF-24077",
    buyer_name="Marta Kowalski",
    item_name="Linen Tea Towels, Set of 3 — Indigo Stripe",
    messages=[
        (
            "BUYER",
            1,
            "It has been three weeks. The tracking link has said 'label created' since the day I "
            "ordered. I don't think this was ever posted.",
        ),
        (
            "BUYER",
            52,
            "Following up — still nothing. Please just refund me.",
        ),
    ],
    offer_types=["REFUND"],
)

# --------------------------------------------------------------------------
# 4. $420 cookware, unauthorised chargeback, verified address + signature
# --------------------------------------------------------------------------
D4 = _dispute(
    dispute_id="PP-D-10044",
    opened_days_ago=5,
    due_in_days=6,
    reason="UNAUTHORISED",
    stage="CHARGEBACK",
    amount="420.00",
    txn_id="9LP60133VB7756S",
    order_id="HF-23940",
    buyer_name="Helen Vasquez",
    item_name="Sorrel 5-Piece Cast Iron Cookware Set",
    messages=[
        (
            "BUYER",
            3,
            "This charge was not authorised by me.",
        )
    ],
    offer_types=None,  # unauthorised chargebacks do not allow an offer
)

# --------------------------------------------------------------------------
# 5. $24.50 candles, duplicate transaction, already refunded
# --------------------------------------------------------------------------
D5 = _dispute(
    dispute_id="PP-D-10045",
    opened_days_ago=1,
    due_in_days=9,
    reason="DUPLICATE_TRANSACTION",
    stage="INQUIRY",
    amount="24.50",
    txn_id="2WD77906HN5529U",
    order_id="HF-24233",
    buyer_name="Tom Whitfield",
    item_name="Beeswax Pillar Candle, Pair — Honey",
    messages=[
        (
            "BUYER",
            4,
            "I was charged twice for the same candles. Please refund the second charge.",
        )
    ],
    offer_types=["REFUND"],
)

DISPUTES: list[dict[str, Any]] = [D1, D2, D3, D4, D5]


# --------------------------------------------------------------------------
# Seller store records, keyed by dispute id. This is the data the agent reads.
# --------------------------------------------------------------------------
def _store() -> dict[str, dict[str, Any]]:
    now = _now()
    return {
        "PP-D-10041": {
            "order": {
                "order_id": "HF-24181",
                "placed_at": _iso(now - timedelta(days=12)),
                "dispatched_at": _iso(now - timedelta(days=10)),
                "status": "DELIVERED",
                "items": [
                    {"sku": "FL-ANC-GR", "name": "Fieldline ANC Headphones — Graphite",
                     "qty": 1, "unit_price": "149.00"}
                ],
                "item_total": "149.00",
                "shipping_total": "0.00",
                "order_total": "149.00",
                "ship_to": {
                    "name": "Daniel Osei",
                    "address_line_1": "41 Pembroke Road",
                    "address_line_2": "Flat 2",
                    "admin_area_2": "Bristol",
                    "postal_code": "BS8 3BA",
                    "country_code": "GB",
                    "paypal_address_status": "CONFIRMED",
                    "matches_paypal_transaction_address": True,
                },
                "signature_required": True,
            },
            "tracking": {
                "carrier_name": "DHL_EXPRESS",
                "carrier_display": "DHL Express",
                "tracking_number": "JD0002186417392041",
                "current_status": "DELIVERED",
                "delivered_at": _iso(now - timedelta(days=8)),
                "signed_by": "D OSEI",
                "signature_image_on_file": True,
                "delivery_photo_on_file": True,
                "delivery_photo_note": "Parcel photographed inside the porch, door number 41 visible.",
                "events": [
                    {"at": _iso(now - timedelta(days=10)), "status": "LABEL_CREATED",
                     "location": "Bristol, GB", "detail": "Shipment information received"},
                    {"at": _iso(now - timedelta(days=10)), "status": "COLLECTED",
                     "location": "Bristol, GB", "detail": "Collected from sender"},
                    {"at": _iso(now - timedelta(days=9)), "status": "IN_TRANSIT",
                     "location": "Bristol Hub, GB", "detail": "Processed at facility"},
                    {"at": _iso(now - timedelta(days=8, hours=4)), "status": "OUT_FOR_DELIVERY",
                     "location": "Bristol, GB", "detail": "With courier for delivery"},
                    {"at": _iso(now - timedelta(days=8)), "status": "DELIVERED",
                     "location": "41 Pembroke Road, Bristol, GB",
                     "detail": "Delivered, signed for by D OSEI. Photo captured."},
                ],
            },
            "messages": [
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=4)),
                 "text": "Has my order shipped? I can't find it."},
                {"direction": "FROM_SELLER", "at": _iso(now - timedelta(days=4)),
                 "text": "It was delivered and signed for on the "
                         + _iso(now - timedelta(days=8))[:10]
                         + " — here is the tracking link and the delivery photo."},
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=3)),
                 "text": "That's not my signature."},
            ],
            "listing": {
                "title": "Fieldline ANC Headphones — Graphite",
                "description": "Over-ear active noise cancelling headphones, 40h battery, "
                               "USB-C, hard case included. Ships tracked and signed.",
            },
            "refunds": [],
            "buyer_history": {
                "paypal_buyer_name": "Daniel Osei",
                "first_order_at": _iso(now - timedelta(days=12)),
                "orders_count": 1,
                "lifetime_value": "149.00",
                "prior_disputes": 0,
                "deliveries_to_this_address": 1,
            },
        },
        "PP-D-10042": {
            "order": {
                "order_id": "HF-24206",
                "placed_at": _iso(now - timedelta(days=9)),
                "dispatched_at": _iso(now - timedelta(days=8)),
                "status": "DELIVERED",
                "items": [
                    {"sku": "SW-MUG-4-OAT", "name": "Stoneware Mug Set of 4 — Oatmeal",
                     "qty": 1, "unit_price": "62.00", "unit_count_in_set": 4,
                     "per_item_value": "15.50"}
                ],
                "item_total": "62.00",
                "shipping_total": "0.00",
                "order_total": "62.00",
                "ship_to": {
                    "name": "Priya Raman",
                    "address_line_1": "208 Ashfield Street",
                    "admin_area_2": "Manchester",
                    "postal_code": "M14 5QT",
                    "country_code": "GB",
                    "paypal_address_status": "CONFIRMED",
                    "matches_paypal_transaction_address": True,
                },
                "signature_required": False,
            },
            "tracking": {
                "carrier_name": "ROYAL_MAIL",
                "carrier_display": "Royal Mail Tracked 48",
                "tracking_number": "RM419823746GB",
                "current_status": "DELIVERED",
                "delivered_at": _iso(now - timedelta(days=6)),
                "signed_by": None,
                "signature_image_on_file": False,
                "delivery_photo_on_file": True,
                "events": [
                    {"at": _iso(now - timedelta(days=8)), "status": "LABEL_CREATED",
                     "location": "Bristol, GB", "detail": "Shipment information received"},
                    {"at": _iso(now - timedelta(days=7)), "status": "IN_TRANSIT",
                     "location": "Midlands Hub, GB", "detail": "Processed at facility"},
                    {"at": _iso(now - timedelta(days=6)), "status": "DELIVERED",
                     "location": "Manchester, GB", "detail": "Delivered"},
                ],
            },
            "messages": [
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=5)),
                 "text": "One of the four mugs has a chip on the rim. Photo attached. The rest are "
                         "lovely."},
                {"direction": "FROM_SELLER", "at": _iso(now - timedelta(days=5)),
                 "text": "So sorry about that — let me look into the best fix for you."},
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=3)),
                 "text": "Any update? I don't want to repack the whole set, I just want the chipped "
                         "one sorted."},
            ],
            "listing": {
                "title": "Stoneware Mug Set of 4 — Oatmeal",
                "description": "Four hand-glazed 350ml stoneware mugs. Dishwasher and microwave "
                               "safe. Sold as a set of four; slight glaze variation between mugs "
                               "is characteristic of the finish. Chips and cracks are not expected "
                               "— contact us and we will put it right.",
            },
            "refunds": [],
            "buyer_history": {
                "paypal_buyer_name": "Priya Raman",
                "first_order_at": _iso(now - timedelta(days=430)),
                "orders_count": 6,
                "lifetime_value": "388.50",
                "prior_disputes": 0,
                "deliveries_to_this_address": 6,
            },
            "photo_evidence": {
                "buyer_photo_on_file": True,
                "note": "Close-up of one mug rim with a 4mm chip. Other three mugs visible, intact.",
            },
        },
        "PP-D-10043": {
            "order": {
                "order_id": "HF-24077",
                "placed_at": _iso(now - timedelta(days=25)),
                "dispatched_at": None,
                "status": "LABEL_CREATED_NOT_COLLECTED",
                "items": [
                    {"sku": "LN-TT-3-IND", "name": "Linen Tea Towels, Set of 3 — Indigo Stripe",
                     "qty": 1, "unit_price": "38.00"}
                ],
                "item_total": "38.00",
                "shipping_total": "0.00",
                "order_total": "38.00",
                "ship_to": {
                    "name": "Marta Kowalski",
                    "address_line_1": "12 Calder Grove",
                    "admin_area_2": "Leeds",
                    "postal_code": "LS11 7HQ",
                    "country_code": "GB",
                    "paypal_address_status": "CONFIRMED",
                    "matches_paypal_transaction_address": True,
                },
                "signature_required": False,
                "internal_note": "Packed but appears to have been missed by the courier collection "
                                 "on the 6th. No scan since.",
            },
            "tracking": {
                "carrier_name": "EVRI",
                "carrier_display": "Evri Standard",
                "tracking_number": "H00A8821947265",
                "current_status": "LABEL_CREATED",
                "delivered_at": None,
                "signed_by": None,
                "signature_image_on_file": False,
                "delivery_photo_on_file": False,
                "days_since_last_scan": 24,
                "events": [
                    {"at": _iso(now - timedelta(days=24)), "status": "LABEL_CREATED",
                     "location": "Bristol, GB", "detail": "Shipment information received"},
                ],
            },
            "messages": [
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=14)),
                 "text": "Tracking hasn't moved since I ordered — has this gone out?"},
                {"direction": "FROM_SELLER", "at": _iso(now - timedelta(days=13)),
                 "text": "Chasing the courier now, sorry for the delay."},
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=6)),
                 "text": "Still nothing. This is three weeks now."},
            ],
            "listing": {
                "title": "Linen Tea Towels, Set of 3 — Indigo Stripe",
                "description": "Three European flax linen tea towels, 50x70cm, pre-washed. "
                               "Dispatched within 2 business days.",
            },
            "refunds": [],
            "buyer_history": {
                "paypal_buyer_name": "Marta Kowalski",
                "first_order_at": _iso(now - timedelta(days=200)),
                "orders_count": 3,
                "lifetime_value": "121.00",
                "prior_disputes": 0,
                "deliveries_to_this_address": 2,
            },
        },
        "PP-D-10044": {
            "order": {
                "order_id": "HF-23940",
                "placed_at": _iso(now - timedelta(days=21)),
                "dispatched_at": _iso(now - timedelta(days=20)),
                "status": "DELIVERED",
                "items": [
                    {"sku": "SR-CI-5PC", "name": "Sorrel 5-Piece Cast Iron Cookware Set",
                     "qty": 1, "unit_price": "420.00"}
                ],
                "item_total": "420.00",
                "shipping_total": "0.00",
                "order_total": "420.00",
                "ship_to": {
                    "name": "Helen Vasquez",
                    "address_line_1": "77 Marlowe Avenue",
                    "admin_area_2": "Cardiff",
                    "postal_code": "CF24 1LD",
                    "country_code": "GB",
                    "paypal_address_status": "CONFIRMED",
                    "matches_paypal_transaction_address": True,
                    "note": "Same address as this buyer's three previous orders.",
                },
                "signature_required": True,
                "device_fingerprint_matches_prior_orders": True,
                "ip_country": "GB",
            },
            "tracking": {
                "carrier_name": "DPD",
                "carrier_display": "DPD Next Day",
                "tracking_number": "15502887463901",
                "current_status": "DELIVERED",
                "delivered_at": _iso(now - timedelta(days=18)),
                "signed_by": "H VASQUEZ",
                "signature_image_on_file": True,
                "delivery_photo_on_file": True,
                "events": [
                    {"at": _iso(now - timedelta(days=20)), "status": "LABEL_CREATED",
                     "location": "Bristol, GB", "detail": "Shipment information received"},
                    {"at": _iso(now - timedelta(days=19)), "status": "IN_TRANSIT",
                     "location": "Severn Hub, GB", "detail": "Processed at facility"},
                    {"at": _iso(now - timedelta(days=18, hours=5)), "status": "OUT_FOR_DELIVERY",
                     "location": "Cardiff, GB", "detail": "On vehicle for delivery"},
                    {"at": _iso(now - timedelta(days=18)), "status": "DELIVERED",
                     "location": "77 Marlowe Avenue, Cardiff, GB",
                     "detail": "Delivered, signed for by H VASQUEZ"},
                ],
            },
            "messages": [
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=19)),
                 "text": "Can this arrive before the weekend? It's a gift."},
                {"direction": "FROM_SELLER", "at": _iso(now - timedelta(days=19)),
                 "text": "Yes — it's out for next-day delivery, signature required."},
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=17)),
                 "text": "Arrived, thank you! The casserole is heavier than I expected, in a good way."},
            ],
            "listing": {
                "title": "Sorrel 5-Piece Cast Iron Cookware Set",
                "description": "Enamelled cast iron: 24cm casserole, 20cm saucepan, 26cm skillet, "
                               "grill pan, trivet. Lifetime guarantee. Signature required on delivery.",
            },
            "refunds": [],
            "buyer_history": {
                "paypal_buyer_name": "Helen Vasquez",
                "first_order_at": _iso(now - timedelta(days=620)),
                "orders_count": 4,
                "lifetime_value": "731.00",
                "prior_disputes": 0,
                "deliveries_to_this_address": 4,
                "note": "Buyer messaged thanks after delivery, from the same account that now "
                        "reports the charge as unauthorised.",
            },
        },
        "PP-D-10045": {
            "order": {
                "order_id": "HF-24233",
                "placed_at": _iso(now - timedelta(days=7)),
                "dispatched_at": _iso(now - timedelta(days=6)),
                "status": "DELIVERED",
                "items": [
                    {"sku": "BW-PIL-PR-HON", "name": "Beeswax Pillar Candle, Pair — Honey",
                     "qty": 1, "unit_price": "24.50"}
                ],
                "item_total": "24.50",
                "shipping_total": "0.00",
                "order_total": "24.50",
                "ship_to": {
                    "name": "Tom Whitfield",
                    "address_line_1": "5 Beckford Lane",
                    "admin_area_2": "Bath",
                    "postal_code": "BA1 6NT",
                    "country_code": "GB",
                    "paypal_address_status": "CONFIRMED",
                    "matches_paypal_transaction_address": True,
                },
                "signature_required": False,
                "duplicate_charge_detected": True,
                "duplicate_note": "Checkout was submitted twice within 90 seconds. The second "
                                  "charge (2WD77906HN5529U) was voided and refunded the same day; "
                                  "only one parcel was shipped.",
            },
            "tracking": {
                "carrier_name": "ROYAL_MAIL",
                "carrier_display": "Royal Mail Tracked 48",
                "tracking_number": "RM520119884GB",
                "current_status": "DELIVERED",
                "delivered_at": _iso(now - timedelta(days=4)),
                "signed_by": None,
                "signature_image_on_file": False,
                "delivery_photo_on_file": True,
                "events": [
                    {"at": _iso(now - timedelta(days=6)), "status": "LABEL_CREATED",
                     "location": "Bristol, GB", "detail": "Shipment information received"},
                    {"at": _iso(now - timedelta(days=4)), "status": "DELIVERED",
                     "location": "Bath, GB", "detail": "Delivered"},
                ],
            },
            "messages": [
                {"direction": "FROM_BUYER", "at": _iso(now - timedelta(days=6)),
                 "text": "I think my card was charged twice at checkout."},
                {"direction": "FROM_SELLER", "at": _iso(now - timedelta(days=6)),
                 "text": "You're right — the duplicate charge has been refunded today, "
                         "refund reference 6RM88204KD9930L. It can take 3–5 days to show on "
                         "your statement."},
            ],
            "listing": {
                "title": "Beeswax Pillar Candle, Pair — Honey",
                "description": "Two 100% British beeswax pillar candles, 7x10cm, ~45h burn each.",
            },
            "refunds": [
                {
                    "refund_id": "6RM88204KD9930L",
                    "refunded_transaction_id": "2WD77906HN5529U",
                    "amount": {"currency_code": "USD", "value": "24.50"},
                    "status": "COMPLETED",
                    "create_time": _iso(now - timedelta(days=6)),
                    "reason": "Duplicate charge at checkout — second charge refunded in full.",
                }
            ],
            "buyer_history": {
                "paypal_buyer_name": "Tom Whitfield",
                "first_order_at": _iso(now - timedelta(days=95)),
                "orders_count": 2,
                "lifetime_value": "61.00",
                "prior_disputes": 0,
                "deliveries_to_this_address": 2,
            },
        },
    }


def fresh_disputes() -> list[dict[str, Any]]:
    return copy.deepcopy(DISPUTES)


def fresh_store() -> dict[str, dict[str, Any]]:
    return _store()
