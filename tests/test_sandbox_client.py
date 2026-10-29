"""The real PayPal client, driven through httpx.MockTransport.

No network: every request is answered locally, so these assert the exact
wire shapes PayPal expects.
"""
import json

import httpx
import pytest

from app.paypal.base import PayPalError
from app.paypal.sandbox import SandboxDisputeClient
from app.store import SellerStore

BASE = "https://api-m.sandbox.sandboxtest.invalid"

DISPUTE = {
    "dispute_id": "PP-D-777",
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "dispute_amount": {"currency_code": "USD", "value": "80.00"},
    "disputed_transactions": [
        {"seller_transaction_id": "TXN-1", "invoice_number": "INV-9"}
    ],
}


class Recorder:
    """Answers PayPal calls and keeps every request for inspection."""

    def __init__(self, **overrides):
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.overrides = overrides

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/v1/oauth2/token":
            self.token_calls += 1
            if "token" in self.overrides:
                return self.overrides["token"]
            return httpx.Response(
                200, json={"access_token": f"A-{self.token_calls}", "expires_in": 32400}
            )
        if path in self.overrides:
            return self.overrides[path]
        if path == "/v1/customer/disputes":
            return httpx.Response(200, json={"items": [{"dispute_id": "PP-D-777"}]})
        if path == "/v1/customer/disputes/PP-D-777":
            return httpx.Response(200, json=DISPUTE)
        if path.endswith(("/provide-evidence", "/make-offer", "/accept-claim", "/send-message")):
            return httpx.Response(200, json={"links": [{"rel": "self", "href": BASE}]})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def by_path(self, suffix: str) -> httpx.Request:
        return next(r for r in self.requests if r.url.path.endswith(suffix))


def make_client(recorder: Recorder, store=None) -> SandboxDisputeClient:
    return SandboxDisputeClient(
        BASE, "client-id", "secret", store=store,
        transport=httpx.MockTransport(recorder.handler),
    )


# --- auth -------------------------------------------------------------------

def test_credentials_are_required():
    with pytest.raises(PayPalError, match="PAYPAL_CLIENT_ID"):
        SandboxDisputeClient(BASE, "", "", transport=None)


def test_token_is_fetched_once_and_reused():
    rec = Recorder()
    client = make_client(rec)
    assert client.access_token() == "A-1"
    assert client.access_token() == "A-1"
    client.get_dispute("PP-D-777")
    assert rec.token_calls == 1


def test_token_is_refreshed_once_it_expires():
    rec = Recorder()
    client = make_client(rec)
    client.access_token()
    client._token_expires_at = 0  # pretend it aged out
    assert client.access_token() == "A-2"
    assert rec.token_calls == 2


def test_token_uses_basic_auth_and_client_credentials_grant():
    rec = Recorder()
    make_client(rec).access_token()
    request = rec.by_path("/v1/oauth2/token")
    assert b"grant_type=client_credentials" in request.content
    assert request.headers["authorization"].startswith("Basic ")


def test_bad_credentials_raise_with_the_status_and_body():
    rec = Recorder(token=httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(PayPalError) as exc:
        make_client(rec).access_token()
    assert exc.value.status_code == 401
    assert exc.value.body == {"error": "invalid_client"}


def test_a_network_failure_becomes_a_paypal_error():
    def boom(request):
        raise httpx.ConnectError("no route to host")

    client = SandboxDisputeClient(
        BASE, "id", "secret", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(PayPalError, match="could not reach PayPal"):
        client.access_token()


# --- reads ------------------------------------------------------------------

def test_get_dispute_sends_a_bearer_token():
    rec = Recorder()
    assert make_client(rec).get_dispute("PP-D-777")["dispute_id"] == "PP-D-777"
    assert rec.by_path("/disputes/PP-D-777").headers["authorization"] == "Bearer A-1"


def test_list_disputes_enriches_summaries_with_the_detail_call():
    rec = Recorder()
    disputes = make_client(rec).list_disputes()
    assert len(disputes) == 1
    # The list endpoint omits these; the detail call is what supplies them.
    assert disputes[0]["status"] == "WAITING_FOR_SELLER_RESPONSE"
    assert rec.by_path("/v1/customer/disputes").url.params["page_size"] == "50"


def test_a_failing_detail_call_falls_back_to_the_summary():
    rec = Recorder(**{"/v1/customer/disputes/PP-D-777": httpx.Response(500, json={"e": 1})})
    disputes = make_client(rec).list_disputes()
    assert disputes == [{"dispute_id": "PP-D-777"}]


def test_an_error_response_raises_with_the_parsed_body():
    rec = Recorder(**{"/v1/customer/disputes/PP-D-777": httpx.Response(
        422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "X"}]})})
    with pytest.raises(PayPalError) as exc:
        make_client(rec).get_dispute("PP-D-777")
    assert exc.value.status_code == 422
    assert exc.value.body["details"][0]["issue"] == "X"


# --- writes -----------------------------------------------------------------

def test_provide_evidence_is_multipart_with_a_json_part_named_input():
    rec = Recorder()
    evidences = [{
        "evidence_type": "PROOF_OF_FULFILLMENT",
        "notes": "delivered",
        "evidence_info": {"tracking_info": [{"carrier_name": "DHL", "tracking_number": "JD1"}]},
    }]
    endpoint, body, response = make_client(rec).provide_evidence("PP-D-777", evidences)

    assert endpoint == "/v1/customer/disputes/PP-D-777/provide-evidence"
    assert body == {"evidences": evidences}
    assert "links" in response

    request = rec.by_path("/provide-evidence")
    assert request.headers["content-type"].startswith("multipart/form-data")
    content = request.content
    assert b'name="input"' in content
    assert b"application/json" in content
    # The JSON part carries the evidences verbatim.
    start = content.index(b"{")
    end = content.rindex(b"}") + 1
    assert json.loads(content[start:end]) == {"evidences": evidences}


def test_make_offer_sends_the_documented_body():
    rec = Recorder()
    endpoint, body, _ = make_client(rec).make_offer(
        "PP-D-777", "partial refund", "REFUND", "20.00", "USD"
    )
    assert endpoint.endswith("/PP-D-777/make-offer")
    assert body == {
        "note": "partial refund",
        "offer_amount": {"currency_code": "USD", "value": "20.00"},
        "offer_type": "REFUND",
    }
    assert json.loads(rec.by_path("/make-offer").content) == body


def test_accept_claim_sends_only_a_note():
    rec = Recorder()
    _, body, _ = make_client(rec).accept_claim("PP-D-777", "we accept")
    assert body == {"note": "we accept"}
    assert json.loads(rec.by_path("/accept-claim").content) == body


def test_send_message_sends_only_a_message():
    rec = Recorder()
    _, body, _ = make_client(rec).send_message("PP-D-777", "hello")
    assert body == {"message": "hello"}
    assert json.loads(rec.by_path("/send-message").content) == body


def test_an_empty_204_response_does_not_crash():
    rec = Recorder(**{"/v1/customer/disputes/PP-D-777/accept-claim": httpx.Response(204)})
    _, _, response = make_client(rec).accept_claim("PP-D-777", "note")
    assert response == {"status_code": 204}


# --- seller store -----------------------------------------------------------

def test_store_record_is_found_by_invoice_number():
    rec = Recorder()
    store = SellerStore([{ "match": {"invoice_number": "INV-9"}, "order": {"order_id": "INV-9"}}])
    record = make_client(rec, store=store).get_store_record("PP-D-777")
    assert record["order"]["order_id"] == "INV-9"


def test_store_record_is_none_when_the_seller_has_no_matching_order():
    rec = Recorder()
    store = SellerStore([{"match": {"invoice_number": "SOMETHING-ELSE"}}])
    assert make_client(rec, store=store).get_store_record("PP-D-777") is None


def test_no_store_configured_returns_none():
    assert make_client(Recorder()).get_store_record("PP-D-777") is None
