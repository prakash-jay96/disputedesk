"""The rules engine and plan normalisation. No API key needed."""
import pytest

from app.agent.fallback import build_plan
from app.agent.runner import normalise_plan
from app.agent.tools import ALL_TOOLS, READ_TOOL_NAMES, SUBMIT_TOOL, ToolBox

EXPECTED = {
    "PP-D-10041": "FIGHT",    # delivered, signed for
    "PP-D-10042": "OFFER",    # one chipped mug of four
    "PP-D-10043": "ACCEPT",   # never collected by the carrier
    "PP-D-10044": "FIGHT",    # unauthorised, but signed for at a verified address
    "PP-D-10045": "FIGHT",    # duplicate charge already refunded
}


@pytest.mark.parametrize("dispute_id,action", EXPECTED.items())
def test_rules_engine_picks_the_right_action(paypal, dispute_id, action):
    plan = build_plan(ToolBox(paypal, dispute_id))
    assert plan["action"] == action
    assert plan["summary"]
    assert plan["reasoning"]
    assert plan["response_note"] and len(plan["response_note"]) <= 2000
    assert plan["buyer_message"]
    assert plan["engine"] == "rules"


def test_partial_refund_is_one_mug_not_the_whole_set(paypal):
    plan = build_plan(ToolBox(paypal, "PP-D-10042"))
    assert plan["offer"] == {"offer_type": "REFUND", "amount": "15.50"}


def test_duplicate_charge_is_fought_with_the_refund_id(paypal):
    plan = build_plan(ToolBox(paypal, "PP-D-10045"))
    assert [e["evidence_type"] for e in plan["evidence"]] == ["PROOF_OF_REFUND"]
    assert plan["evidence"][0]["refund_ids"] == ["6RM88204KD9930L"]


def test_delivery_claims_carry_tracking_evidence(paypal):
    plan = build_plan(ToolBox(paypal, "PP-D-10041"))
    types = [e["evidence_type"] for e in plan["evidence"]]
    assert "PROOF_OF_FULFILLMENT" in types and "PROOF_OF_DELIVERY_SIGNATURE" in types
    tracking = plan["evidence"][0]["tracking_info"]
    assert tracking["tracking_number"] == "JD0002186417392041"


def test_accepted_claim_files_no_evidence(paypal):
    plan = build_plan(ToolBox(paypal, "PP-D-10043"))
    assert plan["evidence"] == [] and plan["offer"] is None


def test_every_rules_plan_flags_that_it_is_not_claude(paypal):
    for dispute_id in EXPECTED:
        plan = build_plan(ToolBox(paypal, dispute_id))
        assert any("rules engine" in flag for flag in plan["risk_flags"])


# --- tool surface -----------------------------------------------------------

def test_tool_surface_is_read_only_plus_one_submit():
    assert len(ALL_TOOLS) == 7
    assert SUBMIT_TOOL["name"] == "submit_resolution_plan"
    assert READ_TOOL_NAMES == {
        "get_dispute_details", "get_order_record", "get_shipment_tracking",
        "get_customer_messages", "get_store_policies", "get_buyer_history",
    }
    for tool in ALL_TOOLS:
        assert tool["input_schema"]["additionalProperties"] is False


def test_toolbox_reports_missing_store_records(paypal, monkeypatch):
    box = ToolBox(paypal, "PP-D-10041")
    monkeypatch.setattr(box, "_store", None)
    monkeypatch.setattr(box, "_store_loaded", True)
    assert box.call("get_order_record")["available"] is False


# --- plan normalisation -----------------------------------------------------

DISPUTE = {
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "dispute_amount": {"currency_code": "USD", "value": "62.00"},
    "allowed_response_options": {"make_offer": {"offer_types": ["REFUND"]}},
}


def test_offer_above_the_disputed_amount_is_capped():
    plan = normalise_plan(
        {"action": "OFFER", "confidence": 0.8, "summary": "s", "reasoning": ["r"],
         "evidence": [], "offer": {"offer_type": "REFUND", "amount": "500"},
         "response_note": "n", "buyer_message": "b", "risk_flags": []},
        DISPUTE,
    )
    assert plan["offer"]["amount"] == "62.00"
    assert any("capped" in f for f in plan["risk_flags"])


def test_disallowed_offer_type_is_switched_and_flagged():
    plan = normalise_plan(
        {"action": "OFFER", "confidence": 0.8, "summary": "s", "reasoning": [],
         "evidence": [], "offer": {"offer_type": "REFUND_WITH_RETURN", "amount": "10"},
         "response_note": "n", "buyer_message": "b", "risk_flags": []},
        DISPUTE,
    )
    assert plan["offer"]["offer_type"] == "REFUND"
    assert any("does not allow" in f for f in plan["risk_flags"])


def test_unknown_action_falls_back_to_accept():
    plan = normalise_plan({"action": "SUE_THEM", "confidence": 2, "summary": "s"}, DISPUTE)
    assert plan["action"] == "ACCEPT"
    assert plan["confidence"] == 1.0


def test_long_response_note_is_truncated():
    plan = normalise_plan(
        {"action": "ACCEPT", "confidence": 0.5, "response_note": "x" * 4000}, DISPUTE
    )
    assert len(plan["response_note"]) == 2000


def test_fight_without_evidence_is_flagged():
    plan = normalise_plan(
        {"action": "FIGHT", "confidence": 0.9, "summary": "s", "evidence": [],
         "response_note": "n"}, DISPUTE
    )
    assert any("no evidence" in f for f in plan["risk_flags"])
