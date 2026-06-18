"""Command-line interface for the video captioning pipeline."""

from pathlib import Path
import sys

import typer
from loguru import logger
from typing_extensions import Annotated

from project import ProgressStage
from services.progress import create_progress_reporter
from services.package import package_project_directory, prepare_noise
from settings import settings
from workflow import submit_project


RESERVED_COMMANDS = {"package", "noise", "process"}

legacy_app = typer.Typer(
    help=(
        "Owarai GrillMaster - Automatic transcription and translation for "
        "Japanese variety show videos"
    ),
    add_completion=False,
)
tools_app = typer.Typer(
    help="Owarai GrillMaster packaging tools",
    add_completion=False,
)
app = tools_app


def _run_process(
    source_str: str,
    translation_hint: str | None,
    break_after: ProgressStage | None,
    parent_project: str | None,
    refine: bool,
    glossary_check: bool,
    cover: bool,
    remix: str | None,
    prefix: bool,
) -> None:
    logger.info(
        f"CLI invoked with source_str={source_str}, "
        f"translation_hint={translation_hint}, break_after={break_after}, "
        f"parent_project={parent_project}, refine={refine}, "
        f"glossary_check={glossary_check}, cover={cover}, remix={remix}, "
        f"prefix={prefix}"
    )

    if prefix and remix is None:
        logger.error("--prefix requires --remix")
        raise typer.Exit(code=1)

    try:
        submit_project(
            source_str=source_str,
            translation_hint=translation_hint,
            break_after=break_after,
            parent_project_path=parent_project,
            enable_refine=refine,
            enable_glossary_check=glossary_check,
            enable_cover=cover,
            remix_noise_name=remix,
            remix_prefix=prefix,
        )
        logger.success(f"Successfully completed processing for {source_str}")
    except Exception as e:
        logger.error(f"Failed to process video {source_str}: {e}")
        raise typer.Exit(code=1)


@legacy_app.command()
@tools_app.command("process")
def process(
    source_str: Annotated[
        str,
        typer.Argument(
            help=(
                "Video source, id or url (e.g., 'BV1ZArvBaEqL', "
                "'https://www.bilibili.com/video/BV1ZArvBaEqL', "
                "'https://youtu.be/dQw4w9WgXcQ', 'v=dQw4w9WgXcQ')."
            ),
            show_default=False,
        ),
    ],
    translation_hint: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Translation hint for the video. If not provided, uses "
                "video title."
            ),
            show_default=False,
        ),
    ] = None,
    break_after: Annotated[
        ProgressStage | None,
        typer.Option(
            "--break-after",
            "--break",
            "-break",
            help=(
                "Stop after reaching the given workflow stage. "
                "Example: is_asr_completed."
            ),
            show_default=False,
        ),
    ] = None,
    parent_project: Annotated[
        str | None,
        typer.Option(
            "--parent-project",
            help=(
                "Path to a parent project directory whose pre_pass.json "
                "should seed this project's pre-pass for cross-episode "
                "consistency."
            ),
            show_default=False,
        ),
    ] = None,
    refine: Annotated[
        bool,
        typer.Option(
            "--refine",
            help=(
                "Force-enable subtitle refinement stage for this run. "
                "Overrides ENABLE_POSTPROCESS_REFINE setting."
            ),
        ),
    ] = False,
    glossary_check: Annotated[
        bool,
        typer.Option(
            "--glossary-check",
            help=(
                "Force-enable the fixed-glossary localization check stage "
                "for this run."
            ),
        ),
    ] = False,
    cover: Annotated[
        bool,
        typer.Option(
            "--cover",
            help=(
                "Force-enable async cover image generation for this run. "
                "Skipped entirely when --break-after is also set."
            ),
        ),
    ] = False,
    remix: Annotated[
        str | None,
        typer.Option(
            "--remix",
            help="Use a prepared noise set for remix packaging.",
            show_default=False,
        ),
    ] = None,
    prefix: Annotated[
        bool,
        typer.Option(
            "--prefix",
            help="Write a standalone noise output before remix videos.",
        ),
    ] = False,
) -> None:
    """Submit and process an online video for captioning and translation."""
    _run_process(
        source_str=source_str,
        translation_hint=translation_hint,
        break_after=break_after,
        parent_project=parent_project,
        refine=refine,
        glossary_check=glossary_check,
        cover=cover,
        remix=remix,
        prefix=prefix,
    )


@tools_app.command("package")
def package_command(
    project_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to an already-finalized project directory.",
            show_default=False,
        ),
    ],
    remix: Annotated[
        str | None,
        typer.Option(
            "--remix",
            help="Use a prepared noise set for remix packaging.",
            show_default=False,
        ),
    ] = None,
    prefix: Annotated[
        bool,
        typer.Option(
            "--prefix",
            help="Write a standalone noise output before remix videos.",
        ),
    ] = False,
) -> None:
    """Run only the package step for an existing project directory."""
    if prefix and remix is None:
        logger.error("--prefix requires --remix")
        raise typer.Exit(code=1)
    if settings.package_path is None:
        logger.error("PACKAGE_PATH is not set; cannot package project")
        raise typer.Exit(code=1)
    try:
        with create_progress_reporter() as progress:
            package_project_directory(
                project_dir=project_dir,
                package_root=settings.package_path,
                remix_noise_name=remix,
                remix_prefix=prefix,
                progress=progress,
            )
    except Exception as e:
        logger.error(f"Failed to package project {project_dir}: {e}")
        raise typer.Exit(code=1)


@tools_app.command("noise")
def noise_command(
    noise_name: Annotated[
        str,
        typer.Argument(
            help="Noise source name under PACKAGE_PATH/noise.",
            show_default=False,
        ),
    ],
    chunk_duration: Annotated[
        int,
        typer.Option(
            "--chunk-duration",
            help="Prepared noise chunk length in seconds.",
        ),
    ] = 300,
) -> None:
    """Prepare normalized noise chunks from PACKAGE_PATH/noise/NAME.webm."""
    if settings.package_path is None:
        logger.error("PACKAGE_PATH is not set; cannot prepare noise")
        raise typer.Exit(code=1)
    try:
        with create_progress_reporter() as progress:
            prepare_noise(
                package_root=settings.package_path,
                noise_name=noise_name,
                chunk_duration_seconds=chunk_duration,
                progress=progress,
            )
    except Exception as e:
        logger.error(f"Failed to prepare noise {noise_name}: {e}")
        raise typer.Exit(code=1)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the CLI application."""
    args = sys.argv[1:] if argv is None else argv
    standalone_mode = argv is None
    if args and args[0] in RESERVED_COMMANDS:
        tools_app(args=args, standalone_mode=standalone_mode)
        return
    legacy_app(args=args, standalone_mode=standalone_mode)


if __name__ == "__main__":
    main()
