# SHIM Guard plugin

This plugin registers SHIM Guard's local `UserPromptSubmit` hook. Install the
CLI first and make sure its executables are on `PATH`:

```console
uv tool install shim-guard
shim help
```

The marketplace plugin and `shim install codex` or `shim install claude` are
alternative hook installation methods. Do not use both for the same client.
