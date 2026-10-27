"""Inspect telop cards when acting on feedback: `uv run python scripts/telop_probe.py <command> ...`.

A project is its directory, or its ID (local, else archived under `[paths]
archive`). Times are seconds or m:ss. Cards are rebuilt with the working
tree's `scan.py` from the kept scan and `ja.srt`, so a rule change shows at
once, without OCR or an agent run.

- `cards <project> <from> [<to>]`: the cards around a time (default ±15 s),
  with kind, whether they are offered, their box and the last selection's
  translation (`.subs/telop.cht.json`).
- `lines <project> <regex>`: tracked OCR lines whose raw text matches, with
  their box in pixels (a line's height, or a vertical line's width, is its
  character size).
- `frames <project> <time>...`: stills at those times, saved to a temp
  folder; their paths are printed.
- `diff <baseline scan.py> [<project>...]`: the offered cards a baseline
  `scan.py` (e.g. `git show HEAD:src/grillmaster/telop/scan.py > old.py`)
  and the working tree build differently, on every scanned project by
  default (local and archived): measure a rule change before keeping it.
- `review <baseline scan.py> [<project>...] [--out <dir>]`: the same
  changes as images to show the owner, one per moment: the still before
  (left) and after (right), each changed card boxed in a colour; it prints
  each image's path and the cards by colour.

Output is UTF-8; in PowerShell set `[Console]::OutputEncoding =
[Text.Encoding]::UTF8` first.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.cli.common import load_or_exit, locate_project_or_exit
from grillmaster.core.srt import read_srt_file
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner, ffmpeg
from grillmaster.media.frames import extract_frames
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.naming import archived_dirs
from grillmaster.telop import scan

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from grillmaster.telop.scan import TelopCards

_FRAME_W, _FRAME_H = 1920, 1080
_WINDOW_S = 15.0
_FRAME_SIDE = 960


def _seconds(text: str) -> float:
    minutes, _, seconds = text.rpartition(":")
    return float(minutes or 0) * 60 + float(seconds)


def _layout(project: str) -> ProjectLayout:
    location, _ = locate_project_or_exit(project, load_or_exit())
    return location.layout


def _cards(layout: ProjectLayout, module: ModuleType = scan) -> TelopCards:
    found = module.TelopScan.model_validate_json(layout.telop_scan.read_text("utf-8"))
    return module.build_cards(found, read_srt_file(layout.ja_srt))


def _kept(layout: ProjectLayout) -> dict[tuple[float, str], str]:
    """The last selection's translations, by (start, card text)."""
    if not layout.telop_cht_json.exists():
        return {}
    items = json.loads(layout.telop_cht_json.read_text("utf-8"))["items"]
    return {(item["start"], item["ja"]): item["zh"] for item in items}


def _box(box: tuple[float, float, float, float]) -> str:
    return f"({box[0]:.3f},{box[1]:.3f})-({box[2]:.3f},{box[3]:.3f})"


def cards_command(project: str, start: str, end: str | None) -> None:
    layout = _layout(project)
    lo = _seconds(start) - (0 if end else _WINDOW_S)
    hi = _seconds(end) if end else _seconds(start) + _WINDOW_S
    cards = _cards(layout)
    offered = {card.id for card in cards.offered}
    kept = _kept(layout)
    for card in cards.cards:
        if card.end < lo or card.start > hi:
            continue
        mark = "OFFER" if card.id in offered else "     "
        zh = kept.get((card.start, card.text))
        print(
            f"{card.id:5} {mark} {card.start:7.1f}-{card.end:7.1f} "
            f"{card.kind.value:9} {_box(card.box)} {card.text}"
            + (f"\n{'':20}=> {zh.replace(chr(10), ' | ')}" if zh else "")
        )


def lines_command(project: str, pattern: str) -> None:
    layout = _layout(project)
    found = scan.TelopScan.model_validate_json(layout.telop_scan.read_text("utf-8"))
    wanted = re.compile(pattern)
    for line in scan.track_lines(found):
        if wanted.search(line.text):
            x0, y0, x1, y1 = line.box
            print(
                f"{line.first / found.fps:7.1f}-{(line.last + 1) / found.fps:7.1f} "
                f"w={(x1 - x0) * _FRAME_W:5.0f} h={(y1 - y0) * _FRAME_H:5.0f} "
                f"conf={line.confidence:.2f} {_box(line.box)} {line.text}"
            )


def frames_command(project: str, times: list[str]) -> None:
    layout = _layout(project)
    out_dir = Path(tempfile.gettempdir()) / "grill-telop-probe" / layout.root.name[:40]
    paths = extract_frames(
        SubprocessFfmpegRunner(),
        layout.video,
        [_seconds(time) for time in times],
        out_dir,
        _FRAME_SIDE,
    )
    for path in paths:
        print(path)


def _scanned() -> Iterator[ProjectLayout]:
    loaded = load_or_exit()
    roots = [*loaded.projects_root.iterdir()] if loaded.projects_root.is_dir() else []
    if loaded.config.paths.archive is not None:
        roots += archived_dirs(loaded.config.paths.archive)
    for root in roots:
        layout = ProjectLayout(root)
        if layout.telop_scan.exists() and layout.ja_srt.exists():
            yield layout


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_scan", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def diff_command(baseline: Path, projects: list[str]) -> None:
    old = _load_module(baseline)
    layouts = [_layout(project) for project in projects] or list(_scanned())
    for layout in layouts:
        before = {(c.start, c.text) for c in _cards(layout, old).offered}
        after = {(c.start, c.text) for c in _cards(layout).offered}
        print(f"=== {layout.root.name}: offered {len(before)} -> {len(after)}")
        for sign, changed in (("+", after - before), ("-", before - after)):
            for start, text in sorted(changed):
                print(f"  {sign} {start:7.1f} {text}")


_SAME_MOMENT_S = 1.5
# Box colours, in card order, with the names `review` prints for them.
_COLOURS = (
    ("red", "0xE53935"),
    ("green", "0x2E9D3A"),
    ("blue", "0x2F5BE8"),
    ("orange", "0xF0A000"),
    ("purple", "0xB03AC8"),
    ("cyan", "0x00A5B5"),
)


def _video_id(layout: ProjectLayout) -> str:
    """The video ID in a project directory's name (`YYMMDD_<id>_<title>`)."""
    date, _, rest = layout.root.name.partition("_")
    if re.fullmatch(r"\d{6}", date) and "_" in rest:
        return rest.partition("_")[0]
    return layout.root.name


def _moments(
    before: list[scan.TelopCard], after: list[scan.TelopCard]
) -> list[tuple[list[scan.TelopCard], list[scan.TelopCard]]]:
    """Changed cards, before and after, grouped by when they appear."""
    tagged = sorted(
        [(card, False) for card in before] + [(card, True) for card in after],
        key=lambda pair: pair[0].start,
    )
    moments: list[tuple[list[scan.TelopCard], list[scan.TelopCard]]] = []
    last = -_SAME_MOMENT_S - 1
    for card, is_after in tagged:
        if card.start - last > _SAME_MOMENT_S:
            moments.append(([], []))
        moments[-1][is_after].append(card)
        last = card.start
    return moments


def _boxes(cards: list[scan.TelopCard]) -> str:
    """An ffmpeg filter drawing each card's box in its colour."""
    return (
        ",".join(
            f"drawbox=x=iw*{x0:.4f}:y=ih*{y0:.4f}:w=iw*{x1 - x0:.4f}:h=ih*{y1 - y0:.4f}"
            f":color={_COLOURS[n % len(_COLOURS)][1]}:t=4"
            for n, card in enumerate(cards)
            for x0, y0, x1, y1 in [card.box]
        )
        or "null"
    )


def review_command(baseline: Path, projects: list[str], out_dir: Path) -> None:
    old = _load_module(baseline)
    layouts = [_layout(project) for project in projects] or list(_scanned())
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = SubprocessFfmpegRunner()
    for layout in layouts:
        before_cards = _cards(layout, old).offered
        after_cards = _cards(layout).offered
        before_keys = {(c.start, c.text) for c in before_cards}
        after_keys = {(c.start, c.text) for c in after_cards}
        gone = [c for c in before_cards if (c.start, c.text) not in after_keys]
        new = [c for c in after_cards if (c.start, c.text) not in before_keys]
        video_id = _video_id(layout)
        stills = Path(tempfile.gettempdir()) / "grill-telop-probe" / video_id
        for was, now in _moments(gone, new):
            shown = [*was, *now]
            latest = max(card.start for card in shown)
            # When every changed card is up, if they ever all are.
            time = max(latest, min(latest + 0.5, min(c.end for c in shown) - 0.1))
            (still,) = extract_frames(runner, layout.video, [time], stills, _FRAME_SIDE)
            minutes, seconds = divmod(int(min(card.start for card in shown)), 60)
            image = out_dir / f"{video_id}_{minutes}m{seconds:02d}s.jpg"
            runner.run(
                ffmpeg(
                    "-i",
                    str(still),
                    "-filter_complex",
                    f"[0]split[a][b];[a]{_boxes(was)},pad=iw+16:ih:0:0:white[l];"
                    f"[b]{_boxes(now)}[r];[l][r]hstack",
                    "-q:v",
                    "3",
                    str(image),
                )
            )
            print(f"{image}  (left: before, right: after)")
            for side, cards in (("before", was), ("after", now)):
                for n, card in enumerate(cards):
                    print(f"  {side:6} {_COLOURS[n % len(_COLOURS)][0]:6} {card.text}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cards = commands.add_parser("cards")
    cards.add_argument("project")
    cards.add_argument("start")
    cards.add_argument("end", nargs="?")
    lines = commands.add_parser("lines")
    lines.add_argument("project")
    lines.add_argument("pattern")
    frames = commands.add_parser("frames")
    frames.add_argument("project")
    frames.add_argument("times", nargs="+")
    diff = commands.add_parser("diff")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("projects", nargs="*")
    review = commands.add_parser("review")
    review.add_argument("baseline", type=Path)
    review.add_argument("projects", nargs="*")
    review.add_argument(
        "--out",
        type=Path,
        default=Path(tempfile.gettempdir()) / "grill-telop-probe" / "review",
    )
    args = parser.parse_args()
    match args.command:
        case "cards":
            cards_command(args.project, args.start, args.end)
        case "lines":
            lines_command(args.project, args.pattern)
        case "frames":
            frames_command(args.project, args.times)
        case "review":
            review_command(args.baseline, args.projects, args.out)
        case _:
            diff_command(args.baseline, args.projects)


if __name__ == "__main__":
    main()
