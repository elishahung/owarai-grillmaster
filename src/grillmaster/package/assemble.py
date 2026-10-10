"""The deliverable folder: its lifetime, its plain-file contents, the burn plan.

The caller fills the folder in a fixed order: plain copies (cover, `info.json`,
reports, inserts) land first so the folder is inspectable while the long
render runs, then the render. `deliverable_dir` builds it under a staging name
and swaps it in only when everything succeeded, so a deliverable is either
complete or the previous one.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ValidationError

from grillmaster.core.json_artifact import read_model
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.live_chat.render import chat_placement, write_chat_ass
from grillmaster.live_chat.schema import TranslatedChatLog
from grillmaster.media import probe
from grillmaster.media.errors import MediaError
from grillmaster.package.errors import PackageError
from grillmaster.package.render import BurnPlan, SubtitleLayer

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.media.ffmpeg import FfmpegRunner

# The deliverable is built as `<name>.partial`; the replaced one waits as
# `<name>.old` until the new one is in place.
STAGING_SUFFIX = ".partial"
_BACKUP_SUFFIX = ".old"
# Room kept inside the deliverable folder for its own entries, counting the
# leading separator: the folder is flat and its longest fixed entry is
# `/glossary_check.md` (insert outputs are short user-chosen stems). It is
# built under the longer staging name.
PACKAGE_INNER_PATH_RESERVE = 24 + len(STAGING_SUFFIX)
INFO_FILE_NAME = "info.json"


def require_inputs(*paths: Path) -> None:
    """Fail before creating anything when a render input is missing."""
    for path in paths:
        if not path.is_file():
            raise PackageError(f"Cannot package without {path}")


@contextmanager
def deliverable_dir(destination: Path) -> Iterator[Path]:
    """An empty staging folder that becomes `destination` when the body
    succeeds.

    The body fills `<destination>.partial`; on success it replaces
    `destination`, on failure it is removed and an existing `destination`
    (the previous deliverable) is untouched. A staging folder left by a
    crash is cleared first.
    """
    staging = destination.with_name(f"{destination.name}{STAGING_SUFFIX}")
    if staging.exists():
        logger.warning(f"Removing a stale deliverable staging folder: {staging}")
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        yield staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _swap_in(staging, destination)


def _swap_in(staging: Path, destination: Path) -> None:
    """Replace `destination` with `staging`; the old one is restored when the
    final rename fails."""
    backup = destination.with_name(f"{destination.name}{_BACKUP_SUFFIX}")
    if destination.exists():
        logger.info(f"Replacing the existing deliverable: {destination}")
        if backup.exists():
            shutil.rmtree(backup)
        destination.rename(backup)
    try:
        staging.replace(destination)
    except BaseException:
        if backup.exists():
            backup.rename(destination)
        raise
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as error:
            logger.warning(
                f"Could not remove the replaced deliverable {backup}: {error}"
            )


def write_info(
    target_dir: Path, *, titles: Mapping[str, object] | None, briefing: Briefing
) -> Path:
    """Write `info.json`: the title suggestions first (what a human reads
    first), then the briefing in its prompt view (term lists as objects)."""
    info: dict[str, object] = dict(titles or {})
    info.update(briefing.prompt_dict())
    target = target_dir / INFO_FILE_NAME
    target.write_text(json.dumps(info, ensure_ascii=False, indent=4), encoding="utf-8")
    logger.info(f"Wrote package artifact: {target}")
    return target


def copy_reports(target_dir: Path, reports: Mapping[str, Path]) -> list[Path]:
    """Copy each existing report (`{target name: source}`); absent ones are
    skipped."""
    copies: list[Path] = []
    for name, source in reports.items():
        if not source.is_file():
            continue
        target = target_dir / name
        shutil.copy2(source, target)
        logger.info(f"Copied package artifact: {source} -> {target}")
        copies.append(target)
    return copies


def build_burn_plan(
    runner: FfmpegRunner,
    *,
    video: Path,
    dialogue: Path,
    chat: Path,
    chat_ass: Path,
    layout: ChatLayout,
) -> BurnPlan:
    """The burn plan: the dialogue, under a chat panel when there is one.

    With a translated `chat` and a layout other than `NONE`, the panel is
    rendered to `chat_ass` (at package time, so a layout change only needs a
    re-package) and the picture and dialogue are placed per the layout.
    Otherwise — or when the panel cannot be rendered, with a warning — the
    dialogue burns alone on the full frame.
    """
    if layout is ChatLayout.NONE or not chat.exists():
        return BurnPlan.dialogue(dialogue)
    placement = chat_placement(layout)
    try:
        write_chat_ass(
            read_model(chat, TranslatedChatLog),
            probe.duration(runner, video),
            chat_ass,
            layout,
        )
    except (OSError, UnicodeDecodeError, ValidationError, MediaError) as error:
        logger.warning(f"Chat panel skipped ({chat}): {error}")
        return BurnPlan.dialogue(dialogue)
    logger.info(f"Rendered {layout} chat panel: {chat_ass}")
    return BurnPlan(
        layers=(
            SubtitleLayer(chat_ass),
            SubtitleLayer(dialogue, placement.dialogue_style),
        ),
        picture=placement.picture,
    )
