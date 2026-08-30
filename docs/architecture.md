# Architecture

SHIM Guard is one local Python distribution. The CLI is a management and
local-inspection interface; client-native hooks are the prompt path. It has no
daemon, history, or service state. Copilot receives direct rewrites; blocking
clients get one temporary redaction per blocked prompt.

```text
shim config -> guarded local entity policy
shim scan/redact (stdin) -> policy -> detector
                         -> categories/counts or typed redaction
shim update -> recorded installer -> uv tool upgrade | pipx upgrade

supported prompt event -> native hook adapter -> policy -> detector
                       -> allow | Copilot direct rewrite
                                | 0600 temporary redaction -> native block
shim install/status/doctor/revert -> guarded merge/revert -> client hook settings
```

The detector is functional, offline, and first party. It normalizes input once,
validates bounded findings against a selected subset of the fixed entity
allowlist, resolves spans deterministically, and produces typed ordinal
placeholders. Every recognizer, checksum and deny rule lives in
`guard/recognizers.py`; the public suffix table used to validate email hosts is
compiled into `guard/suffixes.py`, so nothing is read from the network or the
filesystem. `phonenumbers` is the only third-party module reachable from the
hook, which `tests/contracts/test_import_hygiene.py` enforces. The
hook owns stdin/stdout/stderr, the client protocol, and any per-block temporary
redaction file. Installation owns only SHIM's hook fragment and entity-policy
planning plus guarded filesystem I/O. Each adapter defines its exact target and
owned fragment. Install preserves valid existing settings and adds only that
fragment after informing the user; revert removes only the same fragment. Both
operations are idempotent.

The local TOML policy defaults to every public entity. The CLI and hook validate
it through the same bounded, no-symlink file inspection path; the detector
itself remains pure and receives the enabled tuple explicitly. Malformed or
unsafe policy files fail closed. Configuration stores entity names only—never
prompt-derived data.

Each adapter owns its client's configuration and coexistence rules. The Codex
adapter leaves inline `config.toml` hooks untouched; they may coexist with
`hooks.json` with a client warning. The Claude Code adapter changes only its own
groups inside `hooks` in user `settings.json` — `UserPromptSubmit`, plus one
group per tool event whose mutation shape has been confirmed against a running
client — and preserves every unrelated setting. Which tool events those are
comes from the adapter registry, so the two install paths and `shim doctor`
cannot disagree. Each group is matched by exact value, so an install made
before tool events existed gains only the missing groups, and revert gives up
only SHIM's own. Malformed or ambiguous documents and unsafe or concurrently
changed files require manual setup. Dry-run output contains only SHIM's
fragment, not the existing document.

The GitHub Copilot CLI adapter owns
`$COPILOT_HOME/hooks/shim-guard.json` (defaulting to
`~/.copilot/hooks/shim-guard.json`). Its `userPromptTransformed` hook evaluates
the model-facing content and returns `modifiedTransformedPrompt` with the typed
redaction. Copilot stores and sends the replacement while leaving the original
timeline display unchanged. Revert retains an empty versioned hook document.

## Context diet and injection markers

`events/diet.py` shrinks inbound tool results and `events/injection.py` flags
text addressed to the model. Both ride the single payload walk in
`events/payload.inspect`, which offers each string leaf to the detector, the
scanner and the transforms in turn, then rebuilds the payload once.

The three stay separate deliberately. Masking and diet rewrite; a marker only
reports and can never reach a replacement, so no configuration can turn "this
text looks like an instruction" into an edit of a tool result. Diet is gated on
direction (`inbound` only), on the adapter being able to rewrite, on the mode
not being `observe`, and on the result not being a view of a file the model may
go on to edit.

Every quantifier in `injection.py` is bounded. The line-anchored pattern once
carried an unbounded `\s*`, which a long run of blank lines re-entered per
newline: 32k blank lines cost 25 seconds, past `HOOK_DEADLINE_SECONDS`, so a
file anyone could commit stalled the client for the whole deadline and the
result then went through uninspected. `tests/events/test_injection.py` holds
the timing that keeps it linear.

## Session record

Hooks are separate processes, so anything that spans events must be written
down. `shim_guard.session` owns that: `spool` is a per-session JSONL file under
the OS temporary directory (`0700` directory, `0600` files, session identifier
hashed rather than used as a name), `summary` renders it, and `ledger` is the
opt-in copy that outlives the session.

Recording is deliberately best-effort — every write is wrapped so that a spool
that cannot be used leaves masking working and the summary absent, never the
other way round. Because that failure is silent by design, `shim doctor` probes
the spool and reports it.

`Stop` renders hook output and `SessionEnd` does not, so the summary is emitted
at `Stop` — once per change, carrying session totals, guarded by
`stop_hook_active` — and `SessionEnd` exists only to delete the spool. No
record field ever carries payload text: entity names, counts, and a
detector-scrubbed file path or URL, never a value and never a shell command.

## Detector boundary and corpus

`shim-guard` is intentionally an independently packaged, narrow detector fork.
It does not import the parent SHIM gateway: the gateway's reversible maps,
provider flow, persistence, and broader runtime are outside a local synchronous
hook. The public `guard-v2` synthetic corpus is Guard's executable detector
contract and a migration reference for the parent gateway; it does not claim
current result parity between the independently released implementations.
Guard category coverage and behavior change only with an explicit corpus
update. `guard-tools-v1.json` extends the same contract to tool-event
payloads captured from a real client. `tests/corpus/parity-v1.json` is a third,
larger contract: 475
generated cases recording the exact findings and redacted output produced
before the detector was reimplemented, so a refactor that changes a span, a
score or a placeholder ordinal fails immediately rather than silently.

The secret recognizer is deliberately stricter than broad gateway-style prose
matching. Assignment-style secrets require `key = value` or `key: value` (with
the explicit `--password` form also supported); prose such as “a password
should never be shared” remains safe. This reduces false blocks at the prompt
boundary without claiming complete secret detection.

No daemon, database, HTTP client, account, analytics, plugin framework,
provider-neutral adapter layer, or reversible raw-value store belongs in this
MVP. Every client gets its own native adapter and fixtures; adding one does not
widen the hook's trust boundary.

The [Codex hooks reference](https://developers.openai.com/codex/hooks/) and
Claude Code [hooks](https://code.claude.com/docs/en/hooks) and
[settings](https://code.claude.com/docs/en/settings) references are the
authoritative protocol sources for those clients. GitHub's
[Copilot hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)
defines the Copilot adapter's direct rewrite contract. Every adapter uses its
native settings and output, not a generic cross-client hook format.


## `shim watch`

`watch/proxy.py` forwards, `watch/measure.py` reads, `watch/report.py` says what
it saw, and `cli/watch.py` sequences the three. Nothing in `watch/` is
importable from the hook path and `tests/contracts/test_import_hygiene.py`
enforces that: the proxy pulls in `http.server`, `http.client`, `ssl` and
`socketserver`, and the hook is a cold-start subprocess that would pay for all
of it on every tool call.

Three properties carry the design.

**Forwarding has no opinions.** A hook that breaks fails open and the agent
keeps working; a proxy that breaks fails closed and the agent cannot reach the
model at all. So the relay does not retry, rewrite, decide, or invent a
response. Headers pass through verbatim — `anthropic-beta` and
`anthropic-version` carry an OAuth capability for subscription sign-ins and a
request without them is a 401.

**Measurement is beside the path, never in front of it.** The request is handed
to the upstream *before* a byte of it is examined, so scanning overlaps the
provider's own thinking time instead of being added to the user's latency. The
response is relayed with `read1`, which returns whatever has arrived; plain
`read(n)` blocks until it has all `n` bytes, which on a server-sent-event
stream means blocking until the model has finished — a streaming response
silently turned into a buffered one.

**The provider's numbers and shim's are never mixed.** `usage` is read off the
wire and is exact. Its division across `tools`, `system` and `messages` is
inferred from byte share, carries a `~`, and is scaled so the parts always sum
to the exact total.

The response arrives gzipped. The client receives those bytes untouched; a
second, incremental decompressor feeds the usage reader, so nothing is
re-encoded and no body is buffered or kept.

A fresh TLS connection is opened per request, measured at 23 ms against
`api.anthropic.com`. Connections are deliberately not reused: the only way to
make reuse safe against a stale socket is to retry, and a retried `POST` risks
a second billable request. 23 ms against a multi-second time to first token is
not worth that.
