"""Real PayPal Disputes v1 client against the sandbox (or live) REST API."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from .base import PayPalError

TOKEN_PATH = "/v1/oauth2/token"
DISPUTES_PATH = "/v1/customer/disputes"


class SandboxDisputeClient:
    """OAuth2 client-credentials client. Tokens are cached until just before expiry."""

    mode = "sandbox"

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        store_lookup=None,
        timeout: float = 30.0,
    ) -> None:
        if not client_id or not client_secret:
            raise PayPalError(
                "PAYPAL_MODE=sandbox needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET"
            )
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._store_lookup = store_lookup
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    # --- auth ---------------------------------------------------------------

    def access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            try:
                resp = httpx.post(
                    f"{self.base_url}{TOKEN_PATH}",
                    data={"grant_type": "client_credentials"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                raise PayPalError(f"could not reach PayPal to get a token: {exc}") from exc
            if resp.status_code != 200:
                raise PayPalError(
                    "PayPal rejected the client credentials", resp.status_code, _body(resp)
                )
            payload = resp.json()
            self._token = payload["access_token"]
            # Refresh a minute early rather than racing the expiry.
            self._token_expires_at = time.time() + max(60, int(payload.get("expires_in", 32400)) - 60)
            return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # --- reads --------------------------------------------------------------

    def list_disputes(self) -> list[dict[str, Any]]:
        """Summaries from the list endpoint, each enriched with the detail call.

        The list endpoint omits messages and allowed_response_options, and the UI
        and the agent both need them.
        """
        payload = self._request("GET", DISPUTES_PATH, params={"page_size": 50})
        summaries = payload.get("items", []) or []
        out: list[dict[str, Any]] = []
        for summary in summaries:
            dispute_id = summary.get("dispute_id")
            if not dispute_id:
                continue
            try:
                out.append(self.get_dispute(dispute_id))
            except PayPalError:
                out.append(summary)
        return out

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        return self._request("GET", f"{DISPUTES_PATH}/{dispute_id}")

    def get_store_record(self, dispute_id: str) -> dict[str, Any] | None:
        """Seller-side data lives in the seller's own systems, not in PayPal.

        In sandbox mode we look it up in whatever store the app was given; a real
        deployment would point this at the merchant's order database.
        """
        if self._store_lookup is None:
            return None
        return self._store_lookup(dispute_id)

    # --- writes -------------------------------------------------------------

    def provide_evidence(self, dispute_id: str, evidences: list[dict[str, Any]]):
        """multipart/form-data with a JSON part named "input", per the API docs."""
        endpoint = f"{DISPUTES_PATH}/{dispute_id}/provide-evidence"
        body = {"evidences": evidences}
        url = f"{self.base_url}{endpoint}"
        try:
            resp = httpx.post(
                url,
                files={"input": (None, json.dumps(body), "application/json")},
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise PayPalError(f"PayPal request failed: {exc}") from exc
        return endpoint, body, self._parse(resp, "POST", endpoint)

    def make_offer(self, dispute_id: str, note: str, offer_type: str, value: str, currency_code: str):
        endpoint = f"{DISPUTES_PATH}/{dispute_id}/make-offer"
        body = {
            "note": note,
            "offer_amount": {"currency_code": currency_code, "value": value},
            "offer_type": offer_type,
        }
        return endpoint, body, self._request("POST", endpoint, json_body=body)

    def accept_claim(self, dispute_id: str, note: str):
        endpoint = f"{DISPUTES_PATH}/{dispute_id}/accept-claim"
        body = {"note": note}
        return endpoint, body, self._request("POST", endpoint, json_body=body)

    def send_message(self, dispute_id: str, message: str):
        endpoint = f"{DISPUTES_PATH}/{dispute_id}/send-message"
        body = {"message": message}
        return endpoint, body, self._request("POST", endpoint, json_body=body)

    # --- plumbing -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = httpx.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise PayPalError(f"PayPal request failed: {exc}") from exc
        return self._parse(resp, method, path)

    @staticmethod
    def _parse(resp: httpx.Response, method: str, path: str) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise PayPalError(
                f"PayPal {method} {path} returned {resp.status_code}",
                resp.status_code,
                _body(resp),
            )
        if not resp.content:
            return {"status_code": resp.status_code}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:2000]}
