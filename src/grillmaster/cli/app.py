"""The `grill` Typer app: `run` (also the bare `grill <src>`), `serial`,
`package`, `archive`, `reset`, `status`, `doctor`."""

from __future__ import annotations

import click
import typer
from typer.core import TyperGroup

from grillmaster.cli.archive import archive_command
from grillmaster.cli.doctor import doctor_command
from grillmaster.cli.package import package_command
from grillmaster.cli.reset import reset_command
from grillmaster.cli.run import run_command
from grillmaster.cli.serial import serial_command
from grillmaster.cli.status import status_command

DEFAULT_COMMAND = "run"


class RunByDefault(TyperGroup):
    """`grill <src> [HINT] [options]` means `grill run <src> ...`.

    Anything that is not a command name or the help flag dispatches to
    `run`, so options may come before the source too.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if (
            args
            and args[0] not in self.commands
            and args[0] not in ctx.help_option_names
        ):
            args = [DEFAULT_COMMAND, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=RunByDefault,
    name="grill",
    help=(
        "Owarai GrillMaster: Traditional Chinese subtitles for Japanese variety shows."
    ),
    no_args_is_help=True,
    add_completion=False,
)
app.command(DEFAULT_COMMAND)(run_command)
app.command("serial")(serial_command)
app.command("package")(package_command)
app.command("archive")(archive_command)
app.command("reset")(reset_command)
app.command("status")(status_command)
app.command("doctor")(doctor_command)
