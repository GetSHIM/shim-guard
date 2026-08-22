# SHIM Guard agent guidance

Read `README.md`, `docs/architecture.md`, and `docs/compatibility.md` before changing the detector, hook protocol, or installer.

Apply the `ponytail:ponytail` and `code-writing-guidelines` skills to every implementation and review. Prefer the smallest complete change, standard-library boundaries, direct control flow, and no speculative abstractions.

## Safety boundaries

- The hook is synchronous, local, and network-free, with no daemon or history.
- Never log or persist raw prompts, findings, or replacement values. The only
  prompt-derived file is one `0600` typed redaction in OS temporary storage per
  supported block; remove it if the block response cannot be serialized.
- Safe hook input must produce exactly empty stdout and stderr.
- Handled hook errors must block with a generic native response.
- Entity settings default to all public types; malformed or unsafe settings fail
  closed, and the settings file must never contain prompt-derived data.
- Never modify real user configuration in development or tests; use temporary paths.
- Install may create a missing client settings file or append SHIM's exact hook
  group last while preserving unrelated settings and hooks; revert removes only
  that group and retains the document even when empty.
- Keep install and revert idempotent, leave Codex inline `config.toml` hooks
  untouched, preview only SHIM's fragment, and require manual setup for malformed,
  ambiguous, unsafe, or concurrently changed files.
- Do not add clients, auth, telemetry, daemons, proxies, plugin frameworks, or compatibility shims without an approved requirement.

## Checks

Use CPython 3.13 and uv `0.12.5`:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest
uv build --no-build-isolation
git diff --check
```
