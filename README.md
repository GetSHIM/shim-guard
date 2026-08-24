# SHIM Guard

SHIM Guard is a local pre-submit prompt guard for hook-capable coding-agent
CLIs. It scans a submitted prompt in the client hook process and either stays
silent, replaces the model-facing prompt with a typed redaction, or stops the
client and saves a typed redaction for easy resubmission.

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
shim demo claude
shim demo copilot
shim config
shim install codex --dry-run
shim install codex
shim doctor codex
shim install claude
shim doctor claude
shim install copilot
shim doctor copilot
shim status codex
shim status claude
shim status copilot
shim revert codex
shim revert claude
shim revert copilot
```

### Marketplace plugins

Install the CLI before installing the SHIM Guard plugin from a Claude Code or
Codex marketplace:

```console
uv tool install shim-guard
shim help
```

The plugin registers the prompt hook and calls the installed
`shim-guard-hook` executable. Keep the uv tool bin directory on `PATH`, then
restart the client after installation. The marketplace plugin and
`shim install codex` or `shim install claude` are alternative hook installation
methods; do not use both for the same client.

For local marketplace testing from this repository:

```console
codex plugin marketplace add .
codex plugin add shim-guard@shim-guard
```

In Claude Code, run:

```text
/plugin marketplace add .
/plugin install shim-guard@shim-guard
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

## What the prompt hook does

Codex and Claude Code integrations use each client's native
`UserPromptSubmit` command-hook event. GitHub Copilot CLI uses
`userPromptTransformed`, which can replace the model-facing prompt directly.

```text
submitted prompt
  -> supported client invokes its local SHIM hook
  -> bounded offline detector evaluates the prompt in memory
  -> safe: exit 0 with empty stdout and stderr
  -> finding: Copilot receives the typed redaction directly
            | Codex/Claude write a private temporary redaction and return its path
  -> handled guard error: native client fail-closed response
```

The hook does not require a SHIM account, API key, network request, daemon,
telemetry, prompt log, finding log, or replacement map. For Copilot, SHIM
replaces the model-facing prompt with its typed redaction and creates no
temporary redaction file. For Codex and Claude Code, it creates one `0600`
redacted text file in the operating system's temporary directory and puts a
ready-to-copy `Read this file and use its contents as my prompt: <absolute
path>` instruction in the block response. Paste that whole line as the next
prompt so the agent can read the redaction. Detected raw values are not
included in SHIM's hook messages or redaction output. Redactions are typed and
ordinal, for example `<EMAIL_1>`.

The initial public entity allowlist is:

`EMAIL`, `PHONE`, `CREDIT_CARD`, `IBAN`, `IP_ADDRESS`, `MAC_ADDRESS`,
`US_SSN`, `TR_NATIONAL_ID`, `TR_VKN`, `SECRET`, and `DB_URI`.

The default preset enables every supported entity. `shim config` shows an
explicit `ON` or `OFF` state for each entity. Changes are previewed before they
are saved:

```console
shim config --only EMAIL --only SECRET
shim config --disable IP_ADDRESS --disable MAC_ADDRESS
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
recall across its 27 fixtures, with a positive and targeted safe negative for
each category. That narrow fixture-bound result is not a real-world statistical
guarantee.

## Privacy and limits

SHIM only covers a prompt after a supported client invokes a trusted, enabled
hook that starts and completes. It is not whole-machine DLP and cannot promise
detection of every sensitive value.

- The host client receives the raw prompt before the hook can decide. Other
  matching hooks start concurrently and can receive it too.
- Copilot's timeline can display the original prompt even though SHIM replaces
  the model-facing content and the value stored in session history.
- The host client or another tool may keep transcripts, logs, telemetry,
  caches, or history outside SHIM's control.
- A disabled, untrusted, missing, crashed, or timed-out hook is client
  controlled and can fail open. Some clients require changed hooks to be
  reviewed and trusted again.
- Users can intentionally disable individual entity detectors, including all
  of them. `shim config` shows the active policy.
- A redacted suggestion can still contain content the detector missed. Review
  the temporary file before sending it, then delete it when it is no longer
  needed. Otherwise it remains until the user or operating system cleans the
  temporary directory.
- SHIM does not claim secure memory erasure in Python and does not inspect
  files, tool output, transcript content, images, audio, clipboard history, or
  unsupported client events.

For the exact trust boundary and current client evidence, see
[Privacy](docs/privacy.md) and [Compatibility](docs/compatibility.md).

## Client installation ownership

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

`shim install claude --dry-run` follows the same ownership rules for
`$CLAUDE_CONFIG_DIR/settings.json` when `CLAUDE_CONFIG_DIR` is set and
`~/.claude/settings.json` otherwise. Claude Code stores hooks alongside other
user settings, so SHIM preserves every unrelated key and hook, appends its
exact group last, and uses the client's shell-free `command` plus `args` form.
Its block response asks Claude Code not to repeat the original prompt in the
block message. `shim revert claude` removes only that exact group and retains
the settings file.

`shim install copilot --dry-run` targets SHIM's dedicated user hook file at
`$COPILOT_HOME/hooks/shim-guard.json`, or
`~/.copilot/hooks/shim-guard.json` when `COPILOT_HOME` is unset. SHIM creates
missing private parent directories and refuses to overwrite unexpected content
at that path. The hook uses `userPromptTransformed` and returns
`modifiedTransformedPrompt`. `shim revert copilot` removes the exact hook while
retaining an empty versioned hook document.

## Compatibility and release gates

The implementation target is CPython 3.13 on macOS and Linux. Codex CLI
`0.149.0`, Claude Code `2.1.210`, and GitHub Copilot CLI `1.0.80` were locally
inspected. Codex reported its hook feature as stable and enabled; Claude Code
accepted SHIM's generated user settings through its native `doctor` command.
All three native hook contracts have repository fixtures.
This is not a claim that a live interactive session, every authentication mode,
trust review, or timeout behavior has been verified. Each additional client
still requires a native adapter and compatibility evidence. SHIM Protect
remains deferred.

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
