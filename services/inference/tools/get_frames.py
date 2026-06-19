"""Shared implementation for on-demand project frame extraction.

This is the on-demand frame tool the agent backends (gemini-cli / codex /
claude) run during pre-pass, chunk translation, and refine when they need to see
a specific moment of the video (doubtful ASR, an unclear proper noun, an
on-screen text card). Stage-specific wrapper scripts expose the small CLI
surface agents should use: ``--project-dir`` and ``--times``.

Frame size is the user-configured ``video_frame_max_side`` (read from the repo
``.env``); the agent does not control it.
"""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum
from pathlib import Path

# Make project imports resolve no matter the launch cwd: this file lives at
# <repo>/services/inference/tools/get_frames.py, so the repo root is 3 levels up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from settings import Settings  # noqa: E402
from services.media import MediaProcessor  # noqa: E402

VIDEO_FILE_NAME = "video.mp4"

# Per-call cap on extracted frames. Hard-coded maintainer constant —
# deliberately not a flag.
_MAX_FRAMES_PER_CALL = 20


class FrameToolStage(StrEnum):
    PRE_PASS = "pre_pass"
    CHUNK = "chunk"
    REFINE = "refine"
    GLOSSARY_CHECK = "glossary_check"


def _parse_times(raw: str) -> list[float]:
    times: list[float] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece:
            times.append(float(piece))
    return times


def extra_frame_dir(project_dir: Path, stage: FrameToolStage) -> Path:
    if stage == FrameToolStage.PRE_PASS:
        return project_dir / ".pre_pass" / "media" / "extra_frames"
    if stage == FrameToolStage.CHUNK:
        return project_dir / ".chunks" / "media" / "extra_frames"
    if stage == FrameToolStage.REFINE:
        return project_dir / ".refine" / "extra_frames"
    if stage == FrameToolStage.GLOSSARY_CHECK:
        return project_dir / ".glossary_check" / "extra_frames"
    raise ValueError(f"unsupported frame tool stage: {stage}")


def extract_project_frames(
    *,
    project_dir: Path,
    times: str,
    stage: FrameToolStage,
) -> list[Path]:
    project_dir = project_dir.resolve()
    video = project_dir / VIDEO_FILE_NAME
    if not video.exists():
        raise FileNotFoundError(f"video not found: {video}")

    requested_times = _parse_times(times)
    if not requested_times:
        raise ValueError("--times parsed to no timestamps")

    duration = MediaProcessor.get_media_duration(video)
    max_side = Settings(_env_file=str(_REPO_ROOT / ".env")).video_frame_max_side
    timestamps = sorted({min(max(0.0, t), duration) for t in requested_times})
    timestamps = timestamps[:_MAX_FRAMES_PER_CALL]

    return MediaProcessor.extract_frames_at(
        input_file=video,
        output_dir=extra_frame_dir(project_dir, stage),
        timestamps=timestamps,
        max_side=max_side,
    )


def main_for_stage(stage: FrameToolStage, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract reference frames at specific video timestamps.",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="Project directory containing video.mp4.",
    )
    parser.add_argument(
        "--times", required=True, type=str,
        help='Comma-separated timestamps in seconds, e.g. "62.5,70,77".',
    )
    args = parser.parse_args(argv)

    try:
        paths = extract_project_frames(
            project_dir=args.project_dir,
            times=args.times,
            stage=stage,
        )
    except (FileNotFoundError, ValueError) as e:
        parser.error(str(e))

    if not paths:
        print("No frames could be extracted.", file=sys.stderr)
        return 1

    print(f"Extracted {len(paths)} frame(s):")
    for path in paths:
        print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_for_stage(FrameToolStage.PRE_PASS))
