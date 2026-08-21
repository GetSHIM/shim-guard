# SHIM Guard

SHIM Guard is a local, Codex-first pre-submit prompt guard for common sensitive
values. It scans a submitted prompt in the hook process and either stays silent
or asks Codex to stop before submission with a typed redaction suggestion.

It is an alpha. Treat the compatibility and release gates below as part of the
product boundary.

## Install

Use one package manager:

```console
uv tool install shim-guard
# or
pipx install shim-guard
```

The command is `shim`:

```console
shim
shim help
shim demo codex
shim config
shim install codex --dry-run
shim install codex
shim doctor codex
shim status
shim revert codex
```

For direct local inspection, pipe text instead of placing it in shell history
or a process list:

```console
printf '%s' 'Contact me at alice@example.com' | shim scan
printf '%s' 'Contact me at alice@example.com' | shim redact
```

`scan` and `redact` read stdin. Do not pass real prompts as command-line
arguments. `redact --json` reports only status and counts; use the default
output when piping typed text. Redirected output preserves surrounding text,
while an interactive terminal escapes non-printing control characters.

## What the Codex hook does

The supported integration is Codex's `UserPromptSubmit` command-hook event.

```text
submitted prompt
  -> Codex invokes the local SHIM hook
  -> bounded offline detector evaluates the prompt in memory
  -> safe: exit 0 with empty stdout and stderr
  -> finding or handled guard error: native Codex stop response
```

The hook does not require a SHIM account, API key, network request, daemon,
telemetry, prompt log, finding log, suggestion store, or replacement map.
Detected raw values are not included in SHIM's hook messages. Redactions are
typed and ordinal, for example `<EMAIL_1>`.

The initial public entity allowlist is:

`EMAIL`, `PHONE`, `CREDIT_CARD`, `SECRET`, `US_SSN`, `IP_ADDRESS`,
`MAC_ADDRESS`, `DB_URI`, `FILE_PATH`, `TR_NATIONAL_ID`, `TR_VKN`, and `IBAN`.

The default preset enables every entity except `FILE_PATH`, which is opt-in to
avoid noisy path matches in coding workflows. `shim config` shows an explicit
`ON` or `OFF` state for each entity. Changes are previewed before they are
saved:

```console
shim config --only EMAIL --only SECRET
shim config --disable IP_ADDRESS --disable MAC_ADDRESS
shim config --enable FILE_PATH
shim config --enable PHONE
shim config --reset
```

The repeatable options can be combined except that `--only` and `--reset` are
standalone modes. `scan`, `redact`, and the installed hook use the selection;
the synthetic demo always checks its built-in fixture. Settings are stored in
`$XDG_CONFIG_HOME/shim-guard/config.toml`, or
`~/.config/shim-guard/config.toml` when XDG is unset. Malformed contents block
inspection safely and can be replaced with `shim config --reset --yes`. Unsafe
paths remain untouched for manual review. Selecting no entities is allowed but
shown as a warning.

`guard-v1-metrics.json` reports 100% synthetic case-category precision and
recall across its 30 fixtures, with a positive and targeted safe negative for
each category. That narrow fixture-bound result is not a real-world statistical
guarantee.

## Privacy and limits

SHIM only covers a prompt after Codex invokes a trusted, enabled hook that
starts and completes. It is not whole-machine DLP and cannot promise detection
of every sensitive value.

- Codex receives the raw prompt before the hook can decide. Other matching
  hooks start concurrently and can receive it too.
- Codex or another tool may keep transcripts, logs, telemetry, caches, or
  history outside SHIM's control.
- A disabled, untrusted, missing, crashed, or timed-out hook is client
  controlled and can fail open. Changed non-managed Codex hooks need review and
  trust again.
- Users can intentionally disable individual entity detectors, including all
  of them. `shim config` shows the active policy.
- A redacted suggestion can still contain content the detector missed. Review
  it before sending.
- SHIM does not claim secure memory erasure in Python and does not inspect
  files, tool output, transcript content, images, audio, clipboard history, or
  unsupported client events.

For the exact trust boundary, see [Privacy](docs/privacy.md) and the current
[Codex hook documentation](https://developers.openai.com/codex/hooks/).

## Installation ownership

`shim install codex --dry-run` shows the target and exact owned fragment. It
uses `$CODEX_HOME/hooks.json` when `CODEX_HOME` is set and otherwise
`~/.codex/hooks.json`. When that file is absent, SHIM creates it. When it is a
valid existing hook document, SHIM preserves every existing hook, tells the
user that the document is shared, and appends SHIM's exact matcher group last.
The dry run shows only SHIM's fragment, never the whole settings-file diff.

SHIM leaves inline hooks in Codex's `config.toml` untouched. Codex may load
both representations and warn that they coexist. Malformed or ambiguous hook
documents, unsafe hook-file paths or permissions, and concurrently changed
files require reviewed manual setup. SHIM does not create a full-file backup
or read credential stores.

Repeated install and revert are safe no-ops. `shim revert codex` removes only
SHIM's exact matcher group, preserves every other hook, and retains the hook
document even when it becomes empty.

## Compatibility and release gates

The implementation target is CPython 3.13 on macOS and Linux. Codex CLI
`0.149.0` was locally inspected, reported its hook feature as stable and
enabled, and native hook contract fixtures are tested.
This is not a claim that a live interactive Codex session, every authentication
mode, trust review, or timeout behavior has been verified. Claude Code,
additional clients, and SHIM Protect are deferred.

Before a public release, maintainers must record real-client compatibility,
trusted-hook activation, the supported authentication routes, corpus results,
and fresh-process latency. Publication is gated on those facts; placeholders
are deliberately not metrics. See [Compatibility](docs/compatibility.md).

## Development

```console
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest
uv build --no-build-isolation
git diff --check
```

See [Architecture](docs/architecture.md), [Contributing](CONTRIBUTING.md), and
[Security](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
