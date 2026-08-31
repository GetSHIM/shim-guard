"""Where the payload is going decides what may be done to it.

There is no single "mode" setting. A tool payload is classified by direction
first, and the direction determines whether rewriting is even permitted; the
user's mode then chooses how strongly to act within that permission.

The rule is about the kind of payload, not the tool name:

* **user-prompt** — a sentence a person typed. Generic policy never rewrites
  it. Copilot's verified ``userPromptTransformed`` adapter may replace the
  model-facing copy through its native response field.
* **outbound** — structured arguments leaving the machine, such as an MCP tool
  argument object. Masking here is egress control: the model already produced
  the value, so what masking buys is stopping it from leaving.
* **inbound** — a result flowing into the model's context. Masking here stops
  local data from entering the model's context, which is a different goal.
* **local-write** — content destined for the user's disk. Rewriting it writes
  `<EMAIL_1>` into a real file. That is data loss, so it never happens.
* **executable-text** — a free-form command string. Replacing a value inside a
  shell command changes what runs: `curl -u alice@corp.com:pw` becomes a
  request with different credentials. shim cannot know a command's semantics,
  so it may allow, warn, or deny, never edit.

Classifying commands by destination (`curl` versus `cat`) was considered and
rejected: shell is not reliably parseable, and a wrong classification either
breaks a working command or lets a leak through while the documentation claims
otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

USER_PROMPT = "user-prompt"
OUTBOUND = "outbound"
INBOUND = "inbound"
LOCAL_WRITE = "local-write"
EXECUTABLE_TEXT = "executable-text"
DIRECTIONS = (USER_PROMPT, OUTBOUND, INBOUND, LOCAL_WRITE, EXECUTABLE_TEXT)

OBSERVE = "observe"
WARN = "warn"
ENFORCE = "enforce"
MODES = (OBSERVE, WARN, ENFORCE)

ALLOW = "allow"
REPORT = "report"
MASK = "mask"
DENY = "deny"

DEFAULT_MODES = {
    USER_PROMPT: WARN,
    OUTBOUND: ENFORCE,
    INBOUND: ENFORCE,
    LOCAL_WRITE: WARN,
    EXECUTABLE_TEXT: WARN,
}

#: Directions whose payload may be rewritten in place at all.
REWRITABLE = {
    USER_PROMPT: False,
    OUTBOUND: True,
    INBOUND: True,
    LOCAL_WRITE: False,
    EXECUTABLE_TEXT: False,
}

#: Tools whose input lands on the user's disk rather than going anywhere.
LOCAL_WRITE_TOOLS = frozenset(
    {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "ApplyPatch"}
)
#: Tools whose input is a command string rather than a structured argument set.
COMMAND_TOOLS = frozenset({"Bash", "BashOutput", "Shell", "PowerShell", "shell"})

_PROMPT_EVENTS = frozenset({"UserPromptSubmit", "userPromptTransformed"})
_INPUT_EVENTS = frozenset({"PreToolUse", "preToolUse"})
_RESULT_EVENTS = frozenset(
    {"PostToolUse", "postToolUse", "PostToolUseFailure", "PostToolBatch"}
)


@dataclass(frozen=True)
class Policy:
    """Enabled entities plus the mode to apply, by direction, event or tool."""

    entities: tuple
    modes: dict
    tool_entities: dict
    #: Whether decisions outlive the session. Off unless the user turns it on.
    ledger: bool = False
    #: Enabled context-diet transforms, by name. Empty means diet is off.
    diet: tuple = ()

    def mode_for(self, direction: str, tool: str = "", event: str = "") -> str:
        """Return the mode for one payload, most specific override winning.

        Order: per-tool, then per-event, then per-direction, then the file's
        own default, then the shipped default for that direction.
        """
        for key in (tool, event, direction):
            if key and key in self.modes:
                return self.modes[key]
        if "default" in self.modes:
            return self.modes["default"]
        return DEFAULT_MODES.get(direction, WARN)

    def entities_for(self, tool: str = "", event: str = "") -> tuple:
        for key in (tool, event):
            if key and key in self.tool_entities:
                return self.tool_entities[key]
        return self.entities


@dataclass(frozen=True)
class Decision:
    """What policy permits for one payload, and why it was weakened."""

    __slots__ = ("direction", "mode", "action", "degraded_from", "reason")

    direction: str
    mode: str
    action: str
    degraded_from: str
    reason: str

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_from)


def direction_for(event: str, tool: str) -> str:
    """Classify one payload before anything is allowed to rewrite it."""
    if event in _PROMPT_EVENTS:
        return USER_PROMPT
    if event in _RESULT_EVENTS:
        return INBOUND
    if event in _INPUT_EVENTS:
        if tool in COMMAND_TOOLS:
            return EXECUTABLE_TEXT
        if tool in LOCAL_WRITE_TOOLS:
            return LOCAL_WRITE
        return OUTBOUND
    raise ValueError("unsupported hook event")


def decide(
    direction: str,
    mode: str,
    *,
    can_rewrite: bool = True,
    can_report: bool = True,
) -> Decision:
    """Return the strongest action this direction, mode and client allow.

    ``can_rewrite`` and ``can_report`` describe the client, not the policy:
    Codex cannot mask a tool result surgically, and a client that cannot render
    a message cannot warn. Where the client cannot do what the policy asks, the
    action degrades to the next weaker one and records that it did, rather than
    silently doing nothing.
    """
    if direction not in REWRITABLE:
        raise ValueError("unsupported policy direction")
    if mode not in MODES:
        raise ValueError("unsupported policy mode")

    if mode == OBSERVE:
        return Decision(direction, mode, ALLOW, "", "observing only")
    if mode == WARN:
        if can_report:
            return Decision(direction, mode, REPORT, "", "")
        return Decision(
            direction, mode, ALLOW, REPORT, "the client cannot show a message"
        )

    # enforce
    if REWRITABLE[direction]:
        if can_rewrite:
            return Decision(direction, mode, MASK, "", "")
        if can_report:
            return Decision(
                direction,
                mode,
                REPORT,
                MASK,
                "the client cannot rewrite this payload in place",
            )
        return Decision(
            direction, mode, ALLOW, MASK, "the client can neither rewrite nor report"
        )
    # Denying a non-rewritable payload is safer than changing a command, local
    # write, or generic user prompt.
    return Decision(direction, mode, DENY, "", "")
