"""Command-line interface for open-revisit."""

from typing import Annotated

import typer

from open_revisit import __version__

app = typer.Typer(
    name="open-revisit",
    help="Turn satellite acquisitions into useful-observation service metrics.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Run open-revisit commands."""
