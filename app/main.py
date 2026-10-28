"""DisputeDesk: FastAPI app, JSON API and the seller dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .agent import runner
from .config import get_settings
from .guardrails import (
    GuardrailError,
    allowed_offer_types,
    check_offer,
    check_status,
    dispute_currency,
    money,
    truncate_note,
)
from .paypal import PayPalError, get_client, reset_client

STATIC_DIR = Path(__file__).parent / "static"

REASON_LABELS = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "Item not received",
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "Not as described",
    "UNAUTHORISED": "Unauthorised charge",
    "DUPLICATE_TRANSACTION": "Duplicate charge",
    "CREDIT_NOT_PROCESSED": "Refund not processed",
    "INCORRECT_AMOUNT": "Incorrect amount",
    "PAYMENT_BY_OTHER_MEANS": "Paid by other means",
    "OTHER": "Other",
}

# PayPal takes a carrier enum; anything else goes as OTHER + carrier_name_other.
KNOWN_CARRIERS = {
    "DHL", "DHL_EXPRESS", "FEDEX", "UPS", "USPS", "ROYAL_MAIL", "DPD", "DPD_UK",
    "HERMES", "TNT", "PARCELFORCE", "YODEL", "GLS", "CANADA_POST", "AUSTRALIA_POST",
    "OTHER",
}

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="DisputeDesk",
    version="1.0.0",
    description="An AI agent that prepares PayPal dispute responses for small sellers.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------

class ApproveRequest(BaseModel):
    run_id: str | None = None
    action: str = Field(..., description="FIGHT, OFFER or ACCEPT")
    response_note: str = ""
    buyer_message: str = ""
    send_buyer_message: bool = True
    offer_type: str | None = None
    offer_amount: str | None = None


# --------------------------------------------------------------------------
# Shaping
# --------------------------------------------------------------------------

def _due_in_days(iso: str | None) -> int | None:
    if not iso:
        return None
    from datetime import datetime, timezone

    try:
        due = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = (due - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(seconds // 86400))


def _summarise(dispute: dict[str, Any]) -> dict[str, Any]:
    txn = (dispute.get("disputed_transactions") or [{}])[0]
    buyer_messages = [m for m in dispute.get("messages", []) if m.get("posted_by") == "BUYER"]
    return {
        "dispute_id": dispute.get("dispute_id"),
        "reason": dispute.get("reason"),
        "reason_label": REASON_LABELS.get(dispute.get("reason", ""), dispute.get("reason")),
        "status": dispute.get("status"),
        "stage": dispute.get("dispute_life_cycle_stage"),
        "amount": (dispute.get("dispute_amount") or {}).get("value"),
        "currency": dispute_currency(dispute),
        "buyer_name": (txn.get("buyer") or {}).get("name"),
        "item_name": ((txn.get("items") or [{}])[0]).get("item_name"),
        "order_id": txn.get("invoice_number"),
        "create_time": dispute.get("create_time"),
        "due_date": dispute.get("seller_response_due_date"),
        "due_in_days": _due_in_days(dispute.get("seller_response_due_date")),
        "buyer_message": buyer_messages[-1]["content"] if buyer_messages else None,
        "allowed_offer_types": allowed_offer_types(dispute),
        "actionable": dispute.get("status") == "WAITING_FOR_SELLER_RESPONSE",
        "outcome": dispute.get("dispute_outcome"),
        "offer": dispute.get("offer"),
    }


def _stats(disputes: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    open_disputes = [d for d in disputes if d.get("status") == "WAITING_FOR_SELLER_RESPONSE"]
    at_risk = sum(Decimal(money((d.get("dispute_amount") or {}).get("value", "0")))
                  for d in open_disputes)

    by_id = {d["dispute_id"]: d for d in disputes}
    protected = Decimal("0")
    resolved = 0
    for sub in db.all_submissions():
        if not sub["ok"]:
            continue
        dispute = by_id.get(sub["dispute_id"])
        if dispute is None:
            continue
        full = Decimal(money((dispute.get("dispute_amount") or {}).get("value", "0")))
        if sub["action"] == "FIGHT":
            protected += full
            resolved += 1
        elif sub["action"] == "OFFER":
            offered = (dispute.get("offer") or {}).get("seller_offered_amount") or {}
            protected += full - Decimal(money(offered.get("value", "0")))
            resolved += 1
        elif sub["action"] == "ACCEPT":
            resolved += 1

    completed_runs = sum(1 for d in disputes if (db.latest_run_for(d["dispute_id"]) or {}).get("status") == "complete")
    hours_saved = round(completed_runs * settings.minutes_saved_per_dispute / 60, 1)
    return {
        "open_disputes": len(open_disputes),
        "at_risk": f"{at_risk:.2f}",
        "protected": f"{protected:.2f}",
        "responses_submitted": resolved,
        "hours_saved": hours_saved,
        "currency": dispute_currency(disputes[0]) if disputes else "USD",
    }


# --------------------------------------------------------------------------
# Read endpoints
# --------------------------------------------------------------------------

@app.get("/api/config")
def api_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "paypal_mode": settings.paypal_mode,
        "paypal_base_url": settings.paypal_base_url if settings.live_paypal else None,
        "paypal_configured": settings.paypal_configured,
        "agent_engine": "claude" if settings.agent_is_live else "rules",
        "model": settings.claude_model if settings.agent_is_live else None,
        "max_agent_turns": settings.max_agent_turns,
    }


@app.get("/api/disputes")
def api_disputes() -> dict[str, Any]:
    try:
        disputes = get_client().list_disputes()
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=_paypal_detail(exc)) from exc
    items = []
    for dispute in disputes:
        summary = _summarise(dispute)
        run = db.latest_run_for(summary["dispute_id"])
        summary["run"] = (
            {
                "run_id": run["id"],
                "status": run["status"],
                "engine": run["engine"],
                "action": run["action"],
                "confidence": run["confidence"],
            }
            if run
            else None
        )
        summary["submitted"] = any(s["ok"] for s in db.submissions_for(summary["dispute_id"]))
        items.append(summary)
    items.sort(key=lambda d: (not d["actionable"], d["due_in_days"] if d["due_in_days"] is not None else 99))
    return {"disputes": items, "stats": _stats(disputes)}


@app.get("/api/disputes/{dispute_id}")
def api_dispute(dispute_id: str) -> dict[str, Any]:
    client = get_client()
    try:
        dispute = client.get_dispute(dispute_id)
    except PayPalError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=_paypal_detail(exc)) from exc
    store = client.get_store_record(dispute_id) or {}
    return {
        "dispute": dispute,
        "summary": _summarise(dispute),
        "store": {
            "order": store.get("order"),
            "tracking": store.get("tracking"),
            "messages": store.get("messages", []),
            "refunds": store.get("refunds", []),
            "buyer_history": store.get("buyer_history"),
            "listing": store.get("listing"),
        },
        "run": db.latest_run_for(dispute_id),
        "submissions": db.submissions_for(dispute_id),
    }


@app.get("/api/runs/{run_id}")
def api_run(run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such run")
    return run


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------

@app.post("/api/disputes/{dispute_id}/agent")
def api_run_agent(dispute_id: str) -> dict[str, Any]:
    client = get_client()
    try:
        client.get_dispute(dispute_id)
    except PayPalError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=_paypal_detail(exc)) from exc

    existing = db.active_run_for(dispute_id)
    if existing:
        return {"run_id": existing, "already_running": True}
    run_id = runner.start_run(dispute_id)
    return {"run_id": run_id, "already_running": False}


# --------------------------------------------------------------------------
# Approve -> PayPal
# --------------------------------------------------------------------------

def _carrier(name: str) -> dict[str, str]:
    upper = (name or "OTHER").upper().replace(" ", "_")
    if upper in KNOWN_CARRIERS:
        return {"carrier_name": upper}
    return {"carrier_name": "OTHER", "carrier_name_other": name}


def _build_evidences(evidence: list[dict[str, Any]], response_note: str) -> list[dict[str, Any]]:
    """Turn plan evidence into the provide-evidence payload PayPal expects."""
    if not evidence:
        return [{"evidence_type": "OTHER", "notes": truncate_note(response_note)}]

    out: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        notes = item.get("description", "")
        if index == 0 and response_note:
            notes = f"{response_note}\n\n{notes}".strip()
        entry: dict[str, Any] = {
            "evidence_type": item.get("evidence_type", "OTHER"),
            "notes": truncate_note(notes),
        }
        info: dict[str, Any] = {}
        tracking = item.get("tracking_info")
        if isinstance(tracking, dict) and tracking.get("tracking_number"):
            info["tracking_info"] = [
                {**_carrier(tracking.get("carrier_name", "OTHER")),
                 "tracking_number": tracking["tracking_number"]}
            ]
        refund_ids = item.get("refund_ids")
        if refund_ids:
            info["refund_ids"] = list(refund_ids)
        if info:
            entry["evidence_info"] = info
        out.append(entry)
    return out


@app.post("/api/disputes/{dispute_id}/approve")
def api_approve(dispute_id: str, body: ApproveRequest) -> dict[str, Any]:
    client = get_client()
    try:
        dispute = client.get_dispute(dispute_id)
    except PayPalError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=_paypal_detail(exc)) from exc

    action = body.action.upper()
    if action not in {"FIGHT", "OFFER", "ACCEPT"}:
        raise HTTPException(status_code=400, detail=f"Unknown action {body.action!r}")

    run = db.get_run(body.run_id) if body.run_id else db.latest_run_for(dispute_id)
    plan = (run or {}).get("plan") or {}

    # --- guardrails, before anything leaves the building -------------------
    try:
        check_status(dispute)
        note = truncate_note(body.response_note or plan.get("response_note", ""))
        if not note:
            raise GuardrailError("a response note is required")
        offer_type = offer_value = None
        if action == "OFFER":
            plan_offer = plan.get("offer") or {}
            offer_type, offer_value = check_offer(
                dispute,
                body.offer_type or plan_offer.get("offer_type", ""),
                body.offer_amount if body.offer_amount is not None else plan_offer.get("amount", "0"),
            )
    except GuardrailError as exc:
        raise HTTPException(status_code=422, detail=f"Blocked by guardrails: {exc}") from exc

    results: list[dict[str, Any]] = []

    # The buyer message goes first: it does not consume the response window,
    # while the action below closes the dispute to further seller responses.
    buyer_message = (body.buyer_message or plan.get("buyer_message") or "").strip()
    if body.send_buyer_message and buyer_message:
        results.append(
            _submit(client, dispute_id, run, "MESSAGE",
                    lambda: client.send_message(dispute_id, truncate_note(buyer_message)))
        )

    if action == "FIGHT":
        evidences = _build_evidences(plan.get("evidence") or [], note)
        results.append(
            _submit(client, dispute_id, run, "FIGHT",
                    lambda: client.provide_evidence(dispute_id, evidences))
        )
    elif action == "OFFER":
        results.append(
            _submit(client, dispute_id, run, "OFFER",
                    lambda: client.make_offer(dispute_id, note, offer_type, offer_value,
                                              dispute_currency(dispute)))
        )
    else:
        results.append(
            _submit(client, dispute_id, run, "ACCEPT",
                    lambda: client.accept_claim(dispute_id, note))
        )

    failed = [r for r in results if not r["ok"]]
    return {
        "ok": not failed,
        "action": action,
        "results": results,
        "dispute": _summarise(client.get_dispute(dispute_id)),
    }


def _submit(client, dispute_id: str, run: dict[str, Any] | None, action: str, call) -> dict[str, Any]:
    run_id = (run or {}).get("id")
    try:
        endpoint, request_body, response = call()
    except PayPalError as exc:
        db.record_submission(
            dispute_id, run_id, action, "", None, {"error": str(exc), "body": exc.body},
            exc.status_code, False,
        )
        return {
            "ok": False,
            "action": action,
            "status_code": exc.status_code,
            "error": str(exc),
            "response": exc.body,
        }
    db.record_submission(dispute_id, run_id, action, endpoint, request_body, response, 200, True)
    return {
        "ok": True,
        "action": action,
        "status_code": 200,
        "endpoint": endpoint,
        "request": request_body,
        "response": response,
    }


# --------------------------------------------------------------------------
# Demo helpers
# --------------------------------------------------------------------------

@app.post("/api/reset")
def api_reset() -> dict[str, Any]:
    """Restore the mock fixtures and clear run history. Mock mode only."""
    if get_settings().live_paypal:
        raise HTTPException(status_code=400, detail="Reset is only available in mock mode")
    reset_client()
    db.reset_db()
    return {"ok": True}


def _paypal_detail(exc: PayPalError) -> str:
    if exc.body:
        return f"{exc} — {exc.body}"
    return str(exc)


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
