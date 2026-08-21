"""The flat ``shim`` command registration layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from shim_guard.clients import CLIENT_NAMES

Client = StrEnum("Client", {name.upper(): name for name in CLIENT_NAMES})


app = typer.Typer(
    name="shim",
    add_completion=False,
    help="Local, stdin-first prompt privacy for Codex.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def root(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        typer.echo("SHIM Guard — local prompt privacy for Codex. Try: shim demo codex")


@app.command()
def demo(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    from shim_guard.cli.privacy import demo as run_demo

    run_demo(client, as_json=json_output)


@app.command()
def scan(
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Scan bounded UTF-8 text from standard input."""
    from shim_guard.cli.privacy import scan as run_scan

    run_scan(as_json=json_output)


@app.command()
def redact(
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Redact bounded UTF-8 text from standard input."""
    from shim_guard.cli.privacy import redact as run_redact

    run_redact(as_json=json_output)


@app.command()
def install(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
    yes: bool = typer.Option(False, "--yes", help="Apply without confirmation."),
) -> None:
    from shim_guard.cli.integrations import install as run_install

    run_install(client, dry_run=dry_run, yes=yes)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    from shim_guard.cli.integrations import status as run_status

    run_status(as_json=json_output)


@app.command()
def doctor(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    from shim_guard.cli.integrations import doctor as run_doctor

    run_doctor(client, as_json=json_output)


@app.command()
def revert(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    yes: bool = typer.Option(False, "--yes", help="Apply without confirmation."),
) -> None:
    from shim_guard.cli.integrations import revert as run_revert

    run_revert(client, yes=yes)


def main() -> None:
    app()
