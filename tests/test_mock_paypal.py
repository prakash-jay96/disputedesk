import pytest

from app.paypal.base import PayPalError


def test_five_fixture_disputes_in_api_shape(paypal):
    disputes = paypal.list_disputes()
    assert len(disputes) == 5
    for dispute in disputes:
        assert dispute["dispute_id"].startswith("PP-D-")
        assert set(dispute["dispute_amount"]) == {"currency_code", "value"}
        assert dispute["status"] == "WAITING_FOR_SELLER_RESPONSE"
        assert dispute["disputed_transactions"][0]["seller_transaction_id"]
        assert dispute["seller_response_due_date"].endswith("Z")


def test_every_dispute_has_a_store_record(paypal):
    for dispute in paypal.list_disputes():
        record = paypal.get_store_record(dispute["dispute_id"])
        assert record and "order" in record and "tracking" in record


def test_unknown_dispute_is_404(paypal):
    with pytest.raises(PayPalError) as exc:
        paypal.get_dispute("PP-D-00000")
    assert exc.value.status_code == 404


def test_provide_evidence_moves_dispute_under_review(paypal):
    endpoint, body, response = paypal.provide_evidence(
        "PP-D-10041", [{"evidence_type": "PROOF_OF_FULFILLMENT", "notes": "delivered"}]
    )
    assert endpoint.endswith("/PP-D-10041/provide-evidence")
    assert body["evidences"][0]["evidence_type"] == "PROOF_OF_FULFILLMENT"
    assert "links" in response
    assert paypal.get_dispute("PP-D-10041")["status"] == "UNDER_REVIEW"


def test_make_offer_records_the_offer(paypal):
    _, body, _ = paypal.make_offer("PP-D-10042", "partial", "REFUND", "15.50", "USD")
    assert body["offer_amount"] == {"currency_code": "USD", "value": "15.50"}
    dispute = paypal.get_dispute("PP-D-10042")
    assert dispute["status"] == "WAITING_FOR_BUYER_RESPONSE"
    assert dispute["offer"]["seller_offered_amount"]["value"] == "15.50"


def test_make_offer_rejects_offer_type_paypal_disallows(paypal):
    with pytest.raises(PayPalError) as exc:
        paypal.make_offer("PP-D-10044", "note", "REFUND", "420.00", "USD")
    assert exc.value.status_code == 422


def test_accept_claim_resolves_in_buyer_favour(paypal):
    paypal.accept_claim("PP-D-10043", "parcel never collected")
    dispute = paypal.get_dispute("PP-D-10043")
    assert dispute["status"] == "RESOLVED"
    assert dispute["dispute_outcome"]["outcome_code"] == "RESOLVED_BUYER_FAVOUR"


def test_second_response_is_rejected(paypal):
    paypal.accept_claim("PP-D-10043", "note")
    with pytest.raises(PayPalError) as exc:
        paypal.accept_claim("PP-D-10043", "again")
    assert exc.value.status_code == 422


def test_send_message_keeps_the_response_window_open(paypal):
    paypal.send_message("PP-D-10041", "hello")
    dispute = paypal.get_dispute("PP-D-10041")
    assert dispute["status"] == "WAITING_FOR_SELLER_RESPONSE"
    assert dispute["messages"][-1]["posted_by"] == "SELLER"
