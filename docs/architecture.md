# Architecture

SHIM Guard is one local Python distribution. The CLI is a management and
local-inspection interface; the Codex hook is the prompt path. It has no daemon,
history, or service state and creates one temporary redaction per supported
block.

```text
shim config -> guarded local entity policy
shim scan/redact (stdin) -> policy -> detector
                         -> categories/counts or typed redaction

Codex UserPromptSubmit -> hook adapter -> policy -> detector
                       -> allow | 0600 temporary redaction -> native block with path
shim install/status/doctor/revert -> guarded merge/revert -> Codex hooks.json
```

The detector is functional and offline. It normalizes input once, validates
bounded findings against a selected subset of the fixed entity allowlist,
resolves spans deterministically, and produces typed ordinal placeholders. The
hook owns stdin/stdout/stderr, the Codex protocol, and its per-block temporary
redaction file. Installation owns only SHIM's matcher-group and entity-policy
planning plus guarded filesystem I/O. Install creates an absent `hooks.json`,
or preserves a valid document and appends SHIM's exact matcher group last after
informing the user. Revert removes only that exact group and retains the
document even when empty. Both operations are idempotent.

The local TOML policy defaults to every public entity. The CLI and hook validate
it through the same bounded, no-symlink file inspection path; the detector
itself remains pure and receives the enabled tuple explicitly. Malformed or
unsafe policy files fail closed. Configuration stores entity names only—never
prompt-derived data.

Inline `config.toml` hooks remain untouched and may coexist with `hooks.json`
with a Codex warning. Malformed or ambiguous hook documents and unsafe or
concurrently changed hook files require manual setup. Dry-run output contains
only SHIM's fragment, not the existing hook document.

## Detector boundary and corpus

`shim-guard` is intentionally an independently packaged, narrow detector fork.
It does not import the parent SHIM gateway: the gateway's reversible maps,
provider flow, persistence, and broader runtime are outside a local synchronous
hook. The public `guard-v1` synthetic corpus is Guard's executable detector
contract and a migration reference for the parent gateway; it does not claim
current result parity between the independently released implementations.
Guard category coverage and behavior change only with an explicit corpus
update.

The secret recognizer is deliberately stricter than broad gateway-style prose
matching. Assignment-style secrets require `key = value` or `key: value` (with
the explicit `--password` form also supported); prose such as “a password
should never be shared” remains safe. This reduces false blocks at the prompt
boundary without claiming complete secret detection.

No daemon, database, HTTP client, account, analytics, plugin framework,
provider-neutral adapter layer, or reversible raw-value store belongs in this
MVP. A later client gets its own native adapter and fixtures; it does not widen
the hook's trust boundary.

The [Codex hooks reference](https://developers.openai.com/codex/hooks/) is the
authoritative protocol source. Codex runs matching command hooks concurrently,
requires trust for non-managed hooks, and treats exit 0 with no output as a
continuation. SHIM uses `UserPromptSubmit`, not a generic cross-client hook
format.
