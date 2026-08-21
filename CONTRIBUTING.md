# Contributing

Keep changes small, local, and evidenced. Do not add client adapters, network
paths, telemetry, persistence, or a self-updater without an approved design.

Before a pull request:

```console
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest
uv build --no-build-isolation
git diff --check
```

Use synthetic values in tests and examples. Never commit prompts, secrets, or
real personal data. Changes to hook output, supported entities, installation,
or compatibility claims need contract and documentation updates.
