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
`hooks.json` with a client warning. The Claude Code adapter changes only the
`hooks.UserPromptSubmit` array in user `settings.json` and preserves every
unrelated setting. Malformed or ambiguous documents and unsafe or concurrently
changed files require manual setup. Dry-run output contains only SHIM's
fragment, not the existing document.

The GitHub Copilot CLI adapter owns
`$COPILOT_HOME/hooks/shim-guard.json` (defaulting to
`~/.copilot/hooks/shim-guard.json`). Its `userPromptTransformed` hook evaluates
the model-facing content and returns `modifiedTransformedPrompt` with the typed
redaction. Copilot stores and sends the replacement while leaving the original
timeline display unchanged. Revert retains an empty versioned hook document.

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
