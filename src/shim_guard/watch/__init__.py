"""`shim watch` — a local proxy that measures a session and changes nothing.

Hooks have a permanent blind spot. Files referenced with `@` never fire one,
the system prompt and the tools array are never handed to one, and the token
counts that decide what a session costs are only ever in the response. This
sits in the connection instead, forwards every byte unchanged, and reads.

Nothing here is imported from the hook path. The hook is a cold-start
subprocess on every tool call and pays for each import; this runs once per
session in a process of its own.
"""

from __future__ import annotations

__all__ = ["measure", "proxy", "report"]
