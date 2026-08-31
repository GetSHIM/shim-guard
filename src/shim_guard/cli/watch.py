"""`shim watch -- claude [args...]` — run a client with a proxy in front of it.

The ordering here is the whole safety argument. The proxy binds *before* the
client starts, so a proxy that cannot start means no client is launched rather
than a client pointed at a dead port. The client then inherits a
base URL in its own environment only — nothing is written to a shell profile,
nothing is left behind, and the proxy dies with the command.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time

import typer

from shim_guard.cli.output import emit, emit_json, terminal_text

#: The environment variable each client reads for its API endpoint.
#: Copilot is absent on purpose: it routes a custom endpoint only through
#: bring-your-own-key, which removes GitHub authentication entirely, so there
#: is no existing authenticated session to watch.
BASE_URL_VARIABLES = {
    "claude": "ANTHROPIC_BASE_URL",
    "codex": "OPENAI_BASE_URL",
}
UPSTREAMS = {
    "claude": "api.anthropic.com",
    "codex": "api.openai.com",
}
#: Codex is untested behind a proxy. A real ChatGPT sign-in must verify it
#: before support is claimed, so it runs with a warning rather than being
#: silently promised.
VERIFIED = frozenset({"claude"})


def watch(*, command: tuple, as_json: bool) -> None:
    """Start the proxy, run the command under it, then report."""
    if not command:
        _fail(as_json, "Nothing to run. Try: shim watch -- claude")
    client = os.path.basename(command[0])
    variable = BASE_URL_VARIABLES.get(client, "")
    if not variable:
        _fail(
            as_json,
            f"shim watch does not support {client}. Supported: "
            + ", ".join(sorted(BASE_URL_VARIABLES)),
        )
    if shutil.which(command[0]) is None and not os.path.exists(command[0]):
        _fail(as_json, f"{command[0]} was not found on PATH.")

    from shim_guard.guard import evaluate
    from shim_guard.watch import proxy, report

    try:
        running = proxy.start(UPSTREAMS[client], evaluate)
    except OSError as error:
        # Before the client starts, so there is nothing to leave broken.
        _fail(as_json, f"The proxy could not start ({error}); nothing was run.")

    if not as_json:
        emit("PASS", f"Watching {client} on {running.base_url}. Nothing is modified.")
        if client not in VERIFIED:
            emit(
                "WARN",
                f"{client} behind a proxy is unverified; sign-in may not work.",
            )

    environment = dict(os.environ)
    environment[variable] = running.base_url
    started = time.monotonic()
    code = 0
    try:
        # The client owns the terminal: its stdio is inherited untouched so it
        # renders exactly as it would unwatched.
        process = subprocess.Popen(command, env=environment)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            # Ctrl-C belongs to the client. Pass it on and let it exit the way
            # it normally would rather than killing it from underneath.
            process.send_signal(signal.SIGINT)
            code = process.wait()
    except OSError as error:
        running.stop()
        _fail(as_json, f"{command[0]} could not be started ({error}).")
    finally:
        running.stop()

    elapsed = time.monotonic() - started
    if as_json:
        emit_json(
            "watch", "ok", exit_code=code, **report.as_json(running.session, elapsed)
        )
        raise typer.Exit(code)
    text = report.render(running.session, elapsed)
    if text:
        print(terminal_text(text, sys.stdout, "\n"))
    raise typer.Exit(code)


def _fail(as_json: bool, message: str):
    if as_json:
        emit_json("watch", "error", error=message)
    else:
        emit("FAIL", message, error=True)
    raise typer.Exit(2)


__all__ = ["BASE_URL_VARIABLES", "UPSTREAMS", "VERIFIED", "watch"]
