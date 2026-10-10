"""Command-line entry point: one Typer app over the pipeline.

The top layer: parses arguments, loads config and picks event sinks, then
hands over to `pipeline.runner.run_project`. Typer resolves annotations at
runtime, hence the per-file `TC` ignore for this package; commands import
everything else inside their functions (`PLC0415`) so `grill --help` stays
fast.
"""

from __future__ import annotations

import sys

from grillmaster.cli.app import app
from grillmaster.cli.args import expand_bare_remix
from grillmaster.cli.common import configure_console_logging

__all__ = ["app", "main"]


def main(argv: list[str] | None = None) -> None:
    configure_console_logging()
    args = expand_bare_remix(sys.argv[1:] if argv is None else argv)
    app(args=args, prog_name="grill")
