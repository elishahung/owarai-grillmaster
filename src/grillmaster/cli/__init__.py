"""Command-line entry point: one Typer app over the pipeline.

The top layer: parses arguments, loads config and picks event sinks, then
hands over to `pipeline.runner.run_project`. Typer resolves annotations at
runtime, hence the per-file `TC` ignore for this package; commands import
everything else inside their functions (`PLC0415`) so `grill --help` stays
fast.
"""

from __future__ import annotations

import io
import sys

from grillmaster.cli.app import app
from grillmaster.cli.args import expand_bare_options
from grillmaster.cli.common import configure_console_logging

__all__ = ["app", "main"]


def main(argv: list[str] | None = None) -> None:
    redirected_streams_as_utf8()
    configure_console_logging()
    args = expand_bare_options(sys.argv[1:] if argv is None else argv)
    app(args=args, prog_name="grill")


def redirected_streams_as_utf8() -> None:
    """Write UTF-8 to a redirected stdout/stderr. A console already takes
    Unicode, but a pipe or file defaults to the locale code page (cp950),
    which cannot encode the Japanese titles commands print."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
