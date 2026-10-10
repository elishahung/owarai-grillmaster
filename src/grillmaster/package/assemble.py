"""The deliverable folder: its lifetime, its plain-file contents, the burn plan.

The caller fills the folder in a fixed order: plain copies (cover, `info.json`,
reports, inserts) land first so the folder is inspectable while the long
render runs, then the render. `deliverable_dir` builds it under a staging name
and swaps it in only when everything succeeded, so a deliverable is either
complete or the previous one. `PACKAGE_INNER_PATH_RESERVE` is the room its
entries need below the folder (see `project.naming`).
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ValidationError

from grillmaster.core.fs import staged_dir
from grillmaster.core.json_artifact import read_model
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.live_chat.render import chat_placement, write_chat_ass
from grillmaster.live_chat.schema import TranslatedChatLog
from grillmaster.media import probe
from grillmaster.media.errors import MediaError
from grillmaster.package.errors import PackageError
from grillmaster.package.inserts import (
    INSERT_OUTPUT_MAX_LENGTH,
    INSERT_SUFFIX_ALLOWANCE,
)
from grillmaster.package.render import BurnPlan, SubtitleLayer

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.media.ffmpeg import FfmpegRunner

# The deliverable folder is flat. Its fixed entries: the plain burn-in, the
# cover (`extras.cover` copies `cover.png` or `cover.jpg`), `info.json` and
# the agent reports; a remix writes `1.mp4`, `2.mp4`, ... instead of the
# video. `config` reserves these stems (and all-digit ones) so an insert
# output can never collide with them.
VIDEO_FILE_NAME = "video.mp4"
INFO_FILE_NAME = "info.json"
REFINE_REPORT_NAME = "refine.md"
GLOSSARY_REPORT_NAME = "glossary_check.md"
FIXED_ENTRY_NAMES = (
    VIDEO_FILE_NAME,
    "cover.png",
    "cover.jpg",
    INFO_FILE_NAME,
    REFINE_REPORT_NAME,
    GLOSSARY_REPORT_NAME,
)
# Remix parts are numbered from 1; no realistic show reaches 100 parts.
_LONGEST_REMIX_PART = "99.mp4"
# Room kept inside the deliverable folder for its own entries (inserts
# included), counting the leading separator; `project.naming` adds the
# staging name on top.
PACKAGE_INNER_PATH_RESERVE = 1 + max(
    *(len(name) for name in FIXED_ENTRY_NAMES),
    len(_LONGEST_REMIX_PART),
    INSERT_OUTPUT_MAX_LENGTH + INSERT_SUFFIX_ALLOWANCE,
)


def require_inputs(*paths: Path) -> None:
    """Fail before creating anything when a render input is missing."""
    for path in paths:
        if not path.is_file():
            raise PackageError(f"Cannot package without {path}")


@contextmanager
def deliverable_dir(destination: Path) -> Iterator[Path]:
    """An empty staging folder that becomes `destination` when the body
    succeeds (`core.fs.staged_dir`): a deliverable is either complete or the
    previous one."""
    with staged_dir(destination) as staging:
        yield staging


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
