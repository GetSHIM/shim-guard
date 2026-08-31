"""Session record and summary. Not imported by the detector.

`Stop` renders its hook output; `SessionEnd` does not, though it still runs
(PRD-01, surprise 3). So the summary is emitted at `Stop` and `SessionEnd` is
where the spool is deleted. Both must be registered for either to happen, which
is why they are named here rather than only inside the hook.
"""

from __future__ import annotations

from .record import remember

#: Claude Code's session lifecycle events, in the order they are installed.
SESSION_EVENTS = ("SessionEnd", "Stop")

__all__ = ["SESSION_EVENTS", "remember"]
