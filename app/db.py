"""SQLite persistence for agent runs, reasoning steps and PayPal submissions.

A connection is opened per operation so the background agent thread and the
request handlers never share one.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_settings

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    dispute_id    TEXT NOT NULL,
    status        TEXT NOT NULL,              -- running | complete | error
    engine        TEXT NOT NULL,              -- claude | rules
    model         TEXT,
    action        TEXT,
    confidence    REAL,
    plan_json     TEXT,
    error         TEXT,
    created_at    REAL NOT NULL,
    finished_at   REAL
);
CREATE TABLE IF NOT EXISTS steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,                -- status | thinking | tool_call | tool_result | plan | error
    label       TEXT,
    payload     TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS steps_run_idx ON steps (run_id, seq);
CREATE TABLE IF NOT EXISTS submissions (
    id            TEXT PRIMARY KEY,
    dispute_id    TEXT NOT NULL,
    run_id        TEXT,
    action        TEXT NOT NULL,
    endpoint      TEXT,
    request_json  TEXT,
    response_json TEXT,
    status_code   INTEGER,
    ok            INTEGER NOT NULL,
    created_at    REAL NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    with connect() as conn:
        conn.executescript(
            "DROP TABLE IF EXISTS steps; DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS submissions;"
        )
        conn.executescript(SCHEMA)


# --- runs -------------------------------------------------------------------

def create_run(dispute_id: str, engine: str, model: str | None) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _write_lock, connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, dispute_id, status, engine, model, created_at)"
            " VALUES (?, ?, 'running', ?, ?, ?)",
            (run_id, dispute_id, engine, model, time.time()),
        )
    return run_id


def finish_run(run_id: str, plan: dict[str, Any]) -> None:
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE runs SET status='complete', action=?, confidence=?, plan_json=?, finished_at=?"
            " WHERE id=?",
            (
                plan.get("action"),
                plan.get("confidence"),
                json.dumps(plan),
                time.time(),
                run_id,
            ),
        )


def fail_run(run_id: str, error: str) -> None:
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE runs SET status='error', error=?, finished_at=? WHERE id=?",
            (error, time.time(), run_id),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        steps = conn.execute(
            "SELECT seq, kind, label, payload, created_at FROM steps WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
    run = dict(row)
    run["plan"] = json.loads(run.pop("plan_json")) if run.get("plan_json") else None
    run["steps"] = [
        {
            "seq": s["seq"],
            "kind": s["kind"],
            "label": s["label"],
            "payload": json.loads(s["payload"]) if s["payload"] else None,
            "created_at": s["created_at"],
        }
        for s in steps
    ]
    return run


def latest_run_for(dispute_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE dispute_id=? ORDER BY created_at DESC LIMIT 1",
            (dispute_id,),
        ).fetchone()
    return get_run(row["id"]) if row else None


def active_run_for(dispute_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE dispute_id=? AND status='running'"
            " ORDER BY created_at DESC LIMIT 1",
            (dispute_id,),
        ).fetchone()
    return row["id"] if row else None


# --- steps ------------------------------------------------------------------

def add_step(run_id: str, kind: str, label: str | None, payload: Any = None) -> None:
    with _write_lock, connect() as conn:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM steps WHERE run_id=?", (run_id,)
        ).fetchone()["n"]
        conn.execute(
            "INSERT INTO steps (run_id, seq, kind, label, payload, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, seq, kind, label, json.dumps(payload) if payload is not None else None, time.time()),
        )


# --- submissions ------------------------------------------------------------

def record_submission(
    dispute_id: str,
    run_id: str | None,
    action: str,
    endpoint: str,
    request_body: Any,
    response_body: Any,
    status_code: int | None,
    ok: bool,
) -> str:
    sub_id = uuid.uuid4().hex[:12]
    with _write_lock, connect() as conn:
        conn.execute(
            "INSERT INTO submissions (id, dispute_id, run_id, action, endpoint, request_json,"
            " response_json, status_code, ok, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                sub_id,
                dispute_id,
                run_id,
                action,
                endpoint,
                json.dumps(request_body, default=str),
                json.dumps(response_body, default=str),
                status_code,
                1 if ok else 0,
                time.time(),
            ),
        )
    return sub_id


def submissions_for(dispute_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM submissions WHERE dispute_id=? ORDER BY created_at DESC", (dispute_id,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["request"] = json.loads(item.pop("request_json") or "null")
        item["response"] = json.loads(item.pop("response_json") or "null")
        item["ok"] = bool(item["ok"])
        out.append(item)
    return out


def all_submissions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT dispute_id, action, ok FROM submissions").fetchall()
    return [dict(r) for r in rows]
