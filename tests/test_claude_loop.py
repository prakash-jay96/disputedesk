"""The Claude tool-use loop, driven against a stubbed SDK.

These tests never call the API: they script the responses so the loop's own
behaviour is what is under test.
"""
import copy

import pytest

from app import db
from app.agent import runner
from app.config import get_settings


class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def text(body):
    return Block(type="text", text=body)


def tool(name, call_id="t1", payload=None):
    return Block(type="tool_use", id=call_id, name=name, input=payload or {})


class Response:
    def __init__(self, content, stop_reason="tool_use", stop_details=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = Block(input_tokens=1200, output_tokens=240)


class BadRequest(Exception):
    status_code = 400


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        # The runner mutates one messages list in place, so snapshot it.
        self.calls.append(copy.deepcopy(kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


PLAN = {
    "action": "FIGHT",
    "confidence": 0.9,
    "summary": "Delivered and signed for.",
    "reasoning": ["Tracking reads DELIVERED."],
    "evidence": [
        {
            "evidence_type": "PROOF_OF_FULFILLMENT",
            "description": "DHL tracking shows delivery.",
            "tracking_info": {"carrier_name": "DHL_EXPRESS", "tracking_number": "JD0002186417392041"},
        }
    ],
    "response_note": "The order was delivered.",
    "buyer_message": "Tracking shows it arrived.",
    "risk_flags": [],
}


@pytest.fixture
def claude(monkeypatch):
    """Scripts the SDK and returns the fake client for assertions."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import anthropic

    holder = {}

    def install(script):
        client = FakeClient(script)
        holder["client"] = client
        monkeypatch.setattr(anthropic, "Anthropic", lambda **_: client)
        return client

    holder["install"] = install
    return holder


def run(dispute_id="PP-D-10041"):
    run_id = db.create_run(dispute_id, "claude", "claude-sonnet-5-5")
    plan = runner.execute_run(run_id, dispute_id, get_settings())
    return run_id, plan, db.get_run(run_id)


def test_loop_reads_tools_then_submits_a_plan(claude):
    client = claude["install"](
        [
            Response([
                text("Let me check the dispute and the tracking."),
                tool("get_dispute_details", "a"),
                tool("get_shipment_tracking", "b"),
            ]),
            Response([tool("submit_resolution_plan", "c", PLAN)]),
        ]
    )
    run_id, plan, stored = run()

    assert plan["action"] == "FIGHT"
    assert plan["engine"] == "claude"
    assert plan["model"] == "claude-sonnet-5-5"
    assert stored["status"] == "complete"

    labels = [s["label"] for s in stored["steps"] if s["kind"] == "tool_result"]
    assert labels == ["get_dispute_details", "get_shipment_tracking"]
    assert any(s["kind"] == "thinking" for s in stored["steps"])
    assert client.messages.calls[0]["tools"][0]["name"] == "get_dispute_details"


def test_parallel_tool_results_come_back_in_one_user_message(claude):
    client = claude["install"](
        [
            Response([tool("get_dispute_details", "a"), tool("get_order_record", "b")]),
            Response([tool("submit_resolution_plan", "c", PLAN)]),
        ]
    )
    run()
    second_call_messages = client.messages.calls[1]["messages"]
    result_messages = [m for m in second_call_messages if m["role"] == "user"
                       and isinstance(m["content"], list)]
    assert len(result_messages) == 1
    assert [b["tool_use_id"] for b in result_messages[0]["content"]] == ["a", "b"]


def test_tool_choice_is_auto_because_forcing_is_rejected_by_the_model(claude):
    client = claude["install"]([Response([tool("submit_resolution_plan", "c", PLAN)])])
    run()
    for call in client.messages.calls:
        assert call["tool_choice"] == {"type": "auto"}


def test_a_turn_with_no_tool_call_is_nudged_to_submit(claude):
    client = claude["install"](
        [
            Response([text("I think the seller should fight this.")], stop_reason="end_turn"),
            Response([tool("submit_resolution_plan", "c", PLAN)]),
        ]
    )
    run_id, plan, stored = run()

    assert plan["engine"] == "claude"
    assert any("instructing the agent to submit" in (s["label"] or "") for s in stored["steps"])
    nudge = client.messages.calls[1]["messages"][-1]
    assert nudge["role"] == "user"
    assert "submit_resolution_plan" in nudge["content"]


def test_rules_engine_fills_in_when_the_agent_never_submits(claude):
    claude["install"](
        [Response([text("hmm")], stop_reason="end_turn") for _ in range(runner.MAX_NUDGES + 1)]
    )
    run_id, plan, stored = run()

    assert plan["engine"] == "rules"
    assert plan["action"] == "FIGHT"  # the rules engine reaches the same call here
    assert any("did not return a plan" in flag for flag in plan["risk_flags"])
    assert stored["status"] == "complete"


def test_an_unsupported_output_config_is_dropped_and_the_call_retried(claude):
    client = claude["install"](
        [BadRequest("effort is not supported"), Response([tool("submit_resolution_plan", "c", PLAN)])]
    )
    run_id, plan, stored = run()

    assert plan["engine"] == "claude"
    assert "output_config" in client.messages.calls[0]
    assert "output_config" not in client.messages.calls[1]


def test_a_refusal_fails_the_run_rather_than_inventing_a_plan(claude):
    claude["install"](
        [Response([], stop_reason="refusal", stop_details=Block(type="refusal", category="cyber"))]
    )
    run_id = db.create_run("PP-D-10041", "claude", "claude-sonnet-5-5")
    with pytest.raises(RuntimeError, match="declined"):
        runner.execute_run(run_id, "PP-D-10041", get_settings())
    stored = db.get_run(run_id)
    assert stored["status"] == "error"
    assert stored["plan"] is None


def test_a_model_plan_that_over_refunds_is_capped_by_normalisation(claude):
    over = {**PLAN, "action": "OFFER", "offer": {"offer_type": "REFUND", "amount": "5000"},
            "evidence": []}
    claude["install"]([Response([tool("submit_resolution_plan", "c", over)])])
    _, plan, _ = run("PP-D-10042")
    assert plan["offer"]["amount"] == "62.00"
    assert any("capped" in flag for flag in plan["risk_flags"])


def test_the_loop_stops_at_the_turn_limit(claude, monkeypatch):
    monkeypatch.setenv("MAX_AGENT_TURNS", "3")
    client = claude["install"](
        [Response([tool("get_dispute_details", f"t{i}")]) for i in range(3)]
    )
    _, plan, _ = run()
    assert len(client.messages.calls) == 3
    assert plan["engine"] == "rules"  # no plan arrived, so the rules engine answered
