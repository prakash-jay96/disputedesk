"""Seller-side records: the order, tracking, messages and refunds the agent reads.

This data lives in the merchant's own systems, not in PayPal, so in sandbox
mode it has to come from somewhere local. A JSON file is the honest minimal
answer: a real deployment would swap `SellerStore.lookup` for a query against
the shop's order database and nothing else would change.

Records are matched on dispute id, invoice number or transaction id, because a
real seller knows their own order number but not the PayPal dispute id until
the dispute exists.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MATCH_KEYS = ("dispute_id", "invoice_number", "transaction_id", "order_id")


class SellerStore:
    """Looks up a seller's record for a dispute. Empty is a valid state."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._records = records or []
        self._index: dict[str, dict[str, Any]] = {}
        for record in self._records:
            for key in self._match_values(record):
                self._index[key] = record

    # --- loading ------------------------------------------------------------

    @staticmethod
    def _match_values(record: dict[str, Any]) -> list[str]:
        """Every identifier a record can be found by."""
        values: list[str] = []
        match = record.get("match") or {}
        for key in MATCH_KEYS:
            value = match.get(key)
            if value:
                values.append(str(value))
        order = record.get("order") or {}
        if order.get("order_id"):
            values.append(str(order["order_id"]))
        return values

    @classmethod
    def from_file(cls, path: str | Path) -> "SellerStore":
        path = Path(path)
        if not path.exists():
            return cls([])
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Any) -> "SellerStore":
        """Accepts either {"records": [...]} or a flat {id: record} mapping."""
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return cls(payload["records"])
        if isinstance(payload, dict):
            records = []
            for key, value in payload.items():
                if not isinstance(value, dict):
                    continue
                record = dict(value)
                match = dict(record.get("match") or {})
                match.setdefault("dispute_id", key)
                record["match"] = match
                records.append(record)
            return cls(records)
        if isinstance(payload, list):
            return cls([r for r in payload if isinstance(r, dict)])
        raise ValueError("store file must be an object or a list of records")

    # --- lookup -------------------------------------------------------------

    def lookup(self, *identifiers: str | None) -> dict[str, Any] | None:
        for identifier in identifiers:
            if identifier and str(identifier) in self._index:
                return self._index[str(identifier)]
        return None

    def lookup_for_dispute(self, dispute: dict[str, Any]) -> dict[str, Any] | None:
        """Find a record using every identifier the dispute carries."""
        transaction = (dispute.get("disputed_transactions") or [{}])[0]
        return self.lookup(
            dispute.get("dispute_id"),
            transaction.get("invoice_number"),
            transaction.get("custom"),
            transaction.get("seller_transaction_id"),
        )

    def __len__(self) -> int:
        return len(self._records)

    @property
    def identifiers(self) -> list[str]:
        return sorted(self._index)
