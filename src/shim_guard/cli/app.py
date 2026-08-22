"""The flat ``shim`` command registration layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from shim_guard.config import ENTITY_TYPES


class Client(StrEnum):
    CODEX = "codex"


Entity = StrEnum("Entity", {name: name for name in ENTITY_TYPES})


app = typer.Typer(
    name="shim",
    add_completion=False,
    help="Local, stdin-first prompt privacy for Codex.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def root(context: typer.Context) -> None:
    """Show the next action when no command is provided."""
    if context.invoked_subcommand is None:
        typer.echo("SHIM Guard — local prompt privacy for Codex. Try: shim help")


@app.command()
def help(context: typer.Context) -> None:
    """Show command usage and descriptions."""
    typer.echo(context.find_root().get_help())


@app.command()
def demo(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Run the local synthetic detector proof."""
    from shim_guard.cli.privacy import demo as run_demo

    run_demo(as_json=json_output)


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


@app.command("config")
def config_command(
    only: Annotated[
        list[Entity] | None,
        typer.Option(
            "--only",
            case_sensitive=False,
            metavar="ENTITY",
            help="Enable only this entity; repeatable.",
        ),
    ] = None,
    enable: Annotated[
        list[Entity] | None,
        typer.Option(
            "--enable",
            case_sensitive=False,
            metavar="ENTITY",
            help="Enable this entity; repeatable.",
        ),
    ] = None,
    disable: Annotated[
        list[Entity] | None,
        typer.Option(
            "--disable",
            case_sensitive=False,
            metavar="ENTITY",
            help="Disable this entity; repeatable.",
        ),
    ] = None,
    reset: bool = typer.Option(False, "--reset", help="Restore all defaults."),
    yes: bool = typer.Option(False, "--yes", help="Apply without confirmation."),
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Show or change locally enabled sensitive-data entities."""
    from shim_guard.cli.configuration import configure

    configure(
        only=tuple(map(str, only or ())),
        enable=tuple(map(str, enable or ())),
        disable=tuple(map(str, disable or ())),
        reset=reset,
        yes=yes,
        as_json=json_output,
    )


@app.command()
def install(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
    yes: bool = typer.Option(False, "--yes", help="Apply without confirmation."),
) -> None:
    """Preview or install the Codex prompt hook."""
    from shim_guard.cli.integrations import install as run_install

    run_install(dry_run=dry_run, yes=yes)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Show the Codex hook installation state."""
    from shim_guard.cli.integrations import status as run_status

    run_status(as_json=json_output)


@app.command()
def doctor(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    json_output: bool = typer.Option(False, "--json", help="Write a JSON result."),
) -> None:
    """Run Codex compatibility and hook health checks."""
    from shim_guard.cli.integrations import doctor as run_doctor

    run_doctor(as_json=json_output)


@app.command()
def revert(
    client: Annotated[Client, typer.Argument(case_sensitive=True, show_choices=True)],
    yes: bool = typer.Option(False, "--yes", help="Apply without confirmation."),
) -> None:
    """Remove only SHIM Guard's Codex prompt hook."""
    from shim_guard.cli.integrations import revert as run_revert

    run_revert(yes=yes)


def main() -> None:
    app()
