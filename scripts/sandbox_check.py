#!/usr/bin/env python3
"""Check that PayPal sandbox credentials work before you rely on them.

    python scripts/sandbox_check.py

Reads PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and PAYPAL_BASE_URL from the
environment (or a .env file next to this repo). Prints what it finds and exits
non-zero if anything is wrong. It only reads: no dispute is ever modified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    load_dotenv(ROOT / ".env")

    base_url = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")

    print(f"Base URL      {base_url}")
    if not client_id or not secret:
        print("\nMissing PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET.")
        print("Create a sandbox REST app at https://developer.paypal.com/dashboard/applications/sandbox")
        print("and put the credentials in .env (see .env.example).")
        return 2
    print(f"Client ID     {client_id[:6]}...{client_id[-4:]} ({len(client_id)} chars)")

    from app.paypal.base import PayPalError
    from app.paypal.sandbox import SandboxDisputeClient

    client = SandboxDisputeClient(base_url, client_id, secret)

    try:
        token = client.access_token()
    except PayPalError as exc:
        print(f"\nOAuth failed: {exc}")
        if exc.body:
            print(f"   {exc.body}")
        print("\nCheck the credentials are for the same environment as PAYPAL_BASE_URL:")
        print("sandbox credentials do not work against api-m.paypal.com.")
        return 1
    print(f"OAuth token   ok ({len(token)} chars)")

    try:
        disputes = client.list_disputes()
    except PayPalError as exc:
        print(f"\nCould not list disputes: {exc}")
        if exc.status_code == 403:
            print("The app may not have the Disputes API enabled. In the developer dashboard,")
            print("open the REST app and tick 'Dispute Management' under Features.")
        return 1

    print(f"Disputes      {len(disputes)} returned\n")
    if not disputes:
        print("No disputes in this sandbox account yet. To create one:")
        print("  1. Log in at https://sandbox.paypal.com as your sandbox BUYER account.")
        print("  2. Pay your sandbox seller account (or use a sandbox transaction).")
        print("  3. Open Activity -> the transaction -> Report a Problem, and file a claim.")
        print("  4. Wait a minute, then run this script again.")
        print("\nUntil then, run with PAYPAL_MODE=mock to use the built-in fixtures.")
        return 0

    actionable = 0
    for dispute in disputes:
        status = dispute.get("status", "?")
        amount = dispute.get("dispute_amount", {})
        if status == "WAITING_FOR_SELLER_RESPONSE":
            actionable += 1
        print(
            f"  {dispute.get('dispute_id', '?'):<20} {status:<30} "
            f"{amount.get('currency_code', '')} {amount.get('value', ''):>9}  "
            f"{dispute.get('reason', '')}"
        )
    print(f"\n{actionable} dispute(s) awaiting a seller response.")
    print("Start the app with PAYPAL_MODE=sandbox to work on them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
