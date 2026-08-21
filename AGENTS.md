# SHIM Guard agent guidance

Read `README.md`, `docs/architecture.md`, and `docs/compatibility.md` before changing the detector, hook protocol, or installer.

Apply the `ponytail:ponytail` and `code-writing-guidelines` skills to every implementation and review. Prefer the smallest complete change, standard-library boundaries, direct control flow, and no speculative abstractions.

## Safety boundaries

- The hook is synchronous, local, stateless, and network-free.
- Never log or persist prompts, findings, suggestions, or replacement values.
- Safe hook input must produce exactly empty stdout and stderr.
- Handled hook errors must block with a generic native response.
- Never modify real user configuration in development or tests; use temporary paths.
- Install and revert may touch only an exact SHIM-owned Codex hook document and must refuse drift, shared files, symlinks, or unsafe permissions.
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
