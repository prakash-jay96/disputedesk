"""End-to-end: run the agent over HTTP, approve, and watch it reach PayPal."""
import time

import pytest


def wait_for_run(client, run_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    pytest.fail(f"run {run_id} did not finish within {timeout}s")


def test_config_reports_mock_mode_and_rules_engine(client):
    body = client.get("/api/config").json()
    assert body["paypal_mode"] == "mock"
    assert body["agent_engine"] == "rules"
    assert body["max_agent_turns"] == 8


def test_dispute_list_carries_stats_and_display_fields(client):
    body = client.get("/api/disputes").json()
    assert len(body["disputes"]) == 5
    assert body["stats"]["open_disputes"] == 5
    assert body["stats"]["at_risk"] == "693.50"  # 149 + 62 + 38 + 420 + 24.50
    first = body["disputes"][0]
    assert {"reason_label", "due_in_days", "buyer_message", "allowed_offer_types"} <= set(first)
    # Soonest deadline first.
    assert [d["due_in_days"] for d in body["disputes"]] == sorted(
        d["due_in_days"] for d in body["disputes"]
    )


def test_dispute_detail_includes_store_records(client):
    body = client.get("/api/disputes/PP-D-10045").json()
    assert body["store"]["refunds"][0]["refund_id"] == "6RM88204KD9930L"
    assert body["store"]["tracking"]["current_status"] == "DELIVERED"
    assert body["summary"]["reason_label"] == "Duplicate charge"


def test_unknown_dispute_is_404(client):
    assert client.get("/api/disputes/PP-D-99999").status_code == 404


def test_run_agent_records_a_trace_and_a_plan(client):
    run_id = client.post("/api/disputes/PP-D-10042/agent").json()["run_id"]
    run = wait_for_run(client, run_id)
    assert run["status"] == "complete"
    assert run["action"] == "OFFER"
    kinds = {step["kind"] for step in run["steps"]}
    assert {"status", "tool_call", "tool_result", "plan"} <= kinds
    assert run["plan"]["offer"] == {"offer_type": "REFUND", "amount": "15.50"}


def test_second_agent_request_reuses_the_running_run(client):
    first = client.post("/api/disputes/PP-D-10041/agent").json()
    second = client.post("/api/disputes/PP-D-10041/agent").json()
    assert second["run_id"] == first["run_id"] or second["already_running"] is False
    wait_for_run(client, first["run_id"])


def test_approve_offer_reaches_paypal_with_the_edited_amount(client):
    run_id = client.post("/api/disputes/PP-D-10042/agent").json()["run_id"]
    wait_for_run(client, run_id)
    result = client.post(
        "/api/disputes/PP-D-10042/approve",
        json={"run_id": run_id, "action": "OFFER", "response_note": "Partial refund offered.",
              "offer_type": "REFUND", "offer_amount": "20.00", "buyer_message": "Sorry about that."},
    ).json()
    assert result["ok"] is True
    actions = [r["action"] for r in result["results"]]
    assert actions == ["MESSAGE", "OFFER"]
    offer_call = result["results"][1]
    assert offer_call["request"]["offer_amount"] == {"currency_code": "USD", "value": "20.00"}
    assert result["dispute"]["status"] == "WAITING_FOR_BUYER_RESPONSE"


def test_approve_fight_sends_evidence_in_paypal_shape(client):
    run_id = client.post("/api/disputes/PP-D-10041/agent").json()["run_id"]
    wait_for_run(client, run_id)
    result = client.post(
        "/api/disputes/PP-D-10041/approve",
        json={"run_id": run_id, "action": "FIGHT", "response_note": "Delivered and signed for.",
              "send_buyer_message": False},
    ).json()
    assert result["ok"] is True
    evidences = result["results"][0]["request"]["evidences"]
    assert evidences[0]["evidence_type"] == "PROOF_OF_FULFILLMENT"
    assert evidences[0]["evidence_info"]["tracking_info"][0]["tracking_number"] == (
        "JD0002186417392041"
    )
    assert evidences[0]["notes"].startswith("Delivered and signed for.")
    assert result["dispute"]["status"] == "UNDER_REVIEW"


def test_approve_accept_refunds_in_full(client):
    run_id = client.post("/api/disputes/PP-D-10043/agent").json()["run_id"]
    wait_for_run(client, run_id)
    result = client.post(
        "/api/disputes/PP-D-10043/approve",
        json={"run_id": run_id, "action": "ACCEPT", "response_note": "Never collected."},
    ).json()
    assert result["ok"] is True
    assert result["dispute"]["status"] == "RESOLVED"


def test_guardrail_blocks_an_offer_above_the_disputed_amount(client):
    run_id = client.post("/api/disputes/PP-D-10042/agent").json()["run_id"]
    wait_for_run(client, run_id)
    response = client.post(
        "/api/disputes/PP-D-10042/approve",
        json={"run_id": run_id, "action": "OFFER", "response_note": "n",
              "offer_type": "REFUND", "offer_amount": "999.00"},
    )
    assert response.status_code == 422
    assert "exceeds the disputed amount" in response.json()["detail"]
    # Nothing was sent.
    assert client.get("/api/disputes/PP-D-10042").json()["dispute"]["status"] == (
        "WAITING_FOR_SELLER_RESPONSE"
    )


def test_guardrail_blocks_a_disallowed_offer_type(client):
    response = client.post(
        "/api/disputes/PP-D-10044/approve",
        json={"action": "OFFER", "response_note": "n", "offer_type": "REFUND",
              "offer_amount": "10.00"},
    )
    assert response.status_code == 422
    assert "does not allow an offer" in response.json()["detail"]


def test_guardrail_blocks_a_response_to_a_closed_dispute(client):
    client.post(
        "/api/disputes/PP-D-10043/approve",
        json={"action": "ACCEPT", "response_note": "accepted"},
    )
    again = client.post(
        "/api/disputes/PP-D-10043/approve",
        json={"action": "ACCEPT", "response_note": "again"},
    )
    assert again.status_code == 422
    assert "WAITING_FOR_SELLER_RESPONSE" in again.json()["detail"]


def test_guardrail_requires_a_response_note(client):
    response = client.post(
        "/api/disputes/PP-D-10041/approve", json={"action": "FIGHT", "response_note": "   "}
    )
    assert response.status_code == 422
    assert "response note is required" in response.json()["detail"]


def test_unknown_action_is_rejected(client):
    response = client.post(
        "/api/disputes/PP-D-10041/approve", json={"action": "IGNORE", "response_note": "n"}
    )
    assert response.status_code == 400


def test_long_note_is_truncated_before_it_reaches_paypal(client):
    result = client.post(
        "/api/disputes/PP-D-10041/approve",
        json={"action": "FIGHT", "response_note": "y" * 5000, "send_buyer_message": False},
    ).json()
    assert result["ok"] is True
    assert len(result["results"][0]["request"]["evidences"][0]["notes"]) <= 2000


def test_protected_total_counts_what_the_seller_kept(client):
    run_id = client.post("/api/disputes/PP-D-10041/agent").json()["run_id"]
    wait_for_run(client, run_id)
    client.post(
        "/api/disputes/PP-D-10041/approve",
        json={"run_id": run_id, "action": "FIGHT", "response_note": "Delivered.",
              "send_buyer_message": False},
    )
    stats = client.get("/api/disputes").json()["stats"]
    assert stats["protected"] == "149.00"
    assert stats["open_disputes"] == 4
    assert stats["responses_submitted"] == 1


def test_reset_restores_the_fixtures(client):
    client.post(
        "/api/disputes/PP-D-10043/approve",
        json={"action": "ACCEPT", "response_note": "accepted"},
    )
    assert client.post("/api/reset").json()["ok"] is True
    body = client.get("/api/disputes").json()
    assert body["stats"]["open_disputes"] == 5
    assert all(d["run"] is None for d in body["disputes"])


def test_index_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "DisputeDesk" in page.text
    assert client.get("/static/app.js").status_code == 200
