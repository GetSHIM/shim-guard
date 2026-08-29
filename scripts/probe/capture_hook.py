"""Record raw client hook payloads for the PRD-01 capability probe.

This is not product code. It never ships in the package, never writes to
stdout, and always exits 0 so a probe session behaves exactly like a session
with no hook installed. Captures land in ``$SHIM_PROBE_DIR`` verbatim: the
whole point of the probe is to see the bytes the client actually sends.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

MAX_CAPTURE_BYTES = 8_000_000
_UNPARSED = "unparsed"


def safe_name(value: str) -> str:
    """Return a file-name-safe fragment of at most 64 characters."""
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )
    return cleaned[:64] or _UNPARSED


def describe(raw: bytes) -> tuple[str, str]:
    """Return the ``(event, tool)`` pair a payload names, best effort."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _UNPARSED, _UNPARSED
    if not isinstance(payload, dict):
        return _UNPARSED, _UNPARSED
    event = _first_string(payload, ("hook_event_name", "hookEventName", "eventName"))
    tool = _first_string(payload, ("tool_name", "toolName", "tool"))
    return safe_name(event or "unnamed"), safe_name(tool or "none")


def _first_string(payload: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def capture(raw: bytes, directory: Path) -> Path:
    """Write one payload under ``directory`` and return the path used."""
    event, tool = describe(raw)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{event}-{tool}-{time.time_ns()}.json"
    target.write_bytes(raw)
    return target


def _rewrite_for(raw: bytes, specification: str) -> str:
    """Return the configured hook output when this event matches, else "".

    Used to establish empirically whether a client honours a mutation field,
    rather than trusting a documented shape.
    """
    try:
        rule = json.loads(specification)
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    if not isinstance(rule, dict) or not isinstance(payload, dict):
        return ""
    if payload.get("hook_event_name") != rule.get("event"):
        return ""
    if rule.get("tool") and payload.get("tool_name") != rule["tool"]:
        return ""
    return json.dumps(rule.get("output", {}))


def main() -> int:
    """Never fail: read one payload, store it, allow the event.

    Stdout stays empty unless ``SHIM_PROBE_SYSTEM_MESSAGE`` is set, which is how
    the probe answers whether a client renders hook ``systemMessage`` output.
    """
    try:
        raw = sys.stdin.buffer.read(MAX_CAPTURE_BYTES)
        directory = os.environ.get("SHIM_PROBE_DIR")
        if directory:
            capture(raw, Path(directory))
        message = os.environ.get("SHIM_PROBE_SYSTEM_MESSAGE")
        if message:
            sys.stdout.write(json.dumps({"systemMessage": message}))
        rewrite = os.environ.get("SHIM_PROBE_REWRITE")
        if rewrite:
            sys.stdout.write(_rewrite_for(raw, rewrite))
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
