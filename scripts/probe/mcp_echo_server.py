"""A one-tool stdio MCP server used only by the client capability probe.

It exists so the probe can capture a real ``mcp__*`` tool call: the argument
object and tool result become synthetic, reproducible payload and hook fixtures.
A local echo server keeps real client traffic out of them. Not product code.
"""

from __future__ import annotations

import json
import sys
from typing import IO

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "shim-probe", "version": "0"}
TOOL = {
    "name": "probe_echo",
    "description": "Return the given customer record unchanged.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "customer_email": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["customer_email"],
        "additionalProperties": False,
    },
}


def respond(request: dict[str, object]) -> dict[str, object] | None:
    """Return the JSON-RPC response for one request, or None for a notification."""
    method = request.get("method")
    identifier = request.get("id")
    if identifier is None:
        return None
    if method == "initialize":
        return _result(
            identifier,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/list":
        return _result(identifier, {"tools": [TOOL]})
    if method == "tools/call":
        return _result(identifier, _call(request.get("params")))
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": -32601, "message": "method not found"},
    }


def _result(identifier: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _call(params: object) -> dict[str, object]:
    arguments: object = {}
    if isinstance(params, dict):
        arguments = params.get("arguments", {})
    text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def serve(source: IO[str], sink: IO[str]) -> None:
    """Run the newline-delimited JSON-RPC loop until the client closes stdin."""
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if not isinstance(request, dict):
            continue
        response = respond(request)
        if response is None:
            continue
        sink.write(json.dumps(response, ensure_ascii=False) + "\n")
        sink.flush()


if __name__ == "__main__":
    serve(sys.stdin, sys.stdout)
