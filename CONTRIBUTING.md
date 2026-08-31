# Contributing

Keep changes small, local, and evidenced. Do not add client adapters, network
paths, telemetry, persistent state, or a self-updater without an approved
design.

Before a pull request:

```console
python scripts/check.py
```

Use synthetic values in tests and examples. Never commit real prompts, secrets,
or personal data. Changes to hook output, supported entities, installation, or
compatibility claims need contract and documentation updates.
