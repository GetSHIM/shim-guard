# SHIM Guard plugin

This plugin registers SHIM Guard's local hooks — the user's prompt, and the tool
events whose mutation shape has been confirmed against a running client. It carries a
self-contained copy of the hook in `bin/shim.pyz`, so installing the plugin is
enough — there is no separate package-manager step, and no prerequisite beyond
a `python3` of 3.9 or newer.

**The plugin is the hooks, and only the hooks.** `shim watch`, `shim report`
and `shim config` are commands, not hook events, so they are not in the archive
— deliberately, because the hook runs as a cold-start subprocess on every tool
call and must not pay for imports it never uses. Install the package as well if
you want them; the two are not a duplicate and the launcher simply prefers the
package when it is present.

## What runs

`hooks/run-shim-guard` resolves in this order and stops at the first that works:

1. `shim-guard-hook` on `PATH` — the package install. Preferred when present:
   it is the newest build and starts faster, because a zipapp has no bytecode
   cache and reparses its modules on every event.
2. `${CLAUDE_PLUGIN_ROOT}/bin/shim.pyz` — the archive bundled here.
3. Nothing runnable — the prompt is **allowed** and one line is written to
   stderr explaining why it was not inspected.

Case 3 never blocks. A guard that cannot run is a guard that is off, not a
reason to refuse someone's prompt. The same holds for a tool event: an empty
stdout leaves the tool call and its result exactly as the client produced them.

`shim doctor <client>` prints a coverage table of what SHIM sees and can change
at each event, reports which of the three launchers is live, warns when the
bundled archive and the installed package disagree about their version, and
fails when both this plugin and a `shim install` hook are active for the same
client — that combination inspects every prompt and every tool event twice.

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
