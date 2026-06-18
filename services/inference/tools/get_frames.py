"""Standalone CLI: extract reference frames at specific video timestamps.

This is the on-demand frame tool the agent backends (gemini-cli / codex /
claude) run during pre-pass, chunk translation, and refine when they need to see
a specific moment of the video (doubtful ASR, an unclear proper noun, an
on-screen text card). It is invoked by ABSOLUTE path so it works from any cwd
(each backend runs in a throwaway workspace) and bootstraps ``sys.path`` so the
project imports resolve regardless of where it is launched. It writes JPEG
frames to a temp directory and prints their absolute paths, one per line, for
the agent to open with its own image-reading tool.

Frame size is the user-configured ``video_frame_max_side`` (read from the repo
``.env``); the agent does not control it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Make project imports resolve no matter the launch cwd: this file lives at
# <repo>/services/inference/tools/get_frames.py, so the repo root is 3 levels up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from settings import Settings  # noqa: E402
from services.media import MediaProcessor  # noqa: E402

# Per-call cap on extracted frames. Hard-coded maintainer constant (mirrors
# INTRO_SKIP_SECONDS in services/translate/assets.py) — deliberately not a flag.
_MAX_FRAMES_PER_CALL = 6


def _parse_times(raw: str) -> list[float]:
    times: list[float] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece:
            times.append(float(piece))
    return times


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract reference frames at specific video timestamps.",
    )
    parser.add_argument(
        "--video", required=True, type=Path,
        help="Absolute path to the source video.",
    )
    parser.add_argument(
        "--times", required=True, type=str,
        help='Comma-separated timestamps in seconds, e.g. "62.5,70,77".',
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output directory (default: a fresh temp dir).",
    )
    args = parser.parse_args(argv)

    video = args.video.resolve()
    if not video.exists():
        parser.error(f"video not found: {video}")

    timestamps = _parse_times(args.times)
    if not timestamps:
        parser.error("--times parsed to no timestamps")

    out_dir = (
        args.out.resolve()
        if args.out is not None
        else Path(tempfile.mkdtemp(prefix="grill_agent_frames_"))
    )
    duration = MediaProcessor.get_media_duration(video)
    # Frame size is the user's setting, read from the repo .env by absolute path
    # (the agent's cwd has no .env), not an agent-tunable flag.
    max_side = Settings(_env_file=str(_REPO_ROOT / ".env")).video_frame_max_side

    # Clamp to the valid media range, dedupe, and cap the count.
    timestamps = sorted({min(max(0.0, t), duration) for t in timestamps})
    timestamps = timestamps[:_MAX_FRAMES_PER_CALL]

    paths = MediaProcessor.extract_frames_at(
        input_file=video,
        output_dir=out_dir,
        timestamps=timestamps,
        max_side=max_side,
    )
    if not paths:
        print("No frames could be extracted.", file=sys.stderr)
        return 1

    print(f"Extracted {len(paths)} frame(s) to {out_dir}:")
    for path in paths:
        print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
