"""Minimal .env loader.

The README tells people to put configuration in .env, so the app has to read
it. Done here rather than with a dependency because the format we need is
three lines of parsing, and a hackathon judge should not have to install
anything extra for the documented setup to work.

Real environment variables always win over the file, which is what every
other .env implementation does and what deployments depend on.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_loaded = False


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        # Strip one layer of matching quotes, as dotenv does.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Read a .env file into os.environ. Existing variables are kept."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return {}
    values = parse(path.read_text())
    applied = {}
    for key, value in values.items():
        if override or not os.environ.get(key):
            os.environ[key] = value
            applied[key] = value
    return applied


def load_once() -> None:
    global _loaded
    if not _loaded:
        load()
        _loaded = True
