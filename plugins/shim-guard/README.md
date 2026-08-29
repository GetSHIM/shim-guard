# SHIM Guard plugin

This plugin registers SHIM Guard's local `UserPromptSubmit` hook. It carries a
self-contained copy of the hook in `bin/shim.pyz`, so installing the plugin is
enough — there is no separate package-manager step, and no prerequisite beyond
a `python3` of 3.9 or newer.

## What runs

`hooks/run-shim-guard` resolves in this order and stops at the first that works:

1. `shim-guard-hook` on `PATH` — the package install. Preferred when present:
   it is the newest build and starts faster, because a zipapp has no bytecode
   cache and reparses its modules on every event.
2. `${CLAUDE_PLUGIN_ROOT}/bin/shim.pyz` — the archive bundled here.
3. Nothing runnable — the prompt is **allowed** and one line is written to
   stderr explaining why it was not inspected.

Case 3 never blocks. A guard that cannot run is a guard that is off, not a
reason to refuse someone's prompt.

`shim doctor <client>` reports which of the three is live, warns when the
bundled archive and the installed package disagree about their version, and
fails when both this plugin and a `shim install` hook are active for the same
client — that combination inspects every prompt twice.

## Choosing an installation method

The marketplace plugin and `shim install codex` / `shim install claude` are
alternatives. Do not use both for the same client. Adding the package as well
is fine and is not a duplicate: the launcher simply switches to it.

```console
uv tool install shim-guard
shim help
```

`bin/shim.pyz` is built by `scripts/build_zipapp.py` and is committed only on
release tags; a checkout between tags may not contain it, in which case the
launcher falls back to `PATH` or to case 3.
