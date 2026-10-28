"""The dispute agent: a Claude tool-use loop that ends in a resolution plan.

The loop is read-only. It gathers evidence with the six read tools and finishes
by calling submit_resolution_plan. Nothing it returns reaches PayPal until the
seller approves it in the UI.

Note on forcing the final tool: Claude Sonnet 5.5 and the other current models
reject tool_choice {"type": "any"|"tool"} with a 400, so the final call cannot
be forced by the parameter. Instead the tool is named in the prompt, and if a
turn ends without it we send one explicit instruction to call it. If that still
does not happen, the rules engine fills in and the plan is labelled as such.
"""
from __future__ import annotations

import json
import threading
import traceback
from decimal import Decimal
from typing import Any

from .. import db
from ..config import Settings, get_settings
from ..guardrails import allowed_offer_types, money, truncate_note
from ..paypal import get_client
from . import fallback
from .tools import ALL_TOOLS, READ_TOOL_NAMES, SUBMIT_TOOL_NAME, ToolBox

MAX_NUDGES = 2
RESULT_PREVIEW_CHARS = 1400

SYSTEM_PROMPT = """You are DisputeDesk, an agent that prepares PayPal dispute responses for a \
small online seller. You work for the seller, but your value to them is accuracy, not loyalty: \
a response that contests a claim the seller would lose costs them the money, the dispute-rate \
penalty and the customer.

Decide between exactly three actions:

- FIGHT - contest the claim by filing evidence with PayPal. Only when documented evidence \
(carrier delivery scan, signature, delivery photo, a completed refund, an address match) \
actually supports the seller.
- OFFER - propose a partial or full settlement. Right when the buyer has a genuine but partial \
grievance, e.g. one damaged item in a set. Never offer more than the disputed amount, and only \
use an offer_type that allowed_response_options.make_offer.offer_types lists for this dispute.
- ACCEPT - concede and refund in full. Right when the evidence is against the seller, for \
example a parcel the carrier never collected. Accepting early is better than losing slowly.

How to work:

1. Call get_dispute_details first. Then gather only the records that bear on this reason code - \
tracking for a not-received claim, the order record and refunds for a duplicate charge, buyer \
history for an unauthorised chargeback. You may call several tools in one turn.
2. Read allowed_response_options before planning an offer. If make_offer is absent, an offer is \
not available and you must choose FIGHT or ACCEPT.
3. Ground every claim in something you actually read. Quote tracking numbers, refund IDs, dates \
and amounts rather than paraphrasing. Do not invent evidence the records do not contain.
4. Then call submit_resolution_plan exactly once. That is how you finish; a reply without it is \
not an answer. Write response_note for the PayPal reviewer (factual, specific, under 2000 \
characters) and buyer_message for the customer (short, human, no jargon). Put every genuine \
weakness in risk_flags - the seller is relying on you to say what could still go wrong.

The seller reviews and approves your plan before anything is sent to PayPal."""


def _user_prompt(dispute_id: str) -> str:
    return (
        f"Prepare a response for PayPal dispute {dispute_id}. Gather what you need with the "
        "read-only tools, then submit your resolution plan."
    )


# --------------------------------------------------------------------------
# Plan normalisation
# --------------------------------------------------------------------------

def normalise_plan(raw: dict[str, Any], dispute: dict[str, Any]) -> dict[str, Any]:
    """Coerce a model-supplied plan into the shape the rest of the app relies on.

    Hard enforcement happens again in guardrails.py at submit time; this pass
    fixes what can be fixed and records the rest as risk flags.
    """
    plan: dict[str, Any] = {
        "action": str(raw.get("action", "")).upper(),
        "summary": str(raw.get("summary", "")).strip(),
        "reasoning": [str(x) for x in (raw.get("reasoning") or []) if str(x).strip()],
        "evidence": [],
        "offer": None,
        "response_note": truncate_note(raw.get("response_note")),
        "buyer_message": str(raw.get("buyer_message", "")).strip(),
        "risk_flags": [str(x) for x in (raw.get("risk_flags") or []) if str(x).strip()],
    }

    if plan["action"] not in {"FIGHT", "OFFER", "ACCEPT"}:
        plan["action"] = "ACCEPT"
        plan["risk_flags"].insert(
            0, f"Agent returned an unrecognised action {raw.get('action')!r}; defaulted to ACCEPT."
        )

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    plan["confidence"] = min(1.0, max(0.0, confidence))

    for item in raw.get("evidence") or []:
        if not isinstance(item, dict) or not item.get("evidence_type"):
            continue
        entry: dict[str, Any] = {
            "evidence_type": str(item["evidence_type"]),
            "description": str(item.get("description", "")).strip(),
        }
        tracking = item.get("tracking_info")
        if isinstance(tracking, dict) and tracking.get("tracking_number"):
            entry["tracking_info"] = {
                "carrier_name": str(tracking.get("carrier_name", "OTHER")),
                "tracking_number": str(tracking["tracking_number"]),
            }
        refund_ids = [str(r) for r in (item.get("refund_ids") or []) if str(r).strip()]
        if refund_ids:
            entry["refund_ids"] = refund_ids
        plan["evidence"].append(entry)

    if plan["action"] == "OFFER":
        offer = raw.get("offer") if isinstance(raw.get("offer"), dict) else {}
        allowed = allowed_offer_types(dispute)
        offer_type = str(offer.get("offer_type", "")) or (allowed[0] if allowed else "REFUND")
        cap = money((dispute.get("dispute_amount") or {}).get("value", "0"))
        try:
            value = money(offer.get("amount", cap))
        except Exception:
            value = cap
            plan["risk_flags"].insert(0, "Agent gave an unreadable offer amount; defaulted to the "
                                         "full disputed amount for review.")
        if Decimal(value) > Decimal(cap):
            plan["risk_flags"].insert(
                0, f"Agent proposed {value}, above the disputed {cap}; capped at {cap}."
            )
            value = cap
        if allowed and offer_type not in allowed:
            plan["risk_flags"].insert(
                0,
                f"Agent chose offer_type {offer_type!r}, which PayPal does not allow here; "
                f"switched to {allowed[0]}.",
            )
            offer_type = allowed[0]
        plan["offer"] = {"offer_type": offer_type, "amount": value}

    if plan["action"] == "FIGHT" and not plan["evidence"]:
        plan["risk_flags"].insert(
            0, "Agent chose FIGHT but filed no evidence; PayPal is unlikely to rule in the "
               "seller's favour without it."
        )
    if not plan["response_note"]:
        plan["response_note"] = plan["summary"][:2000]
    return plan


# --------------------------------------------------------------------------
# Claude loop
# --------------------------------------------------------------------------

def _create_message(client, settings: Settings, **kwargs):
    """Send a request, dropping optional params the configured model rejects."""
    extras: dict[str, Any] = {}
    if settings.claude_effort:
        extras["output_config"] = {"effort": settings.claude_effort}
    try:
        return client.messages.create(**kwargs, **extras)
    except TypeError:
        # SDK predates the parameter.
        return client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - re-raised below unless it is a 400 on extras
        if extras and getattr(exc, "status_code", None) == 400:
            return client.messages.create(**kwargs)
        raise


def _blocks_to_payload(content) -> list[dict[str, Any]]:
    out = []
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            out.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        elif getattr(block, "type", None) == "text":
            out.append({"type": "text", "text": block.text})
        else:
            out.append({"type": getattr(block, "type", "unknown")})
    return out


def run_with_claude(run_id: str, box: ToolBox, settings: Settings) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [{"role": "user", "content": _user_prompt(box._dispute_id)}]
    nudges = 0
    submitted: dict[str, Any] | None = None

    for turn in range(1, settings.max_agent_turns + 1):
        db.add_step(run_id, "status", f"Turn {turn}: asking {settings.claude_model}")
        response = _create_message(
            client,
            settings,
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=ALL_TOOLS,
            tool_choice={"type": "auto"},
        )

        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError(
                "The model declined this request "
                f"({getattr(getattr(response, 'stop_details', None), 'category', 'unknown')})."
            )

        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text.strip():
                db.add_step(run_id, "thinking", "Agent note", {"text": block.text.strip()})

        usage = getattr(response, "usage", None)
        if usage is not None:
            db.add_step(
                run_id,
                "status",
                "Tokens this turn",
                {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                },
            )

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

        if not tool_uses:
            if nudges < MAX_NUDGES:
                nudges += 1
                db.add_step(
                    run_id,
                    "status",
                    "Turn ended with no tool call; instructing the agent to submit its plan",
                )
                messages.append({"role": "assistant", "content": _blocks_to_payload(response.content)})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not submitted a plan. Call the submit_resolution_plan tool "
                            "now with your final recommendation, using the records you have "
                            "already read. Do not reply with text."
                        ),
                    }
                )
                continue
            break

        messages.append({"role": "assistant", "content": _blocks_to_payload(response.content)})

        # A submit call ends the run, even if the model batched it with reads.
        submit_calls = [b for b in tool_uses if b.name == SUBMIT_TOOL_NAME]
        if submit_calls:
            raw = submit_calls[0].input
            if isinstance(raw, str):
                raw = json.loads(raw)
            submitted = raw
            db.add_step(run_id, "tool_call", SUBMIT_TOOL_NAME, {"input": raw})
            break

        results = []
        for call in tool_uses:
            db.add_step(run_id, "tool_call", call.name, {"input": call.input})
            if call.name in READ_TOOL_NAMES:
                try:
                    payload = box.call_as_text(call.name)
                    is_error = False
                except Exception as exc:  # noqa: BLE001 - surfaced to the model as a tool error
                    payload = f"Tool failed: {exc}"
                    is_error = True
            else:
                payload = f"Unknown tool {call.name!r}."
                is_error = True
            db.add_step(
                run_id,
                "tool_result",
                call.name,
                {
                    "preview": payload[:RESULT_PREVIEW_CHARS],
                    "truncated": len(payload) > RESULT_PREVIEW_CHARS,
                    "chars": len(payload),
                    "is_error": is_error,
                },
            )
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": payload,
            }
            if is_error:
                result["is_error"] = True
            results.append(result)

        # All results from one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})

    if submitted is None:
        db.add_step(
            run_id,
            "status",
            "Agent did not submit a plan within the turn limit; using the rules engine instead",
        )
        plan = fallback.build_plan(box)
        plan["risk_flags"].insert(
            0, "Claude did not return a plan within the turn limit; this plan came from the "
               "offline rules engine."
        )
        plan["engine"] = "rules"
        return plan

    plan = normalise_plan(submitted, box.dispute)
    plan["engine"] = "claude"
    plan["model"] = settings.claude_model
    return plan


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def execute_run(run_id: str, dispute_id: str, settings: Settings) -> dict[str, Any]:
    """Run the agent synchronously. Used by the thread and directly by tests."""
    box = ToolBox(get_client(settings), dispute_id)
    try:
        db.add_step(run_id, "status", "Loading dispute from PayPal "
                                     f"({'sandbox' if settings.live_paypal else 'mock'})")
        if settings.agent_is_live:
            plan = run_with_claude(run_id, box, settings)
        else:
            db.add_step(
                run_id,
                "status",
                "ANTHROPIC_API_KEY is not set — running the offline rules engine",
            )
            for tool in ("get_dispute_details", "get_order_record", "get_shipment_tracking",
                         "get_customer_messages", "get_store_policies", "get_buyer_history"):
                db.add_step(run_id, "tool_call", tool, {"input": {}})
                text = box.call_as_text(tool)
                db.add_step(
                    run_id,
                    "tool_result",
                    tool,
                    {"preview": text[:RESULT_PREVIEW_CHARS],
                     "truncated": len(text) > RESULT_PREVIEW_CHARS,
                     "chars": len(text), "is_error": False},
                )
            plan = fallback.build_plan(box)
        db.add_step(run_id, "plan", f"Recommendation: {plan['action']}", plan)
        db.finish_run(run_id, plan)
        return plan
    except Exception as exc:  # noqa: BLE001 - the run must record its own failure
        detail = f"{type(exc).__name__}: {exc}"
        db.add_step(run_id, "error", "Agent run failed", {"error": detail,
                                                          "traceback": traceback.format_exc()[-2000:]})
        db.fail_run(run_id, detail)
        raise


def start_run(dispute_id: str, settings: Settings | None = None) -> str:
    """Create a run and drive it on a background thread; returns the run id."""
    settings = settings or get_settings()
    engine = "claude" if settings.agent_is_live else "rules"
    run_id = db.create_run(
        dispute_id, engine, settings.claude_model if settings.agent_is_live else None
    )

    def _target() -> None:
        try:
            execute_run(run_id, dispute_id, settings)
        except Exception:  # noqa: BLE001 - already recorded on the run
            pass

    threading.Thread(target=_target, name=f"agent-{run_id}", daemon=True).start()
    return run_id
